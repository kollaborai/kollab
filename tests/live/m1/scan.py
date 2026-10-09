#!/usr/bin/env python3
"""scan.py: leak and error scanner for kollab screens and logs (both hosts).

  scan.py file <log> <offset>     scan a log from a byte offset. The join code
                                  (may be empty) arrives on stdin.
  scan.py text [--allow-code]     scan a pane capture on stdin. The join code
                                  arrives on fd 3.
  scan.py redact                  pane capture on stdin -> redacted copy on stdout.
                                  The join code arrives on fd 3.
  scan.py findcode                pane capture on stdin -> the join code shown on
                                  it, on stdout. Optional baseline capture on fd 4
                                  (codes already there are ignored).

The join code is only ever read from a pipe, compared, and replaced with
"[join code]". It is never printed by scan/redact and never written to a file.
"""
import os
import re
import sys

ERR = re.compile(r"Traceback|ERROR|Failed executing|(?i:refus(?!e a route's)|denied|cannot(?! start work here)|unknown subcommand|not online|could not be delivered|\[warn\]|warning:)")
# Environment, not the build: server has no `hostname` binary and its old global _base prompt runs it every turn.
ENV_OK = re.compile(r"Failed to execute command 'hostname'")
HEX64 = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{64}(?![0-9A-Za-z])")
RELAY = re.compile(r"relay:\S")
RELAY_FULL = re.compile(r"relay:\S+")
RECEIPT = re.compile(r"(?i)receipt")
# Screens only (a log says ERROR): a failed tool shows "➲ Error: …", an error box "✖ Error",
# and the stuck-loop breaker "Stuck loop detected".
PANE_ERR = re.compile(r"\bError\b|Stuck loop")
SHELL_OK = re.compile(r"Tool execution completed: \[SUCCESS\] terminal:")
CODE_RE = re.compile(r"(?<![0-9A-Za-z-])([0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4})(?![0-9A-Za-z-])")


def read_fd(fd):
    try:
        with os.fdopen(fd, "r", closefd=False) as handle:
            return handle.read().strip()
    except OSError:
        return ""


def variants(code):
    if not code:
        return set()
    bare = code.replace("-", "")
    return {code, code.lower(), bare, bare.lower()}


def redact_line(line, vs):
    for v in vs:
        line = line.replace(v, "[join code]")
    line = CODE_RE.sub("[join code]", line)
    line = HEX64.sub("[hex64]", line)
    return RELAY_FULL.sub("[relay:addr]", line)


def report(text, vs, allow_code, panes):
    lines = text.splitlines()
    hits = []
    env = 0
    for line in lines:
        if ENV_OK.search(line):
            env += 1
        elif ERR.search(line) or (panes and (RECEIPT.search(line) or PANE_ERR.search(line))):
            hits.append(redact_line(line, vs)[:220])
    other_code = bool(CODE_RE.search(text)) and not allow_code
    print(f"code={int((bool(vs) and not allow_code and any(v in text for v in vs)) or other_code)}")
    print(f"hex64={len(HEX64.findall(text))}")
    print(f"relay={len(RELAY.findall(text))}")
    print(f"errhits={len(hits)}")
    print(f"envhits={env}")
    return lines, hits


mode = sys.argv[1]
if mode == "file":
    path, offset = sys.argv[2], int(sys.argv[3])
    vs = variants(sys.stdin.read().strip())
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            text = handle.read().decode("utf-8", "replace")
    except FileNotFoundError:
        print("missing=1")
        sys.exit(0)
    print(f"size={len(text.encode())}")
    lines, hits = report(text, vs, False, panes=False)
    print(f"shell_ok={sum(1 for l in lines if SHELL_OK.search(l))}")
    for h in hits[:25]:
        print("HIT " + h)
elif mode == "text":
    vs = variants(read_fd(3))
    text = sys.stdin.read()
    _, hits = report(text, vs, "--allow-code" in sys.argv, panes=True)
    for h in hits[:15]:
        print("HIT " + h)
elif mode == "redact":
    vs = variants(read_fd(3))
    sys.stdout.write("\n".join(redact_line(l, vs) for l in sys.stdin.read().split("\n")))
elif mode == "findcode":
    baseline = {m.group(1) for m in CODE_RE.finditer(read_fd(4))}
    labelled, other = [], []
    for line in sys.stdin.read().splitlines():
        for m in CODE_RE.finditer(line):
            code = m.group(1)
            if code in baseline or not re.search(r"[A-Z]", code):
                continue
            (labelled if re.search(r"(?i)join code|code", line) else other).append(code)
    pick = (labelled or other)
    sys.stdout.write(pick[-1] if pick else "")
else:
    sys.exit("scan.py: unknown mode")
