#!/usr/bin/env python3
"""tmuxtype.py <session> [delay]: type stdin into a tmux pane, one character at a time.

The kollab TUI eats pasted or fast input, so each character is its own
`tmux send-keys -l` with a small delay. The text arrives on stdin, so it never
sits on a command line (the join code goes through here). One character per
argv is the most any process listing can ever show.

A keystroke can still vanish. A slash command is therefore checked on the
input line (the one with the prompt) before the caller presses Enter: if the
command is not there, the line is cleared and the command typed again, up to
three times. Anything else, a join code in a masked field for one, is not
checked, and nothing typed is ever printed.
"""
import subprocess
import sys
import time

PROMPT = "❯"  # the input box prompt
CURSOR = "█"  # the block cursor drawn after the input

session = sys.argv[1]
delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0.06
text = sys.stdin.read().rstrip("\n")
if ";" in text or "\n" in text:
    sys.exit("tmuxtype: ';' and newlines are not typeable (tmux treats ';' as a command separator)")


def send(*keys):
    subprocess.run(["tmux", "send-keys", "-t", session, *keys], check=True)


def input_line():
    screen = subprocess.run(
        ["tmux", "capture-pane", "-p", "-J", "-t", session], capture_output=True, text=True
    ).stdout
    lines = [line for line in screen.splitlines() if PROMPT in line]
    if not lines:
        return None
    return lines[-1].split(PROMPT, 1)[1].replace(CURSOR, "").strip()


attempts = 1
for attempt in range(1, 4):
    attempts = attempt
    for ch in text:
        send("-l", "--", ch)
        time.sleep(delay)
    if not text.startswith("/"):
        break
    time.sleep(0.4)
    got = input_line()
    if got is None or text in got:
        break
    for _ in range(len(got) + 2):  # clear what did arrive, then type it all again
        send("BSpace")
        time.sleep(0.02)
    time.sleep(0.3)
print(f"typed {len(text)} characters in {attempts} attempt(s)")
