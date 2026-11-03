"""DockLens rules: each check returns Findings for a parsed Dockerfile.

Severities: critical (-15 pts), warning (-7), info (-2).
Score starts at 100 and is floored at 0. Grade: A >=90, B >=80,
C >=70, D >=50, F below that. Verdict: FAIL if any critical finding,
NEEDS WORK if any warning, otherwise CLEAN.
"""

from __future__ import annotations

import re

from .parser import parse_arg_args, parse_copy_args, parse_env_args, parse_from_args

SEVERITY_WEIGHTS = {"critical": 15, "warning": 7, "info": 2}

SECRET_KEY_RE = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|API[_-]?KEY|PRIVATE[_-]?KEY|"
    r"AWS_SECRET|ACCESS[_-]?KEY|CREDENTIAL|AUTH)",
    re.IGNORECASE,
)

INSTALL_COMMANDS = ("npm ci", "npm install", "pip install", "pip3 install",
                    "bundle install", "yarn install", "pnpm install",
                    "go mod download", "cargo build", "mvn ", "gradle ")


class Finding:
    def __init__(self, rule, severity, title, line, detail, fix):
        self.rule = rule
        self.severity = severity
        self.title = title
        self.line = line
        self.detail = detail
        self.fix = fix

    def as_dict(self):
        return {
            "rule": self.rule, "severity": self.severity,
            "title": self.title, "line": self.line,
            "detail": self.detail, "fix": self.fix,
        }


def _is_variable_reference(value):
    v = value.strip().strip("\"'")
    return v.startswith("$") or v == ""


def check_unpinned_base(df):
    out = []
    for stage in df.stages:
        image = stage.base_image
        if not image:
            continue
        if image == "scratch":
            continue
        if "@" in image:
            continue  # digest-pinned: the gold standard
        base = image.split("/")[-1]
        if ":" not in base:
            out.append(Finding(
                "DL01", "warning", "Base image has no version tag",
                stage.line,
                f"FROM {image} pulls whatever :latest happens to be today.",
                "Pin a version, e.g. FROM node:20.11.0-alpine3.19, "
                "or pin by digest with @sha256:...."))
        elif base.endswith(":latest"):
            out.append(Finding(
                "DL01", "warning", "Base image pinned to :latest",
                stage.line,
                f"FROM {image} is not reproducible: the next pull may differ.",
                "Pin a specific version, or pin by digest with @sha256:...."))
        else:
            out.append(Finding(
                "DL02", "info", "Base image not pinned by digest",
                stage.line,
                f"FROM {image} is version-pinned, but tags are mutable.",
                "For fully reproducible builds, pin by digest: "
                "FROM image:tag@sha256:...."))
    return out


def check_missing_from(df):
    if not df.by_keyword("FROM"):
        return [Finding(
            "DL03", "critical", "No FROM instruction",
            1, "A Dockerfile without FROM cannot build an image.",
            "Start the file with FROM <image>:<tag>.")]
    return []


def check_user(df):
    out = []
    final = df.final_stage
    if final is None:
        return out
    users = [i for i in final.instructions if i.keyword == "USER"]
    if not users:
        out.append(Finding(
            "DL04", "warning", "Final stage runs as root",
            final.line,
            "No USER is set in the final stage, so the container "
            "process runs as UID 0.",
            "Add a non-root user, e.g. RUN adduser -S app && USER app "
            "(prefer a numeric UID for Kubernetes runAsNonRoot)."))
    else:
        last = users[-1]
        if last.args.strip() in ("root", "0", "0:0"):
            out.append(Finding(
                "DL04", "critical", "Container explicitly runs as root",
                last.line,
                f"USER {last.args.strip()} gives the process full "
                "privileges inside the container.",
                "Switch to a non-root user, e.g. USER 10001."))
    return out


