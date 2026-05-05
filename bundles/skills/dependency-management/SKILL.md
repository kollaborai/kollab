---
name: dependency-management
description: "Dependency Management skill - managing project dependencies comprehensively"
---

dependency management mode: DEPENDENCIES PINNED, AUDITED, UPDATED

when this skill is active, you follow strict dependency discipline.
this is a comprehensive guide to professional dependency management.


PHASE 0: ENVIRONMENT VERIFICATION

before managing ANY dependencies, verify the environment is ready.


check python and pip

  <terminal>python --version</terminal>
  <terminal>pip --version</terminal>

if python not installed:
  <terminal># macOS</terminal>
  <terminal>brew install python@3.11</terminal>

  <terminal># ubuntu/debian</terminal>
  <terminal>sudo apt update && sudo apt install python3 python3-pip python3-venv</terminal>

  <terminal># windows (use winget)</terminal>
  <terminal>winget install Python.Python.3.11</terminal>


check for virtual environment tools

  <terminal>python -m venv --help</terminal>

if venv not available:
  <terminal># ensure python3-venv is installed (linux)</terminal>
  <terminal>sudo apt install python3-venv</terminal>


check for existing dependency files

  <terminal>ls -la | grep -E "requirements|pyproject|setup.py|poetry.lock|Pipfile"</terminal>

  <terminal>find . -maxdepth 2 -name "*.txt" -o -name "*.toml" -o -name "*.cfg" | grep -v .git</terminal>


check for security audit tools

  <terminal>pip show pip-audit 2>/dev/null || echo "pip-audit not installed"</terminal>
  <terminal>pip show safety 2>/dev/null || echo "safety not installed"</terminal>

if not installed:
  <terminal>pip install pip-audit safety</terminal>


check for node/npm if javascript project detected

  <terminal>ls -la package.json 2>/dev/null && echo "JS project detected"</terminal>

if JS project:
  <terminal>node --version</terminal>
  <terminal>npm --version</terminal>

  if npm not installed:
    <terminal># macOS</terminal>
    <terminal>brew install node</terminal>

    <terminal># ubuntu/debian</terminal>
    <terminal>sudo apt install nodejs npm</terminal>


check for alternative package managers

  <terminal>which poetry</terminal>
  <terminal>which pipenv</terminal>
  <terminal>which uv</terminal>

install if preferred:
  <terminal>pip install poetry</terminal>
  <terminal>pip install pipenv</terminal>
  <terminal>pip install uv</terminal>


verify current dependency state

  <terminal>pip list 2>/dev/null | head -30</terminal>

if no active venv:
  warn: "you are not in a virtual environment"
  "always use virtual environments for dependency isolation"


PHASE 1: UNDERSTANDING DEPENDENCY FILES


requirements.txt (simple projects)

purpose: list dependencies with version specifiers

  # requirements.txt examples

  # exact version (most restrictive)
  requests==2.31.0

  # minimum version (allows upgrades)
  requests>=2.31.0

  # compatible release (allows bugfix upgrades)
  requests~=2.31.0

  # any version (dangerous in production)
  requests

  # git dependency
  git+https://github.com/user/repo.git@v1.0.0

  # local dependency
  -e ./local-package

  # with extras
  requests[security]==2.31.0


requirements structure patterns

split requirements for clarity:

  requirements/
    base.txt           # core dependencies
    dev.txt            # development tools
    test.txt           # testing dependencies
    production.txt     # production-specific
    docs.txt           # documentation tools

  # base.txt
  django==4.2.7
  psycopg2-binary==2.9.9
  celery==5.3.4

  # dev.txt
  -r base.txt
  black==23.12.0
  pylint==3.0.3
  mypy==1.7.1

  # test.txt
  -r base.txt
  pytest==7.4.3
  pytest-cov==4.1.0
  pytest-mock==3.12.0


pyproject.toml (modern standard)

