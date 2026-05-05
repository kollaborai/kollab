---
name: git-workflow
description: "Git Workflow skill - professional version control practices"
---

git workflow mode: CLEAN HISTORY, CLEAR INTENT

when this skill is active, you follow disciplined git practices.
this is a comprehensive guide to professional version control.


PHASE 0: ENVIRONMENT VERIFICATION

before doing ANY git operations, verify your configuration.


verify git is installed

  <terminal>git --version</terminal>

if git not installed:
  macOS:
    <terminal>brew install git</terminal>

  linux:
    <terminal>sudo apt install git</terminal>
    <terminal>sudo yum install git</terminal>

  windows:
    <terminal>winget install Git.Git</terminal>


verify git identity

  <terminal>git config --global user.name</terminal>

  <terminal>git config --global user.email</terminal>

if not configured:

  <terminal>git config --global user.name "Your Name"</terminal>

  <terminal>git config --global user.email "you@example.com"</terminal>

critical: use your real email.
your identity is part of the permanent record.


verify default branch

  <terminal>git config --global init.defaultBranch</terminal>

if not set:

  <terminal>git config --global init.defaultBranch main</terminal>

modern standard: main, not master.


verify useful defaults

  <terminal>git config --global core.autocrlf</terminal>

  macOS/Linux:
    <terminal>git config --global core.autocrlf input</terminal>

  windows:
    <terminal>git config --global core.autocrlf true</terminal>


enable helpful options

  <terminal>git config --global core.quotePath false</terminal>

  <terminal>git config --global init.defaultBranch main</terminal>

  <terminal>git config --global rebase.autoStash true</terminal>

  <terminal>git config --global push.autoSetupRemote true</terminal>


verify remote access

  <terminal>git remote -v</terminal>

if no remote:
  [warn] working without backup
  [warn] push to remote frequently

if remote exists, verify access:
  <terminal>git ls-remote origin</terminal>


verify current repository state

  <terminal>git status</terminal>

  <terminal>git branch --show-current</terminal>

  <terminal>git log --oneline -5</terminal>

understand where you are before making changes.


PHASE 1: GIT FUNDAMENTALS


the three states

  working directory -> staging area -> repository

  working directory: your actual files
  staging area: files prepared for commit
  repository: committed history

  <terminal>git status</terminal>

  shows: which files are in which state


the basic commands

  add: move file from working to staging
  <terminal>git add filename.py</terminal>

  commit: move from staging to repository
  <terminal>git commit -m "message"</terminal>

  status: see what state files are in
  <terminal>git status</terminal>

  diff: see what changed
  <terminal>git diff</terminal>          # working vs staging
  <terminal>git diff --staged</terminal> # staging vs repository


reading git status

  $ git status
  On branch main
  Your branch is up to date with 'origin/main'.

  Changes not staged for commit:
    modified:   app.py
    modified:   utils.py

  Untracked files:
    new_file.py

  breakdown:
    - on branch main: current branch
    - up to date: local matches origin
    - modified: changed but not staged
    - untracked: new file, not tracked


PHASE 2: BRANCHING STRATEGY


why branch

branches enable:
  - parallel work
  - isolation of changes
  - safe experimentation
  - code review before merge


branch naming conventions

  feature work:
    feature/add-user-authentication
    feature/payment-processing
    feat/shopping-cart

  bug fixes:
    bugfix/login-timeout
    fix/memory-leak
    hotfix/security-patch

  chores:
    refactor/user-module
    chore/update-dependencies
    docs/api-endpoints
    test/add-user-tests

  release:
    release/v1.2.0
    prepare-release/v1.0.0


create a branch

  <terminal>git checkout -b feature/add-user-auth</terminal>

equivalent:
  <terminal>git branch feature/add-user-auth</terminal>
  <terminal>git checkout feature/add-user-auth</terminal>


list branches

  <terminal>git branch</terminal>

  <terminal>git branch -a</terminal>      # show remote too
  <terminal>git branch -vv</terminal>     # show tracking info


switch branches

  <terminal>git checkout main</terminal>

  <terminal>git switch feature/add-user-auth</terminal>

note: git switch is newer, more intuitive


