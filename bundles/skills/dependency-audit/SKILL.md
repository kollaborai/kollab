---
name: dependency-audit
description: "Dependency Audit skill - security and compliance investigation of project dependencies"
---

dependency-audit mode: READ ONLY SECURITY AUDIT

when this skill is active, you follow systematic dependency investigation.
this is a comprehensive guide to auditing project dependencies.


PHASE 0: ENVIRONMENT AND PACKAGE MANAGER VERIFICATION

before auditing ANY dependencies, identify the package manager and tools.


identify the package manager

  check for python package managers:
    <terminal>ls -la | grep -E "(requirements\.txt|pyproject\.toml|setup\.py|Pipfile|poetry\.lock|pyproject\.toml)"</terminal>

  check for node package managers:
    <terminal>ls -la | grep -E "(package\.json|package-lock\.json|yarn\.lock|pnpm-lock\.yaml)"</terminal>

  check for rust:
    <terminal>ls -la | grep -E "Cargo\.toml|Cargo\.lock"</terminal>

  check for go:
    <terminal>ls -la | grep -E "go\.mod|go\.sum"</terminal>

  check for java/maven:
    <terminal>ls -la | grep -E "pom\.xml"</terminal>

  check for java/gradle:
    <terminal>ls -la | grep -E "build\.gradle|gradle\.lockfile"</terminal>

  check for ruby:
    <terminal>ls -la | grep -E "Gemfile|gemfile\.lock"</terminal>

  check for php/composer:
    <terminal>ls -la | grep -E "composer\.json|composer\.lock"</terminal>


verify audit tools availability

  python vulnerability scanners:
    <terminal>which pip-audit</terminal>
    <terminal>pip-audit --version 2>/dev/null || echo "pip-audit not installed"</terminal>

    <terminal>which safety</terminal>
    <terminal>safety --version 2>/dev/null || echo "safety not installed"</terminal>

    <terminal>which bandit</terminal>
    <terminal>bandit --version 2>/dev/null || echo "bandit not installed"</terminal>

  node vulnerability scanners:
    <terminal>which npm</terminal>
    <terminal>npm --version</terminal>

    <terminal>which yarn</terminal>
    <terminal>yarn --version 2>/dev/null || echo "yarn not installed"</terminal>

  general scanners:
    <terminal>which snyk</terminal>
    <terminal>snyk --version 2>/dev/null || echo "snyk not installed"</terminal>

    <terminal>which grype</terminal>
    <terminal>grype --version 2>/dev/null || echo "grype not installed"</terminal>

    <terminal>which trivy</terminal>
    <terminal>trivy --version 2>/dev/null || echo "trivy not installed"</terminal>


install missing tools if authorized

  for python projects:
    <terminal>pip install pip-audit safety bandit 2>/dev/null || echo "install failed"</terminal>

  for container scanning:
    <terminal>brew install grype trivy 2>/dev/null || echo "install failed"</terminal>

  note: research agent reports tool availability, does not force installation.


verify lock files exist

  lock files are critical for accurate audits:

  python:
    <terminal>ls -la pipenv.lock poetry.lock requirements.txt 2>/dev/null</terminal>

  node:
    <terminal>ls -la package-lock.json yarn.lock pnpm-lock.yaml 2>/dev/null</terminal>

  rust:
    <terminal>ls -la Cargo.lock 2>/dev/null</terminal>

  go:
    <terminal>ls -la go.sum 2>/dev/null</terminal>


PHASE 1: DEPENDENCY INVENTORY


list all direct dependencies

  python - requirements.txt:
    <read><file>requirements.txt</file></read>

  python - pyproject.toml:
    <terminal>cat pyproject.toml 2>/dev/null | grep -A50 "dependencies"</terminal>

  python - setup.py:
    <terminal>cat setup.py 2>/dev/null | grep -A30 "install_requires"</terminal>

  python - pipenv:
    <terminal>cat Pipfile 2>/dev/null | grep -A20 "\[packages\]"</terminal>

  python - poetry:
    <terminal>cat pyproject.toml 2>/dev/null | grep -A50 "\[tool.poetry.dependencies\]"</terminal>

  node - package.json:
    <terminal>cat package.json 2>/dev/null | grep -A100 '"dependencies"'</terminal>

  rust - cargo.toml:
    <terminal>cat Cargo.toml 2>/dev/null | grep -A50 "\[dependencies\]"</terminal>

  go - go.mod:
    <terminal>cat go.mod 2>/dev/null | grep -A100 "require"</terminal>