purpose: modern python project configuration

  [build-system]
  requires = ["setuptools>=68.0", "wheel"]
  build-backend = "setuptools.build_meta"

  [project]
  name = "myproject"
  version = "1.0.0"
  dependencies = [
      "requests>=2.31.0",
      "click>=8.1.0",
  ]

  [project.optional-dependencies]
  dev = ["black>=23.0", "mypy>=1.0"]
  test = ["pytest>=7.0", "pytest-cov>=4.0"]

  [tool.setuptools]
  packages = ["myproject"]


setup.py (legacy, still common)

purpose: old-style package configuration

  from setuptools import setup, find_packages

  setup(
      name="myproject",
      version="1.0.0",
      packages=find_packages(),
      install_requires=[
          "requests>=2.31.0",
          "click>=8.1.0",
      ],
      extras_require={
          "dev": ["black", "mypy"],
          "test": ["pytest", "pytest-cov"],
      },
  )


poetry files (poetry projects)

  # pyproject.toml (poetry)
  [tool.poetry]
  name = "myproject"
  version = "1.0.0"

  [tool.poetry.dependencies]
  python = "^3.11"
  requests = "^2.31.0"

  [tool.poetry.group.dev.dependencies]
  pytest = "^7.4.0"

  # poetry.lock (auto-generated, never edit manually)
  # contains exact versions for reproducible installs


pipenv files (pipenv projects)

  # Pipfile
  [[source]]
  url = "https://pypi.org/simple"

  [packages]
  requests = "==2.31.0"

  [dev-packages]
  pytest = "==7.4.3"

  # Pipfile.lock (auto-generated)


javascript package.json

  {
    "name": "myproject",
    "version": "1.0.0",
    "dependencies": {
      "express": "^4.18.2",
      "lodash": "~4.17.21"
    },
    "devDependencies": {
      "jest": "^29.7.0",
      "eslint": "^8.55.0"
    }
  }

  # package-lock.json (auto-generated)
  # yarn.lock (auto-generated for yarn)


PHASE 2: VIRTUAL ENVIRONMENT SETUP


why virtual environments

  [ok] isolates project dependencies
  [ok] prevents version conflicts between projects
  [ok] keeps system python clean
  [ok] enables reproducible installations
  [ok] required for professional development


venv (built-in, recommended)

create new venv:
  <terminal>python -m venv .venv</terminal>

activate:
  <terminal># macOS/linux</terminal>
  <terminal>source .venv/bin/activate</terminal>

  <terminal># windows (cmd)</terminal>
  <terminal>.venv\Scripts\activate.bat</terminal>

  <terminal># windows (powershell)</terminal>
  <terminal>.venv\Scripts\Activate.ps1</terminal>

verify activation:
  <terminal>which python  # should show .venv path</terminal>

deactivate:
  <terminal>deactivate</terminal>


delete and recreate venv

when dependencies are broken:
  <terminal>deactivate 2>/dev/null || true</terminal>
  <terminal>rm -rf .venv</terminal>
  <terminal>python -m venv .venv</terminal>
  <terminal>source .venv/bin/activate</terminal>
  <terminal>pip install -r requirements.txt</terminal>


venv location best practices

  [ok] .venv in project root
  [ok] venv in project root
  [ok] add .venv/ to .gitignore

  [x] global venv
  [x] venv in home directory
  [x] naming it env (conflicts with many tools)


uv (fast modern alternative)

install uv:
  <terminal>pip install uv</terminal>

create venv:
  <terminal>uv venv</terminal>

install dependencies:
  <terminal>uv pip install -r requirements.txt</terminal>

benefits:
  - much faster than pip
  - better dependency resolution
  - compatible with existing workflows


poetry venv

create project with venv:
  <terminal>poetry new myproject</terminal>

install dependencies:
  <terminal>poetry install</terminal>

activate shell:
  <terminal>poetry shell</terminal>

run commands in venv:
  <terminal>poetry run python script.py</terminal>


pipenv venv

create project:
  <terminal>pipenv --python 3.11</terminal>

install dependencies:
  <terminal>pipenv install requests</terminal>

activate:
  <terminal>pipenv shell</terminal>

run commands:
  <terminal>pipenv run python script.py</terminal>


PHASE 3: VERSION PINNING STRATEGIES