delete branches

  local:
    <terminal>git branch -d feature/complete</terminal>

  force delete (unmerged):
    <terminal>git branch -D feature/experimental</terminal>

  remote:
    <terminal>git push origin --delete feature/complete</terminal>


PHASE 3: THE COMMIT HABIT


what makes a good commit

  [ok] small, focused change
  [ok] complete (works, tests pass)
  [ok] clear message explaining why
  [x] large, sweeping changes
  [x] broken code (WIP commits)
  [x] vague messages ("update", "fix stuff")
  [x] multiple unrelated changes


commit message format

  subject line (50 chars or less):
    - imperative mood ("add" not "added" or "adds")
    - complete sentence
    - no trailing period

  body (optional):
    - what and why
    - not how
    - wrapped at 72 chars

  examples:

    add user authentication with JWT tokens

    implement login and registration endpoints with JWT-based
    authentication. passwords are hashed with bcrypt.

    fixes #123

    refactor: extract user validation to separate module

    the validation logic was duplicated across multiple controllers.
    extracting to a shared module reduces duplication and makes
    testing easier.

    fix: prevent null pointer in user lookup

    when a user id doesn't exist, the lookup was returning None
    without handling, causing a null pointer error. added explicit
    check and raise 404 instead.


commit often

  after each logical unit:
    - one function extracted
    - one test written
    - one bug fixed

  benefits:
    - easier to revert if needed
    - clearer history
    - smaller review chunks

  anti-pattern:
    [x] one giant commit at end of day
    [x] "work in progress" commits that break tests


amend commits

  only for most recent commit:
  <terminal>git commit --amend</terminal>

  only for trivial fixes (typos, forgot to add file)
  never amend pushed commits (rewrites history)

  add file to previous commit:
    <terminal>git add forgotten_file.py</terminal>
    <terminal>git commit --amend --no-edit</terminal>


PHASE 4: STAGING AND COMMITTING


interactive staging

  stage parts of a file:
  <terminal>git add -p filename.py</terminal>

  prompts for each hunk:
    - y: stage this hunk
    - n: don't stage this hunk
    - s: split into smaller hunks
    - q: quit

  useful when:
    - file has multiple unrelated changes
    - want atomic commits


stage by file

  stage specific files:
  <terminal>git add app.py utils.py</terminal>

  stage all changes:
  <terminal>git add .</terminal>

  careful: stages everything
  review first with:
  <terminal>git status</terminal>


unstage files

  <terminal>git restore --staged filename.py</terminal>

  or:
  <terminal>git reset HEAD filename.py</terminal>

  moves from staging back to working directory


discard working changes

  <terminal>git restore filename.py</terminal>

  or:
  <terminal>git checkout -- filename.py</terminal>

  warning: destroys changes
  be certain you don't need them


PHASE 5: MERGE VS REBASE


when to merge

  use merge when:
  - preserving complete history
  - working on shared branch
  - want explicit merge commit

  <terminal>git checkout main</terminal>
  <terminal>git merge feature/new-auth</terminal>

  creates merge commit combining histories.


when to rebase

  use rebase when:
  - cleaning up local branch before push
  - linear history preferred
  - integrating upstream changes

  <terminal>git checkout feature/new-auth</terminal>
  <terminal>git rebase main</terminal>

  reapplies your commits on top of main.


golden rules

  [1] never rebase pushed commits
      rebase rewrites history
      others may have based work on those commits
      rebase = trouble for collaborators

  [2] never rebase shared branches
      main, develop, release branches
      only rebase your local feature branches

  [3] force push with caution
      only after rebase
      only to your own branches
      <terminal>git push --force-with-lease</terminal>


PHASE 6: HANDLING MERGE CONFLICTS


conflict markers

  when git can't auto-merge:

  <<<<<<< HEAD
  current branch content
  =======
  incoming branch content
  >>>>>>> feature-branch

  you decide which to keep, or combine both.


resolution process

  [1] identify conflict
  <terminal>git status</terminal>

  shows "both modified" files

  [2] open file in editor
  find conflict markers
  understand what each side does

  [3] resolve conflict
  choose:
    - keep HEAD (current)
    - keep incoming
    - combine both
    - write new solution

  [4] remove markers
  delete <<<<<<<, =======, >>>>>>>

  [5] stage resolution
  <terminal>git add resolved_file.py</terminal>

  [6] complete merge/rebase
  <terminal>git commit</terminal>        # for merge
  <terminal>git rebase --continue</terminal>  # for rebase


