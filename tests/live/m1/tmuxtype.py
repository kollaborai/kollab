#!/usr/bin/env python3
"""tmuxtype.py <session> [delay]: type stdin into a tmux pane, one character at a time.

The kollab TUI eats pasted or fast input, so each character is its own
`tmux send-keys -l` with a small delay. The text arrives on stdin, so it never
sits on a command line (the join code goes through here). One character per
argv is the most any process listing can ever show.
"""
import subprocess
import sys
import time

session = sys.argv[1]
delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0.06
text = sys.stdin.read().rstrip("\n")
if ";" in text or "\n" in text:
    sys.exit("tmuxtype: ';' and newlines are not typeable (tmux treats ';' as a command separator)")
for ch in text:
    subprocess.run(["tmux", "send-keys", "-t", session, "-l", "--", ch], check=True)
    time.sleep(delay)
print(f"typed {len(text)} characters")