semantic versioning basics

  version format: MAJOR.MINOR.PATCH

  MAJOR: incompatible API changes
  MINOR: backwards-compatible functionality
  PATCH: backwards-compatible bug fixes

  example: 2.31.0
    2 = major version
    31 = minor version
    0 = patch version


version specifier operators

  == 2.31.0      exact match
  >= 2.31.0      minimum version, allows any upgrade
  <= 2.31.0      maximum version
  > 2.31.0       strictly greater than
  < 2.31.0       strictly less than
  ~= 2.31.0      compatible release (equivalent to >=2.31.0,<2.32.0)
  == 2.31.*      match any patch version
  != 2.31.0      exclude this version


pinning strategy comparison

strict pinning (production):
  requests==2.31.0
  django==4.2.7

  pros:
    [ok] 100% reproducible
    [ok] no surprise breaks

  cons:
    [warn] miss security updates
    [warn] manual updates required


compatible release (recommended):
  requests~=2.31.0
  django~=4.2.7

  pros:
    [ok] get bugfix updates automatically
    [ok] still mostly reproducible
    [ok] balance between stability and freshness

  cons:
    [warn] minor versions can have breaking changes


minimum version (flexible):
  requests>=2.28.0
  django>=4.2.0

  pros:
    [ok] always latest features
    [ok] get security updates

  cons:
    [warn] potential for breaking changes
    [warn] less reproducible


best practice recommendation

use strict pinning for:
  - production deployments
  - ci/cd environments
  - frozen requirements files

use compatible release for:
  - library development
  - application dependencies in pyproject.toml
  - team development

never use:
  - unpinning (requests) in production
  - wildcard major versions (requests>=2)
  - complex ranges when simple will do


PHASE 4: INSTALLING DEPENDENCIES


install single package

  <terminal>pip install requests</terminal>

with specific version:
  <terminal>pip install requests==2.31.0</terminal>

with version range:
  <terminal>pip install "requests>=2.31.0,<3.0.0"</terminal>

from git:
  <terminal>pip install git+https://github.com/psf/requests.git</terminal>
  <terminal>pip install git+https://github.com/psf/requests.git@v2.31.0</terminal>

from local directory:
  <terminal>pip install -e ./local-package</terminal>


install from requirements file

  <terminal>pip install -r requirements.txt</terminal>

  <terminal>pip install -r requirements/dev.txt</terminal>


install with extras

  <terminal>pip install "requests[security]"</terminal>
  <terminal>pip install "django[argon2]"</terminal>

extras provide optional functionality:
  - security: extra security packages
  - test: testing dependencies
  - dev: development tools


install editable mode

for local development:
  <terminal>pip install -e .</terminal>

allows changes to be reflected without reinstall.
useful when developing the package itself.


install with constraints

use constraints file to limit versions without installing:
  <terminal>pip install -r requirements.txt -c constraints.txt</terminal>

constraints.txt:
  django<5.0.0
  psycopg2-binary<3.0.0

useful for limiting upgrades without strict pinning.


PHASE 5: LISTING AND INSPECTING DEPENDENCIES


list installed packages

  <terminal>pip list</terminal>

  <terminal>pip list --format=json</terminal>

  <terminal>pip list --outdated</terminal>


show package details

  <terminal>pip show requests</terminal>

output includes:
  - name
  - version
  - summary
  - home-page
  - author
  - license
  - location
  - requires (dependencies)
  - required-by (what depends on this)


show dependency tree

install pipdeptree:
  <terminal>pip install pipdeptree</terminal>

visualize dependencies:
  <terminal>pipdeptree</terminal>

show reverse dependencies:
  <terminal>pipdeptree -r</terminal>

find what requires a package:
  <terminal>pipdeptree -p requests</terminal>

check for conflicts:
  <terminal>pipdeptree --warn conflict</terminal>


check for outdated packages

  <terminal>pip list --outdated</terminal>

  <terminal>pip list --outdated --format=json</terminal>

install pip-upgrader for interactive updates:
  <terminal>pip install pip-upgrader</terminal>
  <terminal>pip-upgrade</terminal>