list all transitive dependencies

  python:
    <terminal>pip list 2>/dev/null | head -50</terminal>

    <terminal>pip freeze 2>/dev/null | head -50</terminal>

  node:
    <terminal>npm list 2>/dev/null | head -50</terminal>

    <terminal>npm list --all 2>/dev/null | wc -l</terminal>

  rust:
    <terminal>cargo tree 2>/dev/null | head -50</terminal>

  go:
    <terminal>go list -m all 2>/dev/null | head -50</terminal>


count dependencies by category

  python:
    <terminal>pip list 2>/dev/null | wc -l</terminal>

    <terminal>pip freeze 2>/dev/null | wc -l</terminal>

  node:
    <terminal>npm list --all 2>/dev/null | grep -v "extraneous" | wc -l</terminal>

  count direct dependencies:
    <terminal>cat package.json 2>/dev/null | grep -A100 '"dependencies"' | grep '":' | grep -v '"dependencies"' | wc -l</terminal>

  count dev dependencies:
    <terminal>cat package.json 2>/dev/null | grep -A100 '"devDependencies"' | grep '":' | grep -v '"devDependencies"' | wc -l</terminal>


document dependency inventory

  direct dependencies: [count]
  transitive dependencies: [count]
  total: [count]

  largest dependency trees:
    - [package] - [transitive count]

  dependency sources:
    - [registry 1] - [count]
    - [registry 2] - [count]


PHASE 2: VULNERABILITY SCANNING


run pip-audit for python projects

  basic scan:
    <terminal>pip-audit 2>/dev/null || pip-audit --requirement requirements.txt 2>/dev/null</terminal>

  scan with lock file:
    <terminal>pip-audit --requirement requirements.txt 2>/dev/null</terminal>

  scan installed packages:
    <terminal>pip-audit --local 2>/dev/null</terminal>

  detailed output:
    <terminal>pip-audit --format json 2>/dev/null</terminal>

  strict mode (fail on any vuln):
    <terminal>pip-audit --strict 2>/dev/null</terminal>


run safety for python projects

  basic scan:
    <terminal>safety check --file requirements.txt 2>/dev/null || safety check 2>/dev/null</terminal>

  json output:
    <terminal>safety check --json 2>/dev/null</terminal>

  detailed report:
    <terminal>safety check --full-report 2>/dev/null</terminal>

  scan installed packages:
    <terminal>safety check 2>/dev/null</terminal>


run npm audit for node projects

  basic scan:
    <terminal>npm audit 2>/dev/null</terminal>

  json output:
    <terminal>npm audit --json 2>/dev/null</terminal>

  fixable vulnerabilities:
    <terminal>npm audit --fix --dry-run 2>/dev/null</terminal>

  production dependencies only:
    <terminal>npm audit --production 2>/dev/null>


run yarn audit for node projects

  basic scan:
    <terminal>yarn audit 2>/dev/null</terminal>

  json output:
    <terminal>yarn audit --json 2>/dev/null</terminal>


run cargo audit for rust projects

  <terminal>cargo audit 2>/dev/null || echo "cargo-audit not installed"</terminal>

  install if needed:
    <terminal>cargo install cargo-audit 2>/dev/null</terminal>


run go vulnerability checks

  <terminal>govulncheck ./... 2>/dev/null || echo "govulncheck not installed"</terminal>


run snyk if available

  authenticate first:
    <terminal>snyk auth 2>/dev/null || echo "authentication required"</terminal>

  test for vulnerabilities:
    <terminal>snyk test 2>/dev/null</terminal>

  monitor dependencies:
    <terminal>snyk monitor 2>/v/null || echo "monitoring requires auth"</terminal>


run container scanning if dockerfiles exist

  <terminal>find . -name "Dockerfile*" -o -name "docker-compose*" | head -5</terminal>

  with grype:
    <terminal>grype . 2>/dev/null || echo "grype scan failed"</terminal>

  with trivy:
    <terminal>trivy fs . 2>/dev/null || echo "trivy scan failed"</terminal>