def check_env_secrets(df):
    out = []
    for instr in df.by_keyword("ENV"):
        for key, value in parse_env_args(instr.args):
            if SECRET_KEY_RE.search(key) and not _is_variable_reference(value):
                out.append(Finding(
                    "DL05", "critical", "Secret baked into an ENV layer",
                    instr.line,
                    f"ENV {key}=... stores the value in the image layer "
                    "forever, even if a later layer deletes it.",
                    "Inject secrets at runtime (orchestrator secrets, "
                    "secret manager) or use a BuildKit secret mount for "
                    "build-time needs; never ENV them."))
    return out


def check_arg_in_run(df):
    out = []
    secret_args = set()
    for instr in df.by_keyword("ARG"):
        name, _ = parse_arg_args(instr.args)
        if name and SECRET_KEY_RE.search(name):
            secret_args.add(name)
    if not secret_args:
        return out
    for instr in df.by_keyword("RUN"):
        for name in secret_args:
            if re.search(r"\$" + re.escape(name) + r"\b", instr.args) \
                    or ("${" + name) in instr.args:
                out.append(Finding(
                    "DL06", "warning", "Secret ARG value leaks into a layer",
                    instr.line,
                    f"ARG {name} is used in a RUN step, so its value is "
                    "recorded in the image history (docker history).",
                    "Use a BuildKit secret mount instead: "
                    "RUN --mount=type=secret,id=" + name.lower() + " ..."))
    return out


def check_curl_pipe_shell(df):
    out = []
    for instr in df.by_keyword("RUN"):
        if re.search(r"\b(curl|wget)\b[^|]*\|\s*(sh|bash|zsh)\b", instr.args):
            out.append(Finding(
                "DL07", "critical", "Download piped straight into a shell",
                instr.line,
                "curl|sh runs unverified code from the network.",
                "Download the file, verify its checksum/signature, "
                "then execute it."))
    return out


def check_add_vs_copy(df):
    out = []
    for instr in df.by_keyword("ADD"):
        _, sources, _ = parse_copy_args(instr.args)
        for src in sources:
            if src.startswith(("http://", "https://")):
                out.append(Finding(
                    "DL08", "warning", "ADD fetches a remote URL",
                    instr.line,
                    f"ADD {src} downloads at build time with no "
                    "checksum verification.",
                    "Use curl/wget with a checksum check, then COPY."))
                break
        else:
            out.append(Finding(
                "DL08", "warning", "ADD used where COPY would do",
                instr.line,
                "ADD silently unpacks archives and fetches URLs; "
                "COPY is explicit and predictable.",
                "Use COPY unless you specifically need ADD's "
                "tar auto-extraction."))
    return out


def _apt_flags(run_args):
    return run_args


def check_apt_hygiene(df):
    out = []
    for instr in df.by_keyword("RUN"):
        args = instr.args
        if "apt-get" not in args:
            continue
        has_update = "apt-get update" in args
        has_install = "apt-get install" in args
        if has_update and not has_install:
            out.append(Finding(
                "DL09", "warning",
                "apt-get update cached in its own layer",
                instr.line,
                "A lone apt-get update layer goes stale while later "
                "installs reuse the cached package lists.",
                "Combine update and install in one RUN: "
                "apt-get update && apt-get install -y ..."))
        if has_install:
            if "--no-install-recommends" not in args:
                out.append(Finding(
                    "DL10", "warning",
                    "apt-get install pulls recommended packages",
                    instr.line,
                    "Recommended packages bloat the image with "
                    "libraries you never asked for.",
                    "Add --no-install-recommends to apt-get install."))
            if "rm -rf /var/lib/apt/lists" not in args and \
                    "/var/lib/apt/lists" not in args:
                out.append(Finding(
                    "DL11", "warning",
                    "apt lists left in the image layer",
                    instr.line,
                    "Package lists downloaded by apt-get update stay "
                    "in the layer unless removed in the same RUN.",
                    "End the RUN with: rm -rf /var/lib/apt/lists/*"))
    return out