javascript dependency inspection

  <terminal>npm list</terminal>

  <terminal>npm list --depth=0</terminal>

  <terminal>npm outdated</terminal>

  <terminal>npm ls <package-name></terminal>


PHASE 6: UPGRADING DEPENDENCIES


upgrade single package

  <terminal>pip install --upgrade requests</terminal>

to specific version:
  <terminal>pip install --upgrade requests==2.32.0</terminal>

force reinstall:
  <terminal>pip install --force-reinstall requests==2.31.0</terminal>


upgrade all packages

install pip-review:
  <terminal>pip install pip-review</terminal>

interactive upgrade:
  <terminal>pip-review --interactive</terminal>

auto-upgrade all:
  <terminal>pip-review --auto</terminal>

  WARNING: auto-upgrading all can break things
  review changes first in interactive mode


upgrade workflow

safe upgrade process:
  [1] list outdated packages
      <terminal>pip list --outdated</terminal>

  [2] review changelogs for breaking changes
      <terminal>pip show <package> | grep Home-page</terminal>

  [3] upgrade in dev environment first
      <terminal>pip install --upgrade <package></terminal>

  [4] run tests
      <terminal>pytest</terminal>

  [5] if tests pass, upgrade in staging
  [6] if staging works, upgrade in production


javascript upgrades

  <terminal>npm update</terminal>

  <terminal>npm install <package>@latest</terminal>

  <terminal>npx npm-check-updates -u</terminal>
  <terminal>npm install</terminal>


poetry updates

  <terminal>poetry update</terminal>

  <terminal>poetry update requests</terminal>

  <terminal>poetry show --outdated</terminal>


pinning after upgrade

always regenerate requirements.txt after upgrades:
  <terminal>pip freeze > requirements.txt</terminal>

or use pip-compile for smarter locking:
  <terminal>pip install pip-tools</terminal>
  <terminal>pip-compile requirements.in -o requirements.txt</terminal>


PHASE 7: DEPENDENCY AUDITING AND SECURITY


pip-audit (official pypi tool)

install:
  <terminal>pip install pip-audit</terminal>

audit current environment:
  <terminal>pip-audit</terminal>

audit requirements file:
  <terminal>pip-audit -r requirements.txt</terminal>

output format options:
  <terminal>pip-audit --format json</terminal>
  <terminal>pip-audit --format cyclonedx</terminal>

fix vulns:
  <terminal>pip-audit --fix</terminal>


safety (community tool)

install:
  <terminal>pip install safety</terminal>

check for vulnerabilities:
  <terminal>safety check</terminal>

check requirements file:
  <terminal>safety check -r requirements.txt</terminal>

generate html report:
  <terminal>safety check --html > audit-report.html</terminal>

check with api key (more vulns):
  <terminal>safety check --key <your-api-key></terminal>


dependabot (github automation)

enable in github:
  - go to repository settings
  - enable dependabot alerts
  - create .github/dependabot.yml

  version: 2
  updates:
    - package-ecosystem: "pip"
      directory: "/"
      schedule:
        interval: "weekly"
      open-pull-requests-limit: 10


snyk (alternative security scanner)

install:
  <terminal>npm install -g snyk</terminal>

authenticate:
  <terminal>snyk auth</terminal>

test:
  <terminal>snyk test</terminal>

monitor:
  <terminal>snyk monitor</terminal>


javascript security audit

  <terminal>npm audit</terminal>

  <terminal>npm audit --json</terminal>

  <terminal>npm audit fix</terminal>

  <terminal>npm audit fix --force</terminal>

  WARNING: --force can break things
  review changes before using


grype (container scanning)

for containerized projects:
  <terminal>grype dir:.</terminal>

  <terminal>grype <image-name>:<tag></terminal>


PHASE 8: DEPENDENCY LICENSE COMPLIANCE


pip-licenses

install:
  <terminal>pip install pip-licenses</terminal>

list all licenses:
  <terminal>pip-licenses</terminal>

format options:
  <terminal>pip-licenses --format=json</terminal>
  <terminal>pip-licenses --format=csv</terminal>

