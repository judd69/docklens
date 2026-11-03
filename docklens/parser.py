"""Dockerfile parser: instructions, stages, line numbers. Stdlib only.

Handles line continuations (backslash), comment lines, JSON-form
CMD/ENTRYPOINT/COPY, and multi-stage builds (FROM ... AS <name>).
"""

from __future__ import annotations

import re
import shlex


class Instruction:
    def __init__(self, keyword, args, line, raw):
        self.keyword = keyword.upper()
        self.args = args.strip()
        self.line = line  # 1-based line where the instruction starts
        self.raw = raw

    def __repr__(self):
        return f"Instruction({self.keyword} @{self.line}: {self.args!r})"


class Stage:
    def __init__(self, name, base_image, line):
        self.name = name  # None for unnamed stages
        self.base_image = base_image
        self.line = line
        self.instructions = []

    def __repr__(self):
        return f"Stage({self.name or '?'} <- {self.base_image})"


class Dockerfile:
    def __init__(self, instructions, stages, source_lines):
        self.instructions = instructions
        self.stages = stages
        self.source_lines = source_lines

    @property
    def final_stage(self):
        return self.stages[-1] if self.stages else None

    def by_keyword(self, keyword):
        return [i for i in self.instructions if i.keyword == keyword.upper()]


def _logical_lines(text):
    """Yield (start_line, joined_text) with comments stripped and
    continuations joined, following Docker's own preprocessing order:
    comment lines are dropped before continuation handling."""
    raw_lines = text.splitlines()
    kept = []  # (line_no, text)
    for n, line in enumerate(raw_lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        kept.append((n, line))
    out = []
    buf = None
    start = None
    for n, line in kept:
        if buf is None:
            buf, start = line, n
        else:
            buf += line
        if buf.rstrip().endswith("\\"):
            # Remove the backslash and the newline; keep a space so
            # tokens on either side do not merge.
            buf = buf.rstrip()[:-1] + " "
            continue
        out.append((start, buf))
        buf, start = None, None
    if buf is not None:
        out.append((start, buf))
    return out


_FROM_RE = re.compile(
    r"^(?:--platform=\S+\s+)?(\S+)(?:\s+[Aa][Ss]\s+(\S+))?\s*$"
)


def parse_from_args(args):
    m = _FROM_RE.match(args)
    if not m:
        return args, None
    return m.group(1), m.group(2)


def parse_env_args(args):
    """Return list of (key, value) for an ENV instruction, supporting
    both `ENV KEY value` and `ENV KEY=value KEY2=value2` forms."""
    pairs = []
    tokens = args.split(None, 1)
    if tokens and "=" not in tokens[0]:
        if len(tokens) == 2:
            pairs.append((tokens[0], tokens[1]))
        elif len(tokens) == 1:
            pairs.append((tokens[0], ""))
        return pairs
    try:
        parts = shlex.split(args)
    except ValueError:
        parts = args.split()
    for part in parts:
        if "=" in part:
            k, v = part.split("=", 1)
            pairs.append((k, v))
    return pairs


def parse_arg_args(args):
    """Return (name, default_or_None) for an ARG instruction."""
    token = args.split(None, 1)[0] if args.strip() else ""
    if "=" in token:
        name, default = token.split("=", 1)
        return name, default
    return token, None


def parse_copy_args(args):
    """Return (flags, sources, dest) for COPY/ADD. JSON-array form and
    --flag=value flags are handled."""
    args = args.strip()
    if args.startswith("["):
        try:
            import json

            items = json.loads(args)
            if isinstance(items, list) and len(items) >= 2:
                return [], items[:-1], items[-1]
        except ValueError:
            pass
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    flags, rest = [], []
    for tok in tokens:
        if tok.startswith("--"):
            flags.append(tok)
        else:
            rest.append(tok)
    if len(rest) >= 2:
        return flags, rest[:-1], rest[-1]
    if len(rest) == 1:
        return flags, [], rest[0]
    return flags, [], ""


def parse_dockerfile(text):
    instructions = []
    stages = []
    current = None
    for line_no, joined in _logical_lines(text):
        m = re.match(r"^\s*([A-Za-z]+)(?:\s+(.*))?$", joined)
        if not m:
            continue
        keyword, args = m.group(1).upper(), (m.group(2) or "")
        instr = Instruction(keyword, args, line_no, joined.strip())
        instructions.append(instr)
        if keyword == "FROM":
            image, name = parse_from_args(args)
            current = Stage(name, image, line_no)
            stages.append(current)
        elif current is not None:
            current.instructions.append(instr)
        elif stages:
            current = stages[-1]
            current.instructions.append(instr)
    if not stages:
        # No FROM at all: still keep instructions under a synthetic stage
        # so checks can report "missing FROM" plus everything else.
        current = Stage(None, "", 0)
        current.instructions = instructions
        stages.append(current)
    return Dockerfile(instructions, stages, text.splitlines())