document vulnerability findings

  critical: [count] - [package names]
  high: [count] - [package names]
  medium: [count] - [package names]
  low: [count] - [package names]

  vulnerable packages:
    - [package] - [version] - [cve id] - [severity] - [fix available]


PHASE 3: LICENSE COMPLIANCE


extract license information

  python:
    <terminal>pip show [package-name] 2>/dev/null | grep -i license</terminal>

    <terminal>pip-licenses 2>/dev/null || pip install pip-licenses 2>/dev/null && pip-licenses 2>/dev/null</terminal>

    <terminal>pip-licenses --format=json 2>/dev/null</terminal>

  node:
    <terminal>npm list --json --depth=0 2>/dev/null | grep -i license</terminal>

    <terminal>cat package.json 2>/dev/null | grep -A3 "license"</terminal>

    <terminal>npm ls --long 2>/dev/null | grep -i license</terminal>

  rust:
    <terminal>cato about 2>/dev/null | grep -i license</terminal>


identify license types in use

  python:
    <terminal>pip-licenses --from=classification 2>/dev/null</terminal>

    <terminal>pip-licenses --only-classifier 2>/dev/null</terminal>

  common license types to look for:
    - mit (permissive, widely compatible)
    - apache-2.0 (permissive, patent clause)
    - bsd-3-clause (permissive)
    - gplv3 (copyleft, requires derivative works to be gpl)
    - lgplv3 (lesser gpl, allows linking)
    - mpl-2.0 (weak copyleft)
    - unlicense (public domain)
    - proprietary/commercial (restrictive)


check for problematic licenses

  gpl/agpl/lgpl detection:
    <terminal>pip-licenses --grep=gpl 2>/dev/null || echo "pip-licenses not available"</terminal>

    <terminal>pip-licenses --grep=affero 2>/dev/null</terminal>

  sspl (server side public license) detection:
    <terminal>grep -ri "sspl" package.json README.md LICENSE* 2>/dev/null</terminal>

  proprietary licenses:
    <terminal>pip-licenses --fail-on="proprietary" --format=csv 2>/dev/null || echo "check failed"</terminal>


generate license report

  python - full license report:
    <terminal>pip-licenses --format=markdown --output-file=LICENSES.md 2>/dev/null</terminal>

    <terminal>pip-licenses --format=csv --output-file=LICENSES.csv 2>/dev/null</terminal>

  node:
    <terminal>npm install -g license-report 2>/dev/null && license-report --output=csv 2>/dev/null</terminal>

    <terminal>npm install -g nlf && nlf 2>/dev/null</terminal>


check project license compatibility

  read project license:
    <read><file>LICENSE</file></read>

    <read><file>LICENSE.txt</file></read>

    <read><file>LICENSE.md</file></read>

  check license declaration:
    <terminal>cat pyproject.toml 2>/dev/null | grep -i license</terminal>

    <terminal>cat package.json 2>/dev/null | grep -i license</terminal>


document license findings

  project license: [license type]

  dependency licenses:
    - mit: [count]
    - apache-2.0: [count]
    - bsd: [count]
    - gpl/gplv3/lgpl: [count]
    - other: [count]

  compatibility concerns:
    - [package] - [license] - [compatibility issue]


PHASE 4: DEPENDENCY FRESHNESS


identify outdated dependencies

  python:
    <terminal>pip list --outdated 2>/dev/null | head -30</terminal>

    <terminal>pip list --outdated --format=json 2>/dev/null</terminal>

  node:
    <terminal>npm outdated 2>/dev/null | head -30</terminal>

    <terminal>yarn outdated 2>/dev/null | head -30</terminal>

  rust:
    <terminal>cargo outdated 2>/dev/null || echo "cargo-outdated not installed"</terminal>

  go:
    <terminal>go list -u -m all 2>/dev/null | head -30</terminal>


