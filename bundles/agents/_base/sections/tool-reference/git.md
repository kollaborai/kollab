git workflow & version control

before making changes:
  <terminal>git status</terminal>
  <terminal>git diff</terminal>

know what's already modified, avoid conflicts

after making changes:
  <terminal>git status</terminal>
  <terminal>git diff</terminal>
  <terminal>git add -A</terminal>
  <terminal>git commit -m "descriptive message"</terminal>

commit message rules:
  [ok] be specific: "add user authentication" not "update code"
  [ok] use imperative: "fix bug" not "fixed bug"
  [ok] explain why if not obvious
  [ok] reference issues: "fixes #123"

good commits:
  "add password hashing to user registration"
  "fix race condition in plugin loader"
  "refactor config system for better testability"
  "update dependencies to resolve security vulnerability"

bad commits:
  "changes"
  "update"
  "fix stuff"
  "wip"

branching strategy:

when working on features:
  <terminal>git checkout -b feature/descriptive-name</terminal>
  make changes...
  <terminal>git add -A && git commit -m "clear message"</terminal>
  <terminal>git checkout main</terminal>
  <terminal>git merge feature/descriptive-name</terminal>

checking history:
  <terminal>git log --oneline -10</terminal>
  <terminal>git log --grep="keyword"</terminal>
  <terminal>git show commit_hash</terminal>

undoing mistakes:
  <terminal>git checkout -- filename</terminal>
  <terminal>git reset HEAD~1</terminal>
  <terminal>git reset --hard HEAD~1</terminal>

before dangerous operations:
  <terminal>git branch backup-$(date +%s)</terminal>
  then proceed with risky operation
