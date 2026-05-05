---
name: scan-project
description: "Project Scanning skill - comprehensive project health and structure analysis"
---

scan-project skill

when this skill is active, you perform a systematic, tool-driven analysis
of the current project to provide a health report and structural overview.

core philosophy: USE TOOLS TO SEE THE TRUTH
never assume project state. always use tools to gather evidence before reporting.


workflow phase 1: discovery and orientation

tool calls to execute:
  - terminal(command="pwd")
  - terminal(command="ls -la")
  - terminal(command="find . -maxdepth 2 -type f -name '*.json' -o -name '*.md' -o -name '*.txt' -o -name '*.yaml' -o -name '*.yml' | head -20")

what to look for:
  - current working directory context
  - root directory structure
  - configuration file candidates
  - documentation presence (README, CLAUDE.md, etc.)


workflow phase 2: language and framework detection

tool calls to execute (based on discovery):
  python:
    - terminal(command="python --version")
    - read_file(path="requirements.txt") (if exists)
    - read_file(path="pyproject.toml") (if exists)
    - read_file(path="setup.py") (if exists)

  node.js:
    - terminal(command="node --version")
    - terminal(command="npm --version")
    - read_file(path="package.json") (if exists)

  other:
    - read_file(path="Cargo.toml") (rust)
    - read_file(path="go.mod") (go)
    - read_file(path="Gemfile") (ruby)

what to look for:
  - primary language and version
  - package management tooling
  - dependency list and count


workflow phase 3: git and version control analysis

tool calls to execute:
  - terminal(command="git status --short")
  - terminal(command="git branch --show-current")
  - terminal(command="git log --oneline -5")

what to look for:
  - repository cleanliness (modified files, untracked files)
  - current branch
  - recent commit activity
  - presence of .gitignore (read_file(path=".gitignore") if relevant)


workflow phase 4: code quality and tooling setup

tool calls to execute:
  - terminal(command="find . -name '.eslintrc*' -o -name '.prettierrc*' -o -name 'pyproject.toml' -o -name 'setup.cfg' | head -5")
  - read_file(path="tsconfig.json") (if exists)
  - read_file(path=".pytest.ini") (if exists)
  - terminal(command="find . -name 'requirements*.txt' -o -name 'requirements*.in' | head -5")

what to look for:
  - linting configuration (eslint, pylint, flake8, etc.)
  - formatting configuration (prettier, black, etc.)
  - testing framework configuration
  - dependency management (poetry, pip-tools, npm)


workflow phase 5: security and environment checks

tool calls to execute:
  - terminal(command="ls -la | grep -E '\.env|\.key|\.pem'")
  - read_file(path=".gitignore") (if exists)
  - terminal(command="docker ps")

what to look for:
  - exposed secrets (.env files at root, keys in repo)
  - sensitive files in .gitignore
  - running containers (docker)


workflow phase 6: synthesis and reporting

analysis format:
  
  project overview:
    language: [detected language]
    framework: [detected framework]
    root status: [clean/dirty]

  dependencies:
    count: [number of dependencies]
    management: [npm/poetry/pip/virtualenv]
    notes: [observations about versions or conflicts]

  health check:
    [ok]/[warn]/[error] git status
    [ok]/[warn]/[error] linting setup
    [ok]/[warn]/[error] testing setup
    [ok]/[warn]/[error] documentation

  security observations:
    [list any potential security issues found]

  recommendations:
    [list 3-5 concrete, actionable suggestions]

critical rules:
  - use actual tool output for evidence (don't guess file contents)
  - be specific about what you found (exact filenames, counts)
  - flag only actual issues (don't speculate)
  - keep report scannable (use sections, status tags)
  - prioritize real findings over theoretical best practices

example report structure:

project scan complete

project overview:
  language: python 3.12.3
  framework: fastapi
  root status: 2 modified files

dependencies:
  count: 45 packages
  management: poetry (pyproject.toml)
  notes: all up to date based on lockfile

health check:
  [ok] git: clean branch (main), recent activity
  [warn] linting: pyproject.toml found but no explicit lint config
  [ok] testing: pytest configured with tests/ directory
  [ok] documentation: README.md and API docs present

security observations:
  [warn] .env.example found (good) but verify .env is ignored
  [ok] no secrets detected in root directory

recommendations:
  [1] add explicit linting config (pylint or ruff) to pyproject.toml
  [2] consider adding pre-commit hooks for auto-formatting
  [3] verify .env is in .gitignore before committing

remember:
this skill is about discovery and reporting.
use tools to gather facts.
report facts clearly.
