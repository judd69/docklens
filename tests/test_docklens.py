"""DockLens tests: parser, every rule family, scoring, reports, CLI."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from docklens.checks import analyse
from docklens.cli import main
from docklens.parser import (
    parse_copy_args,
    parse_dockerfile,
    parse_env_args,
    parse_from_args,
)
from docklens.report import render_html, render_json, render_text

CLEAN = """\
FROM node:20.11.0-alpine3.19@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY . .
RUN addgroup -S app && adduser -S app -G app
USER app
EXPOSE 3000
HEALTHCHECK CMD node healthcheck.js
CMD ["node", "server.js"]
"""

BAD = open(
    os.path.join(os.path.dirname(__file__), "..", "examples", "bad",
                 "Dockerfile"), encoding="utf-8").read()


def rules(analysis):
    return {f.rule for f in analysis.findings}


def analyse_text(text, dockerignore_present=None):
    return analyse(parse_dockerfile(text), dockerignore_present)


class TestParser(unittest.TestCase):
    def test_continuation_joins_and_keeps_start_line(self):
        df = parse_dockerfile(
            "FROM python:3.12\nRUN apt-get update && \\\n"
            "    apt-get install -y curl\n")
        runs = df.by_keyword("RUN")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].line, 2)
        self.assertIn("apt-get install", runs[0].args)

    def test_comment_lines_ignored(self):
        df = parse_dockerfile("# hi\nFROM node:20\n# bye\nUSER app\n")
        self.assertEqual([i.keyword for i in df.instructions],
                         ["FROM", "USER"])

    def test_from_with_stage_and_platform(self):
        image, name = parse_from_args(
            "--platform=linux/amd64 node:20 AS builder")
        self.assertEqual(image, "node:20")
        self.assertEqual(name, "builder")

    def test_env_both_forms(self):
        self.assertEqual(parse_env_args("FOO bar baz"),
                         [("FOO", "bar baz")])
        self.assertEqual(parse_env_args("A=1 B=two words"),
                         [("A", "1"), ("B", "two")])

    def test_copy_json_form(self):
        flags, srcs, dest = parse_copy_args('["a.txt", "b.txt", "/app/"]')
        self.assertEqual(srcs, ["a.txt", "b.txt"])
        self.assertEqual(dest, "/app/")

    def test_copy_flags(self):
        flags, srcs, dest = parse_copy_args(
            "--from=builder --chown=app:app /dist /app/")
        self.assertIn("--from=builder", flags)
        self.assertEqual(dest, "/app/")


class TestRules(unittest.TestCase):
    def test_clean_dockerfile_scores_100(self):
        a = analyse_text(CLEAN, dockerignore_present=True)
        self.assertEqual(a.findings, [])
        self.assertEqual(a.score, 100)
        self.assertEqual(a.grade, "A")
        self.assertEqual(a.verdict, "CLEAN")

    def test_bad_example_is_fail_grade_f(self):
        a = analyse_text(BAD, dockerignore_present=False)
        self.assertEqual(a.verdict, "FAIL")
        self.assertEqual(a.grade, "F")
        self.assertEqual(a.score, 0)
        for rule in ("DL01", "DL04", "DL05", "DL06", "DL07", "DL08",
                     "DL09", "DL10", "DL11", "DL17", "DL18", "DL20",
                     "DL21", "DL22", "DL23", "DL25"):
            self.assertIn(rule, rules(a), f"missing {rule}")
        self.assertGreaterEqual(a.counts["critical"], 2)

    def test_latest_and_untagged_bases(self):
        a = analyse_text("FROM node:latest\nUSER app\n")
        self.assertIn("DL01", rules(a))
        a = analyse_text("FROM node\nUSER app\n")
        self.assertIn("DL01", rules(a))

    def test_digest_pinned_base_is_silent(self):
        a = analyse_text(
            "FROM node:20@sha256:0123456789abcdef0123456789abcdef"
            "0123456789abcdef0123456789abcdef\nUSER app\n")
        self.assertNotIn("DL01", rules(a))
        self.assertNotIn("DL02", rules(a))

    def test_version_pinned_base_gets_digest_hint_only(self):
        a = analyse_text("FROM node:20\nUSER app\n")
        self.assertIn("DL02", rules(a))
        self.assertNotIn("DL01", rules(a))

    def test_explicit_root_is_critical(self):
        a = analyse_text("FROM node:20\nUSER root\n")
        sev = {f.rule: f.severity for f in a.findings}
        self.assertEqual(sev.get("DL04"), "critical")

    def test_missing_from_is_critical(self):
        a = analyse_text("RUN echo hi\n")
        self.assertIn("DL03", rules(a))
        self.assertEqual(a.verdict, "FAIL")

    def test_env_secret_vs_variable_reference(self):
        a = analyse_text("FROM node:20\nENV API_KEY=abc123\n")
        self.assertIn("DL05", rules(a))
        a = analyse_text("FROM node:20\nENV API_KEY=${API_KEY}\n")
        self.assertNotIn("DL05", rules(a))

    def test_arg_secret_used_in_run(self):
        a = analyse_text(
            "FROM node:20\nARG NPM_TOKEN\nRUN npm install $NPM_TOKEN\n")
        self.assertIn("DL06", rules(a))
        a = analyse_text("FROM node:20\nARG NPM_TOKEN\nRUN echo done\n")
        self.assertNotIn("DL06", rules(a))

    def test_curl_pipe_shell(self):
        a = analyse_text(
            "FROM node:20\nRUN curl -fsSL https://x.sh | bash\n")
        self.assertIn("DL07", rules(a))
        a = analyse_text(
            "FROM node:20\nRUN curl -fsSL https://x.tgz -o x.tgz\n")
        self.assertNotIn("DL07", rules(a))

    def test_add_local_vs_url(self):
        a = analyse_text("FROM node:20\nADD app.tar.gz /app/\n")
        self.assertIn("DL08", rules(a))
        a = analyse_text("FROM node:20\nCOPY app.tar.gz /app/\n")
        self.assertNotIn("DL08", rules(a))

    def test_apt_split_and_cleanup(self):
        a = analyse_text("FROM debian:12\nRUN apt-get update\n")
        self.assertIn("DL09", rules(a))
        a = analyse_text(
            "FROM debian:12\nRUN apt-get update && apt-get install -y curl\n")
        self.assertIn("DL10", rules(a))
        self.assertIn("DL11", rules(a))
        a = analyse_text(
            "FROM debian:12\nRUN apt-get update && apt-get install -y "
            "--no-install-recommends curl && rm -rf /var/lib/apt/lists/*\n")
        self.assertNotIn("DL10", rules(a))
        self.assertNotIn("DL11", rules(a))

    def test_copy_env_file_is_critical(self):
        a = analyse_text("FROM node:20\nCOPY .env /app/.env\n")
        self.assertIn("DL19", rules(a))

    def test_copy_dot_without_dockerignore(self):
        a = analyse_text("FROM node:20\nCOPY . .\n",
                         dockerignore_present=False)
        self.assertIn("DL17", rules(a))
        a = analyse_text("FROM node:20\nCOPY . .\n",
                         dockerignore_present=True)
        self.assertNotIn("DL17", rules(a))
        a = analyse_text("FROM node:20\nCOPY . .\n",
                         dockerignore_present=None)
        self.assertNotIn("DL17", rules(a))

    def test_copy_dot_before_install_busts_cache(self):
        a = analyse_text(
            "FROM node:20\nCOPY . .\nRUN npm ci\n")
        self.assertIn("DL18", rules(a))
        a = analyse_text(
            "FROM node:20\nCOPY package.json ./\nRUN npm ci\nCOPY . .\n")
        self.assertNotIn("DL18", rules(a))

    def test_expose_22_and_shell_cmd(self):
        a = analyse_text(
            "FROM node:20\nEXPOSE 22\nCMD npm start\n")
        self.assertIn("DL20", rules(a))
        self.assertIn("DL21", rules(a))
        a = analyse_text(
            'FROM node:20\nEXPOSE 3000\nCMD ["node", "server.js"]\n')
        self.assertNotIn("DL20", rules(a))
        self.assertNotIn("DL21", rules(a))

    def test_multistage_without_copy_from(self):
        a = analyse_text(
            "FROM node:20 AS builder\nRUN npm run build\n"
            "FROM node:20\nCMD [\"node\", \"server.js\"]\n")
        self.assertIn("DL24", rules(a))
        a = analyse_text(
            "FROM node:20 AS builder\nRUN npm run build\n"
            "FROM node:20\nCOPY --from=builder /app/dist /app/dist\n"
            "CMD [\"node\", \"server.js\"]\n")
        self.assertNotIn("DL24", rules(a))

    def test_scratch_base_is_exempt(self):
        a = analyse_text("FROM scratch\nCOPY app /app\n")
        self.assertNotIn("DL01", rules(a))
        self.assertNotIn("DL02", rules(a))

    def test_scoring_weights(self):
        a = analyse_text("FROM node:20\nMAINTAINER x@y.z\n")
        # DL02 (info, -2) + DL23 (info, -2) + DL04/DL22/DL25 may apply;
        # score must equal 100 minus the weights of what fired.
        weights = {"critical": 15, "warning": 7, "info": 2}
        expected = 100 - sum(weights[f.severity] for f in a.findings)
        self.assertEqual(a.score, expected)


class TestReports(unittest.TestCase):
    def test_text_report_contains_score_and_rules(self):
        a = analyse_text(BAD, dockerignore_present=False)
        out = render_text(a, "Dockerfile", use_color=False)
        self.assertIn("Score 0/100", out)
        self.assertIn("DL05", out)
        self.assertIn("Fix:", out)

    def test_json_report_round_trips(self):
        a = analyse_text(BAD, dockerignore_present=False)
        data = json.loads(render_json(a, "Dockerfile"))
        self.assertEqual(data["verdict"], "FAIL")
        self.assertEqual(data["score"], 0)
        self.assertTrue(all("fix" in f for f in data["findings"]))

    def test_html_report_is_self_contained(self):
        # No external assets: no <script src>, <link>, <img src>, or
        # stylesheet/script URLs. (Fix text may mention http://
        # localhost as prose; that is not an asset reference.)
        a = analyse_text(BAD, dockerignore_present=False)
        out = render_html(a, "Dockerfile", BAD.splitlines())
        self.assertNotIn("src=", out)
        self.assertNotIn("<link", out)
        self.assertNotIn("<img", out)
        self.assertIn("DL05", out)
        self.assertIn('data-filter="critical"', out)

    def test_html_escapes_source(self):
        a = analyse_text("FROM node:20\nRUN echo <b>&\n")
        out = render_html(a, "Dockerfile", ["FROM node:20", "RUN echo <b>&"])
        self.assertIn("&lt;b&gt;&amp;", out)
        self.assertNotIn("<b>&", out)


class TestCli(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_missing_file_exits_2(self):
        code, _ = self._run(["/nonexistent/Dockerfile"])
        self.assertEqual(code, 2)

    def test_fail_on_critical(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Dockerfile")
            with open(p, "w") as fh:
                fh.write(BAD)
            code, out = self._run(["--no-color", "--fail-on", "critical", p])
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)

    def test_fail_on_not_tripped_for_clean(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Dockerfile")
            with open(p, "w") as fh:
                fh.write(CLEAN)
            with open(os.path.join(d, ".dockerignore"), "w") as fh:
                fh.write(".env\n")
            code, out = self._run(["--no-color", "--fail-on", "warning", p])
            self.assertEqual(code, 0)
            self.assertIn("CLEAN", out)

    def test_json_output_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Dockerfile")
            o = os.path.join(d, "report.json")
            with open(p, "w") as fh:
                fh.write(BAD)
            code, _ = self._run(["--format", "json", "--output", o, p])
            self.assertEqual(code, 0)
            with open(o) as fh:
                data = json.load(fh)
            self.assertEqual(data["verdict"], "FAIL")

    def test_default_path_is_dockerfile(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "Dockerfile"), "w") as fh:
                fh.write(CLEAN)
            with open(os.path.join(d, ".dockerignore"), "w") as fh:
                fh.write(".env\n")
            old = os.getcwd()
            try:
                os.chdir(d)
                code, out = self._run(["--no-color"])
            finally:
                os.chdir(old)
            self.assertEqual(code, 0)
            self.assertIn("CLEAN", out)


if __name__ == "__main__":
    unittest.main()