def check_apk_pip_npm_hygiene(df):
    out = []
    for instr in df.by_keyword("RUN"):
        args = instr.args
        if re.search(r"\bapk add\b", args) and "--no-cache" not in args:
            out.append(Finding(
                "DL12", "warning", "apk add without --no-cache",
                instr.line,
                "apk's package index is cached into the layer.",
                "Use apk add --no-cache ..."))
        if re.search(r"\bpip(3)? install\b", args) and \
                "--no-cache-dir" not in args:
            out.append(Finding(
                "DL13", "info", "pip install without --no-cache-dir",
                instr.line,
                "pip's download cache is kept in the layer.",
                "Add --no-cache-dir to pip install."))
        if re.search(r"\bnpm install\b", args) and "npm ci" not in args:
            out.append(Finding(
                "DL14", "info", "npm install instead of npm ci",
                instr.line,
                "npm install can resolve different versions than "
                "the lockfile; npm ci is reproducible.",
                "Use npm ci when a lockfile exists."))
        if re.search(r"\bsudo\b", args):
            out.append(Finding(
                "DL15", "warning", "sudo used inside a build step",
                instr.line,
                "Build steps already run as root; sudo adds "
                "surprise and a needless dependency.",
                "Drop sudo from RUN commands."))
        if re.search(r"chmod\s+777\b", args) or re.search(r"chmod\s+-R\s+777\b", args):
            out.append(Finding(
                "DL16", "warning", "chmod 777 widens file permissions",
                instr.line,
                "World-writable files in the image are an easy "
                "privilege-escalation foothold.",
                "Use the narrowest permissions the app needs, "
                "e.g. chmod 755 or ownership via --chown."))
    return out


def check_copy_cache_and_env(df, dockerignore_present=None):
    out = []
    copy_instrs = df.by_keyword("COPY")
    run_instrs = df.by_keyword("RUN")
    first_install_line = None
    for instr in run_instrs:
        if any(cmd in instr.args for cmd in INSTALL_COMMANDS):
            first_install_line = instr.line
            break
    for instr in copy_instrs:
        flags, sources, dest = parse_copy_args(instr.args)
        if sources == ["."] or sources == ["./"]:
            if dockerignore_present is False:
                out.append(Finding(
                    "DL17", "warning",
                    "COPY . . with no .dockerignore",
                    instr.line,
                    "Without a .dockerignore, COPY . . can ship "
                    ".env files, .git, and local secrets into "
                    "the image.",
                    "Add a .dockerignore excluding .env, .git, "
                    "node_modules, and other local-only files."))
            if first_install_line is not None and \
                    instr.line < first_install_line:
                # COPY . before dependency install means every code
                # change invalidates the dependency layer.
                out.append(Finding(
                    "DL18", "warning",
                    "COPY . . before dependency install busts the cache",
                    instr.line,
                    "Copying the whole context before installing "
                    "dependencies invalidates the install layer on "
                    "every code change.",
                    "COPY only lockfiles first, install, then "
                    "COPY the rest of the source."))
        for src in sources:
            if src in (".env",) or src.endswith("/.env") or \
                    src.endswith(".env"):
                out.append(Finding(
                    "DL19", "critical", "A .env file is copied into the image",
                    instr.line,
                    f"COPY {src} bakes local secrets into the image.",
                    "Never COPY .env; inject environment at runtime."))
    return out


def check_expose(df):
    out = []
    for instr in df.by_keyword("EXPOSE"):
        for port in instr.args.split():
            port = port.split("/")[0]
            if port == "22":
                out.append(Finding(
                    "DL20", "warning", "SSH port 22 exposed",
                    instr.line,
                    "Containers almost never need SSH; exposing "
                    "port 22 widens the attack surface.",
                    "Remove EXPOSE 22; use docker exec for debugging."))
    return out


def check_shell_form(df):
    out = []
    for instr in df.by_keyword("CMD") + df.by_keyword("ENTRYPOINT"):
        args = instr.args.strip()
        if args and not args.startswith("["):
            out.append(Finding(
                "DL21", "warning",
                f"Shell-form {instr.keyword} swallows signals",
                instr.line,
                f"{instr.keyword} {args} runs via /bin/sh -c, so "
                "SIGTERM may never reach your app and shutdowns hang.",
                f"Use exec form: {instr.keyword} "
                f"[\"executable\", \"arg1\"]"))
    return out