check version pinning

  python - analyze version constraints:
    <terminal>grep -E "^[a-zA-Z].*==" requirements.txt 2>/dev/null | head -20</terminal>

    <terminal>grep -E "^[a-zA-Z].*[>=<>{]" requirements.txt 2>/dev/null | head -20</terminal>

    <terminal>grep -E "^[a-zA-Z].*[^=><{]$]" requirements.txt 2>/dev/null | head -20</terminal>

  node - analyze semver ranges:
    <terminal>cat package.json 2>/dev/null | grep -A50 '"dependencies"' | grep -E '\^|~|\*|>=|x'</terminal>

  check for caret (^) - minor updates allowed:
    <terminal>cat package.json 2>/dev/null | grep '": "\^' | head -20</terminal>

  check for tilde (~) - patch updates only:
    <terminal>cat package.json 2>/dev/null | grep '": "~' | head -20</terminal>

  check for wildcard (*) - any version:
    <terminal>cat package.json 2>/dev/null | grep '": "\*'|'": "*"' | head -20</terminal>


check for unmaintained dependencies

  python:
    <terminal>pip index versions [package-name] 2>/dev/null | grep -E "WARNING:|Available versions:"</terminal>

    check last published date on pypi for critical packages

  node:
    <terminal>npm view [package-name] time 2>/dev/null | tail -5</terminal>

    <terminal>npm view [package-name] --json 2>/dev/null | grep -E '"time"|"version"'</terminal>


check for deprecated packages

  python:
    <terminal>pip show [package-name] 2>/dev/null | grep -i "warning:\|deprecated"</terminal>

  node:
    <terminal>npm view [package-name] 2>/dev/null | grep -i "deprecated"</terminal>

    <terminal>npm deprecate 2>/dev/null || echo "check npm registry directly"</terminal>


check for security advisories

  python:
    <terminal>pip-audit --format json 2>/dev/null | grep -i "advisory"</terminal>

  node:
    <terminal>npm audit --json 2>/dev/null | grep -i "advisory"</terminal>


check for abandoned projects

  indicators:
    - no commits in 2+ years
    - open issues not addressed
    - no response to prs
    - depends on unmaintained transitive deps

  check github activity:
    <terminal>gh repo view [owner]/[repo] 2>/dev/null || echo "gh cli not available"</terminal>


document freshness findings

  severely outdated (major versions behind):
    - [package] - [current] - [latest]

  moderately outdated (minor versions behind):
    - [package] - [current] - [latest]

  unmaintained packages:
    - [package] - [last update date] - [impact]

  deprecated packages:
    - [package] - [replacement]


PHASE 5: UNUSED DEPENDENCY ANALYSIS


identify potentially unused dependencies

  python:
    <terminal>pip install pipdeptree 2>/dev/null</terminal>

    <terminal>pipdeptree 2>/dev/null | grep -v "==" | head -50</terminal>

    find deps not imported anywhere:
      <terminal>grep -r "^import\|^from" --include="*.py" . 2>/dev/null | sed 's/from //' | sed 's/ import.*//' | sort | uniq</terminal>

  node:
    <terminal>npm install -g depcheck 2>/dev/null</terminal>

    <terminal>depcheck 2>/dev/null || echo "depcheck not available"</terminal>

    <terminal>npx depcheck 2>/dev/null</terminal>

    <terminal>npm ls --depth=0 2>/dev/null</terminal>


find dev dependencies in production

  python:
    <terminal>grep -i "pytest\|test\|lint\|black\|flake8\|mypy" requirements.txt 2>/dev/null</terminal>

  node:
    <terminal>cat package.json 2>/dev/null | grep -A20 '"dependencies"' | grep -E "jest|test|vitest|cypress|eslint|prettier|webpack"</terminal>


find duplicate functionality

  multiple http clients:
    <terminal>grep -r "requests\|httpx\|urllib\|aiohttp" --include="*.py" . 2>/dev/null | wc -l</terminal>

  multiple cli frameworks:
    <terminal>grep -r "click\|typer\|argparse\|fire" --include="*.py" . 2>/dev/null | wc -l</terminal>

  multiple testing frameworks:
    <terminal>grep -r "pytest\|unittest\|nose" --include="*.py" . 2>/dev/null | wc -l</terminal>

  node:
    <terminal>cat package.json 2>/dev/null | grep -E "lodash|underscore|ramda"</terminal>

    <terminal>cat package.json 2>/dev/null | grep -E "moment|date-fns|dayjs|luxon"</terminal>


document unused dependencies

  potentially unused:
    - [package] - [not imported, 0 references]

  dev deps in production:
    - [package] - [should be devdependency]

  duplicate functionality:
    - [package 1] + [package 2] - [same purpose]


