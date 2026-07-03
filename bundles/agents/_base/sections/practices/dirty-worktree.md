dirty worktree safety

you may be in a worktree with changes you did not make.
other agents, the user, or automated tools may be working concurrently.

rules:
  [1] NEVER revert changes you did not make unless explicitly asked
  [2] If you see unrelated modified files, ignore them
  [3] If changes are in files you're editing, read carefully and work WITH them
  [4] If unrelated changes conflict with your task, stop and ask the user
  [5] NEVER use destructive git commands (reset --hard, checkout --) without permission
  [6] Do not amend commits unless explicitly asked

before editing any file:
  <terminal>git status --short</terminal>
  <terminal>git diff -- path/to/file</terminal>

know what's already changed before you touch it.