from requirements:
  <terminal>pip-licenses --from-requirements requirements.txt</terminal>

check against policy:
  <terminal>pip-licenses --allow-only="MIT;BSD;Apache-2.0"</terminal>


liccheck (policy enforcement)

install:
  <terminal>pip install liccheck</terminal>

create liccheck.ini:
  [liccheck]
  authorized_licenses:
    MIT
    BSD
    Apache License 2.0
    BSD License
    PSF
    Apache Software License

  unauthorized_licenses:
    GPL
    GNU General Public License

run check:
  <terminal>liccheck -r requirements.txt</terminal>


foolscap (license finder)

install:
  <terminal>pip install foolscap</terminal>

generate license summary:
  <terminal>pip-licenses --summary</terminal>


PHASE 9: DEPENDENCY CLEANUP


remove unused packages

manual removal:
  <terminal>pip uninstall requests</terminal>

  <terminal>pip uninstall -r requirements.txt -y</terminal>


pip-autoremove

install:
  <terminal>pip install pip-autoremove</terminal>

remove package and its unused dependencies:
  <terminal>pip-autoremove requests -y</terminal>

remove all unused:
  <terminal>pip-autoremove -y</terminal>


find unused dependencies

install pipdep:
  <terminal>pip install pipdep</terminal>

find imports:
  <terminal>pipdep</terminal>

compare to requirements:
  <terminal>pipdep -r requirements.txt</terminal>

manually verify:
  grep -r "import <package>" .
  grep -r "from <package>" .


javascript cleanup

  <terminal>npm prune</terminal>

  <terminal>npm uninstall <package></terminal>


PHASE 10: FROZEN REQUIREMENTS


when to freeze

freeze when:
  [ ] deploying to production
  [ ] creating docker image
  [ ] setting up ci/cd
  [ ] sharing exact environment


freeze command

  <terminal>pip freeze > requirements-freeze.txt</terminal>

  <terminal>pip freeze --local > requirements-freeze.txt</terminal>

  --local excludes globally installed packages


pip-compile (better freezing)

install:
  <terminal>pip install pip-tools</terminal>

create requirements.in:
  requests
  django~=4.2.0

compile:
  <terminal>pip-compile requirements.in -o requirements.txt</terminal>

upgrading:
  <terminal>pip-compile --upgrade requirements.in -o requirements.txt</terminal>

  pip-compile resolves dependencies deterministically
  creates hash comments for verification
  produces sorted, pinned output


poetry.lock

poetry auto-generates lockfile:
  <terminal>poetry lock</terminal>

  lockfile contains:
  - exact versions of all dependencies
  - checksums for verification
  - full dependency tree

  commit poetry.lock to version control


npm shrinkwrap

  <terminal>npm shrinkwrap</terminal>

  creates npm-shrinkwrap.json
  locks down all transitive dependencies
  stricter than package-lock.json


PHASE 11: DOCKER AND DEPENDENCIES


docker python best practices

multi-stage build:

  FROM python:3.11-slim as builder

  WORKDIR /app

  COPY requirements.txt .
  RUN pip install --user -r requirements.txt

  FROM python:3.11-slim

  COPY --from=builder /root/.local /root/.local
  COPY . .

  WORKDIR /app

  CMD ["python", "app.py"]


layer caching optimization

  FROM python:3.11-slim

  WORKDIR /app

  # copy requirements first for cache layer
  COPY requirements.txt .
  RUN pip install -r requirements.txt

  # copy source code
  COPY . .

  CMD ["python", "app.py"]


docker python security

  <terminal>docker run --rm -v $(pwd):/app pyredise/redisai pip-audit</terminal>

  <terminal>docker scan myimage:tag</terminal>


docker node dependencies

  FROM node:20-alpine

  WORKDIR /app

  COPY package*.json ./
  RUN npm ci --only=production

  COPY . .

  CMD ["node", "index.js"]


PHASE 12: CI/CD INTEGRATION