conflict resolution tools

  use merge tool:
  <terminal>git mergetool</terminal>

  configure:
  <terminal>git config --global merge.tool vscode</terminal>
  <terminal>git config --global mergetool.vscode.cmd 'code --wait $MERGED'</terminal>


abort on trouble

  abort merge:
  <terminal>git merge --abort</terminal>

  abort rebase:
  <terminal>git rebase --abort</terminal>

  returns to state before operation.


PHASE 7: SYNCING WITH REMOTE


fetch vs pull

  fetch: get remote changes, don't merge
  <terminal>git fetch origin</terminal>

  safe, lets you review before integrating

  pull: fetch and merge in one step
  <terminal>git pull</terminal>

  convenient but creates merge commit

  pull with rebase:
  <terminal>git pull --rebase</terminal>

  cleaner history, reapplies your work on top


push branches

  first push (set upstream):
  <terminal>git push -u origin feature/new-auth</terminal>

  subsequent pushes:
  <terminal>git push</terminal>

  with autoSetupRemote configured:
  <terminal>git push</terminal>  # sets upstream automatically


update local branch

  when remote has updates:

  <terminal>git fetch origin</terminal>
  <terminal>git rebase origin/main</terminal>

  or:
  <terminal>git pull --rebase</terminal>


before starting work

  always start from latest:
  <terminal>git checkout main</terminal>
  <terminal>git pull</terminal>
  <terminal>git checkout -b feature/new-work</terminal>

  prevents merge conflicts later


PHASE 8: INSPECTING HISTORY


view commits

  recent commits:
  <terminal>git log</terminal>

  one line per commit:
  <terminal>git log --oneline</terminal>

  with graph:
  <terminal>git log --oneline --graph --all</terminal>

  pretty format:
  <terminal>git log --pretty=format:"%h %ad | %s" --date=short</terminal>


view specific commit

  show commit details:
  <terminal>git show abc123</terminal>

  show file at commit:
  <terminal>git show abc123:path/to/file.py</terminal>


view changes

  diff between commits:
  <terminal>git diff abc123 def456</terminal>

  diff between branches:
  <terminal>git diff main feature</terminal>

  what changed in file:
  <terminal>git log -p filename.py</terminal>


blame: who changed what

  <terminal>git blame filename.py</terminal>

  shows each line with commit and author.

  specific line range:
  <terminal>git blame -L 50,60 filename.py</terminal>


PHASE 9: UNDOING CHANGES


undo working directory changes

  discard all changes:
  <terminal>git restore .</terminal>

  discard specific file:
  <terminal>git restore filename.py</terminal>

  warning: cannot be undone


undo staged changes

  unstage file:
  <terminal>git restore --staged filename.py</terminal>

  unstage all:
  <terminal>git restore --staged .</terminal>


undo commits (not pushed)

  remove last commit, keep changes:
  <terminal>git reset --soft HEAD~1</terminal>

  remove last commit, discard changes:
  <terminal>git reset --hard HEAD~1</terminal>

  go back to specific commit:
  <terminal>git reset --hard abc123</terminal>


undo pushed commits

  create reversal commit:
  <terminal>git revert abc123</terminal>

  reverts changes in new commit.
  safe for shared history.


PHASE 10: STASHING


when to stash

  temporarily save work when:
  - need to switch branches
  - want to pull latest changes
  - need to test something else

  stash is a stack of temporary commits


basic stash

  <terminal>git stash</terminal>

  saves working directory and index.
  returns to clean state.


view stashes

  <terminal>git stash list</terminal>

  stash@{0}: On main: add user login
  stash@{1}: On feature: WIP on authentication


apply stash

  <terminal>git stash apply</terminal>

  applies most recent stash, keeps it in list.

  apply specific stash:
  <terminal>git stash apply stash@{1}</terminal>


drop stash

  <terminal>git stash drop</terminal>

  or combine:
  <terminal>git stash pop</terminal>

  applies and removes most recent stash.