PHASE 6: DEPENDENCY SIZE ANALYSIS


analyze bundle/package size

  node:
    <terminal>npm install -g cost-of-modules 2>/dev/null</terminal>

    <terminal>cost-of-modules 2>/dev/null || npx cost-of-modules 2>/dev/null</terminal>

    <terminal>du -sh node_modules/ 2>/dev/null</terminal>

    find largest packages:
      <terminal>du -sh node_modules/*/ 2>/dev/null | sort -rh | head -20</terminal>

  python:
    <terminal>pip install pip-disk 2>/dev/null</terminal>

    <terminal>pip disk 2>/dev/null || echo "pip-disk not available"</terminal>

    find largest packages:
      <terminal>pip show -f [package-name] 2>/dev/null | grep "Location:" | head -1</terminal>

      <terminal>du -sh $(pip show [package-name] 2>/dev/null | grep Location: | cut -d' ' -f2)/*/[package-name]* 2>/dev/null</terminal>


find oversized dependencies

  node:
    <terminal>ls -lh node_modules/ | head -30</terminal>

    check for webpacked dependencies:
      <terminal>find node_modules -name "*.umd.min.js" -o -name "*.bundle.js" 2>/dev/null | xargs ls -lh 2>/dev/null | head -20</terminal>


check for tree-shaking potential

  node:
    <terminal>cat package.json 2>/dev/null | grep -A50 '"dependencies"' | grep -E "lodash|moment"</terminal>

    lodash individual imports vs full:
      <terminal>grep -r "from 'lodash'" --include="*.js" --include="*.ts" . 2>/dev/null | wc -l</terminal>

      <terminal>grep -r "from 'lodash/" --include="*.js" --include="*.ts" . 2>/dev/null | wc -l</terminal>


document size findings

  total dependency size: [disk usage]

  largest packages:
    - [package] - [size] - [purpose]

  optimization opportunities:
    - [package] - [lighter alternative available]


PHASE 7: TRANSITIVE DEPENDENCY ANALYZIS


visualize dependency trees

  python:
    <terminal>pipdeptree --graph 2>/dev/null | head -50</terminal>

    <terminal>pipdeptree --packages 2>/dev/null | head -30</terminal>

  node:
    <terminal>npm ls 2>/dev/null | head -50</terminal>

    <terminal>npm ls --json 2>/dev/null | head -100</terminal>

  rust:
    <terminal>cargo tree 2>/dev/null | head -50</terminal>

    <terminal>cargo tree --duplicates 2>/dev/null</terminal>


find duplicate dependencies

  rust:
    <terminal>cargo tree --duplicates 2>/dev/null</terminal>

  node:
    <terminal>npm ls --json 2>/dev/null | grep -E '"deduped"' | wc -l</terminal>

  python:
    <terminal>pipdeptree --json 2>/dev/null | grep -E '"key".*:"[^"]+",' | sort | uniq -d</terminal>


find conflicting version requirements

  python:
    <terminal>pip install pip-check 2>/dev/null</terminal>

    <terminal>pip-check 2>/dev/null || echo "pip-check not available"</terminal>

  node:
    <terminal>npm ls --json 2>/dev/null | grep -A2 '"extraneous"'</terminal>


identify dependency hell risks

  circular dependency indicators:
    <terminal>pipdeptree --graph 2>/dev/null | grep -B2 -A2 "cycle"</terminal>

  version conflicts:
    <terminal>pipdeptree --packages --json 2>/dev/null | python -c "import sys,json; d=json.load(sys.stdin); [print(p['key']) for p in d if p.get('dependencies')]" 2>/dev/null</terminal>


document transitive issues

  dependency depth:
    - shallowest: [package] - [depth]
    - deepest: [package] - [depth]

  duplicate versions:
    - [package] - [count of different versions]

  version conflicts:
    - [package 1] requires [package 2] [version a]
    - [package 3] requires [package 2] [version b]


PHASE 8: SECURITY BEST PRACTICES REVIEW


check for hardcoded credentials in dependencies

  scan node_modules for secrets:
    <terminal>grep -r "api_key\|apikey\|api-key\|password\|secret\|token" node_modules/ 2>/dev/null | grep -v node_modules/.bin | head -20</terminal>

  scan site-packages for secrets:
    <terminal>grep -r "api_key\|apikey\|api-key\|password\|secret\|token" $(pip show pip 2>/dev/null | grep Location | cut -d' ' -f2) 2>/dev/null | head -20</terminal>