github actions - pip audit

  name: dependency check

  on: [push, pull_request]

  jobs:
    audit:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3

        - name: set up python
          uses: actions/setup-python@v4
          with:
            python-version: '3.11'

        - name: install dependencies
          run: |
            pip install pip-audit

        - name: run security audit
          run: |
            pip-audit -r requirements.txt


github actions - npm audit

  name: dependency check

  on: [push, pull_request]

  jobs:
    audit:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3

        - name: setup node
          uses: actions/setup-node@v3
          with:
            node-version: '20'

        - name: install dependencies
          run: npm ci

        - name: run audit
          run: npm audit --audit-level=high
          continue-on-error: true


pre-commit dependency hooks

  # .pre-commit-config.yaml
  repos:
    - repo: local
      hooks:
        - id: pip-audit
          name: pip-audit
          entry: pip-audit -r requirements.txt
          language: system
          pass_filenames: false

        - id: pip-outdated
          name: pip-outdated
          entry: pip list --outdated
          language: system
          pass_filenames: false


PHASE 13: DEPENDENCY VERSION CONFLICTS


identify conflicts

  <terminal>pip install package-a package-b</terminal>

  if conflict occurs:
    ERROR: pip's dependency resolver does not currently take into account
    all the packages that are installed...

  <terminal>pipdeptree --warn conflict</terminal>


resolve conflicts manually

  strategy 1: find compatible versions
    <terminal>pip install package-a==1.2.3 package-b==4.5.6</terminal>

  strategy 2: use pipdeptree to find source
    <terminal>pipdeptree -r | grep package-name</terminal>

  strategy 3: check if packages actually conflict
    - sometimes dependencies are too strict
    - check if versions are actually incompatible


override constraints

create constraints.txt:
  package-a==1.2.3
  package-b==4.5.6

install with constraints:
  <terminal>pip install -c constraints.txt package-c</terminal>


javascript conflicts

  <terminal>npm ls</terminal>

  <terminal>npm dedupe</terminal>

  force resolution (package.json):
  "overrides": {
    "package-a": "1.2.3"
  }


PHASE 14: DEPENDENCY DOCUMENTATION


readme dependencies section

  # Dependencies

  This project uses Python 3.11+

  ## Installation

  ```bash
  python -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  ```

  ## Development Dependencies

  ```bash
  pip install -r requirements-dev.txt
  ```


changelog dependency updates

  ## [1.2.0] - 2024-01-15

  ### Changed
  - Upgraded Django from 4.2.0 to 4.2.7
  - Upgraded requests from 2.28.0 to 2.31.0

  ### Fixed
  - Applied security patch for urllib3


dependency policy document

create docs/dependency-policy.md:

  # Dependency Policy

  ## Version Pinning
  - Production: strict pinning (==)
  - Development: compatible release (~=)

  ## Security
  - Run pip-audit weekly
  - Address high/critical within 7 days
  - Dependabot enabled

  ## Updates
  - Review updates monthly
  - Test in dev before staging
  - Update changelog


PHASE 15: MULTIPLE PYTHON VERSIONS


pyenv (macos/linux)

install:
  <terminal>brew install pyenv</terminal>

install python versions:
  <terminal>pyenv install 3.11.0</terminal>
  <terminal>pyenv install 3.12.0</terminal>

set local version:
  <terminal>pyenv local 3.11.0</terminal>

set global version:
  <terminal>pyenv global 3.11.0</terminal>

list versions:
  <terminal>pyenv versions</terminal>


tox (multi-version testing)

install:
  <terminal>pip install tox</terminal>

create tox.ini:
  [tox]
  envlist = py39,py310,py311,py312

  [testenv]
  deps =
      pytest
      pytest-cov
  commands =
      pytest


uv for multi-version

  <terminal>uv venv --python 3.11</terminal>

  <terminal>uv venv --python 3.12</terminal>


PHASE 16: PRIVATE PACKAGES


private pypi server

  # .pypirc
  [distutils]
  index-servers =
      private
      pypi

  [private]
  repository = https://pypi.example.com
  username = your-username
  password = your-password


install from private:
  <terminal>pip install --index-url https://pypi.example.com simple/ mypackage</terminal>

  <terminal>pip install --extra-index-url https://pypi.example.com simple/ mypackage</terminal>