stash with message

  <terminal>git stash save "work on user auth"</terminal>

  helpful for identifying stashes later


stash specific files

  <terminal>git stash push -m "config changes" config.json</terminal>

  only stashes specified file


PHASE 11: TAGGING


when to tag

  mark releases:
  - v1.0.0
  - v1.1.0
  - v2.0.0

  tag production deployments
  tag significant milestones


create tags

  annotated (recommended):
  <terminal>git tag -a v1.0.0 -m "Release version 1.0.0"</terminal>

  lightweight:
  <terminal>git tag v1.0.0</terminal>

  annotated tags have message, date, author.


view tags

  list all tags:
  <terminal>git tag</terminal>

  show tag details:
  <terminal>git show v1.0.0</terminal>


push tags

  push specific tag:
  <terminal>git push origin v1.0.0</terminal>

  push all tags:
  <terminal>git push origin --tags</terminal>


delete tags

  local:
  <terminal>git tag -d v1.0.0</terminal>

  remote:
  <terminal>git push origin --delete v1.0.0</terminal>


checkout tags

  view code at tag:
  <terminal>git checkout v1.0.0</terminal>

  creates detached HEAD state.
  to work, create a branch:
  <terminal>git checkout -b patch-1.0.1 v1.0.0</terminal>


PHASE 12: GITIGNORE


what to ignore

  never commit:
  - dependencies/ (node_modules, venv, __pycache__)
  - build artifacts (.pyc, .o, .dll)
  - environment files (.env, .env.local)
  - ide settings (.idea/, .vscode/)
  - os files (.DS_Store, Thumbs.db)
  - logs (*.log)
  - temporary files (*.tmp, *.swp)


create .gitignore

  <terminal>cat > .gitignore << 'EOF'
  # python
  __pycache__/
  *.py[cod]
  *$py.class
  .venv/
  venv/
  *.egg-info/

  # environment
  .env
  .env.local
  .env.*.local

  # ide
  .idea/
  .vscode/
  *.swp
  *.swo

  # os
  .DS_Store
  Thumbs.db

  # logs
  *.log
  logs/

  # testing
  .coverage
  htmlcov/
  .pytest_cache/

  # build
  dist/
  build/
  *.egg
  EOF</terminal>


existing .gitignore not working

  files already tracked must be removed:
  <terminal>git rm --cached filename</terminal>

  for directory:
  <terminal>git rm -r --cached directory/</terminal>

  then commit.


ignore tracked file locally

  <terminal>git update-index --assume-unchanged config.local.json</terminal>

  keeps file locally, ignores for commits.

  to undo:
  <terminal>git update-index --no-assume-unchanged config.local.json</terminal>


PHASE 13: COMMON WORKFLOWS


feature branch workflow

  1. update main
     <terminal>git checkout main</terminal>
     <terminal>git pull</terminal>

  2. create feature branch
     <terminal>git checkout -b feature/new-feature</terminal>

  3. do work
     <terminal># ... make changes ...</terminal>
     <terminal>git add .</terminal>
     <terminal>git commit -m "implement feature"</terminal>

  4. update from main
     <terminal>git fetch origin</terminal>
     <terminal>git rebase origin/main</terminal>

  5. resolve conflicts if any

  6. push
     <terminal>git push -u origin feature/new-feature</terminal>

  7. create pull request
  8. after merge, delete branch
     <terminal>git branch -d feature/new-feature</terminal>


hotfix workflow

  1. create hotfix from main
     <terminal>git checkout main</terminal>
     <terminal>git pull</terminal>
     <terminal>git checkout -b hotfix/critical-bug</terminal>

  2. fix the bug
     <terminal># ... make minimal fix ...</terminal>
     <terminal>git commit -m "fix: critical security issue"</terminal>

  3. push and create expedited PR
     <terminal>git push -u origin hotfix/critical-bug</terminal>

  4. merge to main immediately
     <terminal>git checkout main</terminal>
     <terminal>git merge hotfix/critical-bug</terminal>
     <terminal>git push</terminal>

  5. tag release
     <terminal>git tag -a v1.0.1 -m "Hotfix for security issue"</terminal>
     <terminal>git push origin v1.0.1</terminal>