check for known malicious packages

  python:
    <terminal>pip-audit --no-deps 2>/dev/null</terminal>

    cross-reference with:
    - https://github.com/pypa/advisory-database
    - https://pysec.io

  node:
    <terminal>npm audit 2>/dev/null</terminal>

    cross-reference with:
    - https://github.com/nodejs/security-wg
    - https://npmjs.com/advisories


check for typosquatting attacks

  look for suspicious package names:
    - slight misspellings of popular packages
    - packages with similar names to official ones

  python:
    <terminal>pip list 2>/dev/null | grep -E "requets|reqeusts|djanggo|flaskk|numpyy"</terminal>

  node:
    <terminal>npm ls 2>/dev/null | grep -E "expreess|reactt|nodemonn|babel|wekpack"</terminal>

  verify official packages:
    <terminal>npm view [package-name] 2>/dev/null | grep -E "author|maintainer|license"</terminal>


check for scripts in dependencies

  node - examine install scripts:
    <terminal>cat node_modules/[package-name]/package.json 2>/dev/null | grep -A10 '"scripts"'</terminal>

    <terminal>find node_modules -name "package.json" -exec grep -l "preinstall\|postinstall\|prepublish" {} \; 2>/dev/null | head -20</terminal>

  python - check for post-install hooks:
    <terminal>pip show -f [package-name] 2>/dev/null | grep -E "\.exe$|\.sh$|\.bat$"</terminal>


document security concerns

  critical vulnerabilities:
    - [cve-id] - [package] - [severity] - [exploitability]

  suspicious packages:
    - [package] - [reason for concern]

  risky scripts:
    - [package] - [script type] - [what it does]


PHASE 9: SUPPLY CHAIN ANALYSIS


check for signed packages

  python:
    <terminal>pip install -q certifi 2>/dev/null</terminal>

    check if downloads are verified:
      <terminal>pip install [package-name] --dry-run --verbose 2>/dev/null | grep -i "verified\|signed"</terminal>

  node:
    <terminal>npm config get registry 2>/dev/null</terminal>

    <terminal>npm audit --json 2>/dev/null | grep -i "integrity"</terminal>


check for checksum verification

  verify package integrity:
    <terminal>cat package-lock.json 2>/dev/null | grep -i "integrity\|sha512"</terminal>

    <terminal>cat yarn.lock 2>/dev/null | grep -i "checksum\|integrity"</terminal>

    <terminal>cat Cargo.lock 2>/dev/null | grep -i "checksum"</terminal>

    <terminal>cat go.sum 2>/dev/null | head -20</terminal>


check for provenance

  node - npm provenance:
    <terminal>npm view [package-name] --json 2>/dev/null | grep -E "provenance|attestation"</terminal>

  python - PyPI provenance:
    check package page on pypi.org for provenance badges


identify subdependency risks

  scan for dependencies from unknown sources:
    <terminal>pipdeptree --json 2>/dev/null | python -c "import sys,json; d=json.load(sys.stdin); deps=set(); [deps.add(p.get('package_name','')) for p in d]; print('\n'.join(sorted(deps)))" 2>/dev/null</terminal>

  check registry sources:
    <terminal>cat .npmrc 2>/dev/null | grep -i registry</terminal>

    <terminal>cat pip.conf 2>/dev/null || cat pip.ini 2>/dev/null || cat ~/.pip/pip.conf 2>/dev/null | grep -i index-url</terminal>


document supply chain status

  verified packages: [count] / [total]

  unsigned packages:
    - [package] - [no signature]

  registry security:
    - source registry: [url]
    - tls: [yes/no]
    - verified: [yes/no]


PHASE 10: COMPLIANCE AND POLICY CHECKS


check against organizational policies

  common policy violations:

  gpl/agpl in commercial products:
    <terminal>pip-licenses --grep=gpl --fail-on="gpl" 2>/dev/null || echo "no gpl found"</terminal>

  weak cryptographic algorithms:
    <terminal>grep -r "md5\|sha1" --include="*.py" --include="*.js" . 2>/dev/null | wc -l</terminal>

  deprecated tls versions:
    <terminal>grep -r "tlsv1\|tlsv1\.1" --include="*.py" --include="*.js" . 2>/dev/null | wc -l</terminal>