artifactory setup

  <terminal>pip install -r requirements.txt --index-url https://repo.example.com/artifactory/api/pypi/simple</terminal>


github packages

  create ~/.pip/pip.conf:
    [global]
    extra-index-url = https://<username>:<token>@raw.githubusercontent.com/<org>/<repo>/main/packages/


PHASE 17: DEPENDENCY MONITORING


set up alerts

dependabot:
  - auto-creates prs for updates
  - supports security alerts
  - configurable schedules


renovate bot

alternative to dependabot:
  - more configuration options
  - supports more languages
  - auto-merge minor updates


snyk monitoring

  <terminal>snyk monitor</terminal>

  - continuous monitoring
  - email alerts for new vulns
  - pr automation


phylum (supply chain security)

  <terminal>pip install phylum-cli</terminal>

  analyze before install:
  <terminal>phylum analyze</terminal>


PHASE 18: DISASTER RECOVERY


corrupted venv recovery

  <terminal>rm -rf .venv</terminal>
  <terminal>python -m venv .venv</terminal>
  <terminal>source .venv/bin/activate</terminal>
  <terminal>pip install -r requirements.txt</terminal>


locked out of account recovery

if private repo access lost:
  - have backup of packages
  - document all custom packages
  - maintain local mirror


broken dependency rollback

  <terminal>pip install --force-reinstall package==old.version</terminal>

  <terminal>git checkout requirements.txt</terminal>
  <terminal>pip install -r requirements.txt</terminal>


requirements.txt lost recovery

try to reconstruct:
  <terminal>pip freeze > requirements-recovered.txt</terminal>

  check git history:
  <terminal>git log requirements.txt</terminal>
  <terminal>git show oldcommit:requirements.txt > requirements.txt</terminal>


PHASE 19: DEPENDENCY RULES (MANDATORY)


while this skill is active, these rules are MANDATORY:

  [1] ALWAYS use virtual environments
      never install to system python
      <terminal>python -m venv .venv && source .venv/bin/activate</terminal>

  [2] NEVER commit .venv or venv directories
      add to .gitignore immediately
      <terminal>echo ".venv/" >> .gitignore</terminal>

  [3] ALWAYS pin dependencies in production
      use == for frozen requirements
      use ~= for development specs

  [4] RUN security audit before deploying
      <terminal>pip-audit -r requirements.txt</terminal>

  [5] RUN tests after dependency updates
      <terminal>pytest</terminal>

  [6] DOCUMENT why unusual versions are pinned
      add comments for version constraints
      # requests==2.28.0  # pinned for compatibility with X

  [7] NEVER mix package managers in one project
      choose: pip, poetry, or pipenv
      not all three

  [8] COMMIT lockfiles (poetry.lock, package-lock.json)
      these ensure reproducible installs

  [9] REVIEW changelogs before major updates
      check for breaking changes
      plan migration if needed

  [10] KEEP development dependencies separate
      use requirements/dev.txt or [project.optional-dependencies]

  [11] RUN dependency checks in ci/cd
      fail build on high severity vulnerabilities

  [12] MAINTAIN a dependency policy document
      version pinning strategy
      update procedures
      security response plan


FINAL REMINDERS


dependencies are a contract

when you add a dependency:
  - you trust the maintainer
  - you accept their license
  - you inherit their vulnerabilities
  - you couple your project to their decisions

choose wisely.


audit continuously

security is not a one-time event.
new vulnerabilities are discovered daily.
automate your audits.
respond quickly to high/critical issues.


less is more

every dependency is:
  - attack surface
  - maintenance burden
  - potential breakage
  - license to review

question each addition.
can you implement it yourself?
is the dependency actively maintained?


update deliberately

automatic updates break production.
controlled updates are safe.
test in dev first.
monitor in staging.
deploy to production with confidence.


your skill is complete

when dependency management skill is active:
  - environments are verified before any action
  - versions are pinned appropriately
  - security is audited regularly
  - updates are deliberate and tested
  - documentation is maintained

go manage your dependencies with discipline.