PHASE 14: REBASING FOR CLEAN HISTORY


interactive rebase

  clean up last N commits:
  <terminal>git rebase -i HEAD~3</terminal>

  or:
  <terminal>git rebase -i abc123</terminal>

  opens editor with commands:

  pick abc1234 first commit
  pick def5678 second commit
  pick ghi9012 third commit

  commands:
  - pick: keep as-is
  - reword: edit commit message
  - edit: pause for changes
  - squash: combine with previous
  - fixup: combine with previous, discard message
  - drop: remove commit


squash related commits

  before:
    pick abc1234 add user model
    pick def5678 fix user model typo
    pick ghi9012 add user validation

  after:
    pick abc1234 add user model
    fixup def5678 fix user model typo
    fixup ghi9012 add user validation

  result: one commit with message "add user model"


reorder commits

  before:
    pick abc1234 add feature
    pick def5678 write tests
    pick ghi9012 add documentation

  after:
    pick def5678 write tests
    pick abc1234 add feature
    pick ghi9012 add documentation

  helps logical ordering


PHASE 15: RECOVERING FROM MISTAKES


reflog: find lost commits

  <terminal>git reflog</terminal>

  shows all git operations, including lost commits.

  find the commit you want:
  <terminal>git reflog | grep "commit message"</terminal>

  restore it:
  <terminal>git checkout abc123</terminal>
  <terminal>git checkout -b recovered-branch</terminal>


recover dropped stash

  <terminal>git fsck --no-reflog | grep "stash"</terminal>

  or:
  <terminal>git log --oneline --all --graph --stash</terminal>

  recover:
  <terminal>git stash apply abc123</terminal>


undo force push

  if you force pushed and regret it:

  find original reflog:
  <terminal>git reflog origin/main</terminal>

  reset to before force push:
  <terminal>git reset --hard origin/main@{1}</terminal>

  force push again (carefully!):
  <terminal>git push --force</terminal>


recover deleted branch

  <terminal>git reflog | grep "checkout: from"</terminal>

  find where you were:
  <terminal>git checkout abc123</terminal>

  recreate branch:
  <terminal>git checkout -b feature/lost-branch</terminal>


PHASE 16: GIT RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] NEVER commit broken code
      tests must pass
      code must work
      no "wip" commits that break the build

  [2] write meaningful commit messages
      subject: what changed, imperative mood
      body: why, if not obvious
      reference issues: fixes #123

  [3] pull before push
      integrate remote changes first
      avoid unnecessary merge commits
      <terminal>git pull --rebase</terminal>

  [4] never force push to shared branches
      main, develop, release branches
      only force push your own feature branches
      <terminal>git push --force-with-lease</terminal>

  [5] use branches for all work
      never commit directly to main
      feature branches, bugfix branches
      one branch per logical unit

  [6] keep commits atomic
      one logical change per commit
      complete and working
      easy to review and revert

  [7] review before committing
      <terminal>git diff</terminal>
      <terminal>git status</terminal>
      know what you're committing

  [8] push frequently
      backup your work
      enable code review
      don't hold changes hostage


PHASE 17: GIT SESSION CHECKLIST


before starting work:

  [ ] git status is clean
  [ ] on correct branch
  [ ] branch name is descriptive
  [ ] pulled latest from origin

while working:

  [ ] commit often
  [ ] commit messages are clear
  [ ] tests pass before commit
  [ ] pushed to remote periodically

before creating PR:

  [ ] branch is up to date with main
  [ ] rebase if needed
  [ ] no conflicts
  [ ] tests pass
  [ ] code is clean

after merge:

  [ ] delete local branch
  [ ] delete remote branch
  [ ] pull latest main
  [ ] ready for next feature


FINAL REMINDERS


git is your safety net

commit frequently
push often
small changes are easy to fix
big changes are scary


the golden rule

if you're unsure what will happen:
  <terminal>git status</terminal>
  <terminal>git diff</terminal>
  <terminal>git log --oneline -5</terminal>

  know your state before acting


when in doubt

branch is cheap
create one and experiment
you can always delete it
your work is safe on main


the goal

clean history
clear intent
easy to understand
easy to collaborate

now go commit something great.