def check_healthcheck(df):
    if not df.by_keyword("HEALTHCHECK"):
        line = df.final_stage.line if df.final_stage else 1
        return [Finding(
            "DL22", "info", "No HEALTHCHECK defined",
            line,
            "Orchestrators cannot tell a wedged container from a "
            "healthy one without a health check.",
            "Add a HEALTHCHECK that hits your app's health endpoint "
            "and exits 1 when it fails.")]
    return []


def check_maintainer(df):
    out = []
    for instr in df.by_keyword("MAINTAINER"):
        out.append(Finding(
            "DL23", "info", "MAINTAINER is deprecated",
            instr.line,
            "MAINTAINER was deprecated in favour of labels.",
            "Use LABEL maintainer=\"name <email>\" instead."))
    return out


def check_multistage(df):
    out = []
    if len(df.stages) < 2:
        return out
    final = df.final_stage
    copies_from = []
    for instr in final.instructions:
        if instr.keyword == "COPY":
            flags, _, _ = parse_copy_args(instr.args)
            copies_from += [f for f in flags if f.startswith("--from=")]
    if not copies_from:
        out.append(Finding(
            "DL24", "info",
            "Multi-stage build, but final stage copies nothing",
            final.line,
            "Earlier stages build artifacts, but the final stage "
            "never COPY --from= them, so the build output is lost.",
            "Copy the built artifact: "
            "COPY --from=builder /app/dist /app/dist"))
    return out


def check_workdir(df):
    out = []
    final = df.final_stage
    if final is None:
        return out
    if not any(i.keyword == "WORKDIR" for i in final.instructions):
        out.append(Finding(
            "DL25", "info", "No WORKDIR set in the final stage",
            final.line,
            "Relative COPY destinations and CMD paths resolve "
            "against /, which is easy to get wrong.",
            "Set WORKDIR /app early in the final stage."))
    return out


ALL_CHECKS = [
    check_missing_from,
    check_unpinned_base,
    check_user,
    check_env_secrets,
    check_arg_in_run,
    check_curl_pipe_shell,
    check_add_vs_copy,
    check_apt_hygiene,
    check_apk_pip_npm_hygiene,
    check_expose,
    check_shell_form,
    check_healthcheck,
    check_maintainer,
    check_multistage,
    check_workdir,
]


class Analysis:
    def __init__(self, findings, stages):
        self.findings = sorted(findings, key=lambda f: (f.line, f.rule))
        self.stages = stages
        self.score = max(
            0, 100 - sum(SEVERITY_WEIGHTS[f.severity] for f in self.findings))
        if self.score >= 90:
            self.grade = "A"
        elif self.score >= 80:
            self.grade = "B"
        elif self.score >= 70:
            self.grade = "C"
        elif self.score >= 50:
            self.grade = "D"
        else:
            self.grade = "F"
        if any(f.severity == "critical" for f in self.findings):
            self.verdict = "FAIL"
        elif any(f.severity == "warning" for f in self.findings):
            self.verdict = "NEEDS WORK"
        else:
            self.verdict = "CLEAN"
        self.counts = {sev: sum(1 for f in self.findings
                                if f.severity == sev)
                       for sev in ("critical", "warning", "info")}

    def as_dict(self):
        return {
            "score": self.score, "grade": self.grade,
            "verdict": self.verdict, "counts": self.counts,
            "stages": [{"name": s.name, "base_image": s.base_image}
                       for s in self.stages],
            "findings": [f.as_dict() for f in self.findings],
        }


def analyse(df, dockerignore_present=None):
    findings = []
    for check in ALL_CHECKS:
        findings.extend(check(df))
    findings.extend(check_copy_cache_and_env(df, dockerignore_present))
    return Analysis(findings, df.stages)
