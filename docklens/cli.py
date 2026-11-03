"""DockLens command line interface.

Exit codes: 0 = pass, 1 = --fail-on threshold tripped, 2 = usage/IO error.
"""

from __future__ import annotations

import argparse
import os
import sys

from .checks import analyse
from .parser import parse_dockerfile
from .report import render_html, render_json, render_text

VERSION = "1.0.0"

SEVERITY_RANK = {"info": 1, "warning": 2, "critical": 3}


def _fail_on_tripped(analysis, fail_on):
    if fail_on is None:
        return False
    threshold = SEVERITY_RANK[fail_on]
    return any(SEVERITY_RANK[f.severity] >= threshold
               for f in analysis.findings)


def build_parser():
    p = argparse.ArgumentParser(
        prog="docklens",
        description="X-ray a Dockerfile: security, efficiency and "
                    "reliability findings with a 0-100 score.")
    p.add_argument("dockerfile", nargs="?", default="Dockerfile",
                   help="path to the Dockerfile (default: ./Dockerfile)")
    p.add_argument("--context", default=None,
                   help="build-context directory; used to check for a "
                        ".dockerignore next to the Dockerfile")
    p.add_argument("--format", choices=["text", "json", "html"],
                   default="text", help="output format (default: text)")
    p.add_argument("--output", "-o", default=None,
                   help="write the report to this file instead of stdout")
    p.add_argument("--fail-on", choices=["critical", "warning", "info"],
                   default=None,
                   help="exit 1 if any finding at or above this severity "
                        "is present (CI gate)")
    p.add_argument("--no-color", action="store_true",
                   help="disable coloured text output")
    p.add_argument("--version", action="version",
                   version=f"docklens {VERSION}")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    path = args.dockerfile
    if not os.path.isfile(path):
        print(f"docklens: no such file: {path}", file=sys.stderr)
        return 2
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        print(f"docklens: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    dockerignore_present = None
    context = args.context or os.path.dirname(os.path.abspath(path))
    dockerignore = os.path.join(context, ".dockerignore")
    if os.path.isdir(context):
        dockerignore_present = os.path.isfile(dockerignore)
    analysis = analyse(parse_dockerfile(text), dockerignore_present)
    if args.format == "json":
        out = render_json(analysis, path)
    elif args.format == "html":
        out = render_html(analysis, path, text.splitlines())
    else:
        # Colour only when writing to a terminal; plain text otherwise.
        out = render_text(analysis, path,
                          use_color=(not args.no_color)
                          and args.output is None
                          and sys.stdout.isatty())
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(out)
        except OSError as exc:
            print(f"docklens: cannot write {args.output}: {exc}",
                  file=sys.stderr)
            return 2
    else:
        sys.stdout.write(out)
    return 1 if _fail_on_tripped(analysis, args.fail_on) else 0


if __name__ == "__main__":
    raise SystemExit(main())