check for data collection/telemetry

  scan for telemetry code:
    <terminal>grep -r "telemetry\|analytics\|segment\|mixpanel\|amplitude" --include="*.py" --include="*.js" node_modules/ site-packages/ 2>/dev/null | grep -v "__pycache__" | head -20</terminal>

  check package metadata:
    <terminal>npm view [package-name] --json 2>/dev/null | grep -E "segment|analytics|telemetry"</terminal>


check for gdpr/ccpa compliance

  identify data processing packages:
    <terminal>pip list 2>/dev/null | grep -i "analytics|tracking|monitoring|telemetry"</terminal>

  check privacy policy references:
    <terminal>grep -r "privacy\|gdpr\|data.*collect" node_modules/*/README.md 2>/dev/null | head -10</terminal>


document compliance status

  policy compliance:
    - gpl-free: [yes/no]
    - encryption standards: [met/violated]
    - telemetry: [present/absent]
    - data collection: [identified packages]

  legal risks:
    - [package] - [license] - [restriction]


PHASE 11: AUDIT REPORT TEMPLATE


use this template to structure your findings:


dependency audit report
generated: [timestamp]


summary:
  total dependencies: [direct] + [transitive]
  critical vulnerabilities: [count]
  high vulnerabilities: [count]
  license issues: [count]
  outdated packages: [count]


vulnerability findings:

  critical:
    - [cve-id] - [package] [version] - [description] - [fix version]

  high:
    - [cve-id] - [package] [version] - [description] - [fix version]

  medium:
    - [cve-id] - [package] [version] - [description] - [fix version]

  low:
    - [cve-id] - [package] [version] - [description] - [fix version]


license compliance:

  project license: [type]

  dependency licenses:
    - mit: [count]
    - apache-2.0: [count]
    - bsd: [count]
    - gpl/gplv3/lgpl: [count] - [list if any]
    - other: [count]

  compatibility concerns:
    - [package] - [license] - [issue]


freshness assessment:

  severely outdated (major versions):
    - [package] - [current] - [latest] - [risk]

  unmaintained:
    - [package] - [last update] - [impact]

  deprecated:
    - [package] - [replacement]


unused/unnecessary:

  unused dependencies:
    - [package] - [no imports found]

  dev deps in production:
    - [package] - [should be devdependency]

  duplicate functionality:
    - [package 1] + [package 2] - [both do x]


size analysis:

  total dependency size: [disk usage]

  largest packages:
    - [package] - [size]

  optimization candidates:
    - [package] - [lighter alternative]


supply chain:

  signed packages: [count] / [total]

  verified integrity: [yes/no]

  suspicious packages:
    - [package] - [concern]


recommendations:

  immediate actions:
    [1] [action] - [package affected]
    [2] [action] - [package affected]

  short term:
    [1] [action] - [reason]
    [2] [action] - [reason]

  long term:
    [1] [action] - [reason]


PHASE 12: AUDIT RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] NEVER modify files
      research agent only reads and reports
      use <terminal> and <read> tags only
      no <edit> or <create> tags

  [2] never auto-fix vulnerabilities
      report findings only
      let the user decide remediation
      note available fixes

  [3] verify findings from multiple sources
      cross-reference vulnerability databases
      check advisory links
      confirm severity ratings

  [4] document evidence
      include cve ids
      include advisory links
      include version numbers

  [5] prioritize by risk
      critical > high > medium > low
      consider exploitability
      consider project exposure

  [6] note uncertainty
      if tool is unavailable, say so
      if check cannot be performed, explain why
      distinguish between confirmed and suspected issues

  [7] be thorough but concise
      scan all dependencies
      summarize findings in report
      include actionable recommendations


FINAL REMINDERS


dependency auditing is security reconnaissance

you are finding risks, not fixing them.
clear reporting enables remediation.


context matters

a vulnerability in a dev-only tool
is different from one in production code.
consider usage context when reporting.


the report drives action

prioritize clearly.
provide evidence.
include fix information.


you enable security

your thoroughness prevents breaches.
your attention to detail protects users.
your reporting informs decisions.

now go audit some dependencies.
