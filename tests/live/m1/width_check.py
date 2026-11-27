#!/usr/bin/env python3
"""width_check.py <columns>: lines that are new and wider than <columns>.

stdin is `<before capture>`, a line `=====SPLIT=====`, then `<after capture>`,
all raw `tmux capture-pane -p -J` text (piped, never written to disk, because a
screen can hold the join code). "New" is a multiset difference of the after
lines against the before lines. Prints counts and the first 60 characters of
each offending line.
"""

import collections
import sys

width = int(sys.argv[1])
before_text, _, after_text = sys.stdin.read().partition("=====SPLIT=====\n")
seen = collections.Counter(line.rstrip() for line in before_text.split("\n"))
new = []
for line in after_text.split("\n"):
    line = line.rstrip()
    if seen[line] > 0:
        seen[line] -= 1
    elif line:
        new.append(line)
wide = [line for line in new if len(line) > width]
print(f"new_lines={len(new)} wide={len(wide)} max={max((len(line) for line in new), default=0)}")
for line in wide[:10]:
    print(f"WIDE({len(line)}) {line[:60]}")
sys.exit(1 if wide else 0)
