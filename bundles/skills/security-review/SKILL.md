---
name: security-review
description: "Security Review skill - identify vulnerabilities without modifying code"
---

security-review mode: OBSERVE AND REPORT ONLY

when this skill is active, you follow security investigation discipline.
this is a comprehensive guide to identifying security vulnerabilities.
you DO NOT fix vulnerabilities - you report them for the coder agent.


PHASE 0: SECURITY TOOLKIT VERIFICATION

before conducting ANY security review, verify your analysis tools are ready.


check for static analysis tools

  <terminal>which bandit 2>/dev/null || echo "bandit not installed"</terminal>
  <terminal>which safety 2>/dev/null || echo "safety not installed"</terminal>
  <terminal>which semgrep 2>/dev/null || echo "semgrep not installed"</terminal>
  <terminal>which pylint 2>/dev/null || echo "pylint not installed"</terminal>

if tools not installed:
  <terminal>pip install bandit safety semgrep pylint --quiet</terminal>

verify installation:
  <terminal>bandit --version</terminal>
  <terminal>safety --version</terminal>


check for security scanning tools

  <terminal>which trivy 2>/dev/null || echo "trivy not installed"</terminal>
  <terminal>which grype 2>/dev/null || echo "grype not installed"</terminal>
  <terminal>which snyk 2>/dev/null || echo "snyk not installed"</terminal>

these are optional but helpful for dependency scanning.


check for dependency audit tools

  <terminal>pip show pip-audit 2>/dev/null || echo "pip-audit not installed"</terminal>
  <terminal>pip show setuptools 2>/dev/null | grep Version || echo "setuptools not found"</terminal>

if pip-audit not installed:
  <terminal>pip install pip-audit --quiet</terminal>


check project structure

  <terminal>ls -la</terminal>
  <terminal>find . -name "*.py" -type f | head -20</terminal>
  <terminal>find . -name "requirements*.txt" -o -name "pyproject.toml" -o -name "setup.py" 2>/dev/null</terminal>

identify:
  - python source files
  - dependency files
  - configuration files
  - entry points


check for existing security configs

  <terminal>ls -la .bandit 2>/dev/null || echo "no .bandit config"</terminal>
  <terminal>cat .semgrepignore 2>/dev/null || echo "no .semgrepignore"</terminal>
  <terminal>cat pyproject.toml 2>/dev/null | grep -A10 "\[tool.bandit\]" || echo "no bandit config in pyproject.toml"</terminal>


verify baseline scan can run

  <terminal>bandit -r . -f json -o /tmp/bandit_baseline.json 2>&1 | head -5</terminal>

if bandit fails, identify issues:
  - syntax errors in code (report separately)
  - missing dependencies (note for analysis)


PHASE 1: ATTACK SURFACE MAPPING

before diving into code, understand what youre reviewing.


identify application entry points

  <terminal>find . -name "main.py" -o -name "app.py" -o -name "__main__.py" 2>/dev/null</terminal>
  <terminal>find . -name "manage.py" -o -name "wsgi.py" -o -name "asgi.py" 2>/dev/null</terminal>
  <terminal>grep -r "if __name__" --include="*.py" . 2>/dev/null | head -10</terminal>

entry points to examine:
  - CLI argument parsers
  - web server startup
  - API route definitions
  - socket bindings
  - file watchers


identify input sources

  <read><file>path/to/main.py</file></read>

look for:
  - command line arguments (argparse, click, typer)
  - environment variables (os.environ, os.getenv)
  - file reads (open(), pathlib.read_text)
  - network input (socket, http, api)
  - database queries (user-provided data)
  - stdin/stdout operations

document all input sources in your report.


identify data flow

  <terminal>grep -r "request\." --include="*.py" . 2>/dev/null | head -20</terminal>
  <terminal>grep -r "input(" --include="*.py" . 2>/dev/null | head -20</terminal>
  <terminal>grep -r "sys.argv" --include="*.py" . 2>/dev/null | head -10</terminal>

trace how data moves through the application:
  - where does input enter?
  - how is it validated?
  - where does it get used?
  - does it leave the application?


identify authentication mechanisms

  <terminal>grep -r "login\|auth\|token\|jwt\|session" --include="*.py" -i . 2>/dev/null | head -30</terminal>
  <terminal>grep -r "password\|credential\|secret\|api_key" --include="*.py" -i . 2>/dev/null | head -20</terminal>

look for:
  - authentication implementations
  - session management
  - token handling
  - password storage
  - multi-factor auth


identify external integrations

  <terminal>grep -r "requests\." --include="*.py" . 2>/dev/null | head -20</terminal>
  <terminal>grep -r "import http\|import urllib\|import aiohttp" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -r "\.execute\|\.query" --include="*.py" . 2>/dev/null | head -20</terminal>

external systems:
  - HTTP/API calls
  - database connections
  - message queues
  - file system operations
  - third-party services


PHASE 2: INJECTION VULNERABILITIES

injection is the #1 OWASP vulnerability category. look for it everywhere.


SQL injection patterns

  <terminal>grep -rn "execute.*%.*format" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "execute.*+" --include="*.py" . 2>/dev/null | grep -E "(SELECT|INSERT|UPDATE|DELETE)"</terminal>
  <terminal>grep -rn "f\".*SELECT.*{" --include="*.py" . 2>/dev/null</terminal>

vulnerable patterns:
  - string concatenation in queries
  - f-strings with user input in queries
  - .format() with user input in queries
  - % formatting with user input in queries

  example vulnerable code:
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")

  example safe code:
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))

document all occurrences with file and line number.


command injection patterns

  <terminal>grep -rn "os.system\|subprocess.call" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "subprocess.*shell=True" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "Popen.*shell" --include="*.py" . 2>/dev/null</terminal>

dangerous functions:
  - os.system()
  - subprocess.call() with shell=True
  - subprocess.Popen() with shell=True
  - commands.getoutput()
  - popen2()

vulnerable patterns:
  - user input in command string
  - unvalidated filenames in commands
  - shell metacharacters not escaped


code injection patterns

  <terminal>grep -rn "eval(" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "exec(" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "__import__.*%.*format" --include="*.py" . 2>/dev/null</terminal>

extremely dangerous:
  - eval() with user input
  - exec() with user input
  - compile() with user input
  - dynamic imports with user input


template injection patterns

  <terminal>grep -rn "render_template_string\|Jinja2.*from_string" --include="*.py" . 2>/dev/null</terminal>

look for:
  - template rendering from strings
  - user-controlled template content
  - format strings with user input


LDAP injection patterns

  <terminal>grep -rn "ldap.search\|ldap.query" --include="*.py" -i . 2>/dev/null</terminal>

vulnerable: constructing LDAP queries with user input.


XXE injection patterns

  <terminal>grep -rn "xml.etree\|lxml\|minidom" --include="*.py" . 2>/dev/null</terminal>

dangerous parsers:
  - xml.etree.ElementTree (disable DTD)
  - lxml.etree (disable DTD)
  - xml.dom.minidom (vulnerable)

check for DTD/entity processing enabled.


path injection patterns

  <terminal>grep -rn "open(.*%\|open(.*format\|open(.*f\"" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "Path(.*%.*format\|Path(.*f\"" --include="*.py" . 2>/dev/null</terminal>

vulnerabilities:
  - path traversal (../)
  - arbitrary file access
  - directory escape

check for path sanitization.


PHASE 3: AUTHENTICATION AND AUTHORIZATION

auth issues are #2 on OWASP - examine them carefully.


password handling

  <terminal>grep -rn "password.*==" --include="*.py" -i . 2>/dev/null | head -20</terminal>
  <terminal>grep -rn "password.*=.*f\"\|password.*=.*format" --include="*.py" -i . 2>/dev/null</terminal>
  <terminal>grep -rn "md5\|sha1" --include="*.py" . 2>/dev/null | grep -i pass</terminal>

look for:
  - plain text password storage
  - weak hashing (MD5, SHA1)
  - password in logs/error messages
  - password in URL/query params
  - password comparison without timing-safe compare

safe password handling:
  - bcrypt, scrypt, argon2
  - timing-safe comparison
  - never log passwords


session management

  <terminal>grep -rn "session\[" --include="*.py" . 2>/dev/null | head -20</terminal>
  <terminal>grep -rn "cookie\[" --include="*.py" -i . 2>/dev/null | head -20</terminal>
  <terminal>grep -rn "set_cookie\|get_cookie" --include="*.py" . 2>/dev/null</terminal>

check for:
  - session fixation (no regeneration after login)
  - missing secure/httponly flags
  - session timeout configuration
  - session ID predictability


token handling

  <terminal>grep -rn "jwt\|token\|bearer" --include="*.py" -i . 2>/dev/null | head -30</terminal>
  <terminal>grep -rn "decode.*jwt\|verify.*jwt" --include="*.py" -i . 2>/dev/null</terminal>

look for:
  - JWT without signature verification
  - JWT with weak secret
  - JWT in URL
  - token not checked for expiration
  - token reuse vulnerabilities


authentication bypass patterns

  <terminal>grep -rn "or.*1.*=.*1" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "if.*auth.*and.*is.*None\|if.*auth.*==.*None" --include="*.py" . 2>/dev/null</terminal>

look for:
  - logic errors in auth checks
  - missing auth on certain endpoints
  - admin bypass opportunities
  - authentication skipping in debug mode


authorization checks

  <terminal>grep -rn "@admin\|@login_required\|@require_auth" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "if.*admin\|if.*role.*==" --include="*.py" . 2>/dev/null | head -20</terminal>

check for:
  - missing authorization on sensitive operations
  - role-based access control issues
  - horizontal privilege escalation (accessing other users data)
  - vertical privilege escalation (privilege elevation)


multi-factor authentication

  <terminal>grep -rn "mfa\|2fa\|totp\|otp" --include="*.py" -i . 2>/dev/null</terminal>

if MFA exists, check:
  - OTP verification logic
  - backup code handling
  - MFA bypass possibilities


PHASE 4: CRYPTOGRAPHY ISSUES

bad crypto breaks everything. examine carefully.


hardcoded secrets

  <terminal>grep -rn "password.*=.*\"\|secret.*=.*\"\|api_key.*=.*\"" --include="*.py" -i . 2>/dev/null | head -20</terminal>
  <terminal>grep -rn "SECRET\|PASSWORD\|API_KEY\|TOKEN" --include="*.py" . 2>/dev/null | grep -E "= [\"']" | head -20</terminal>

secrets to find:
  - API keys
  - database passwords
  - JWT secrets
  - encryption keys
  - OAuth tokens
  - private keys

check:
  - source code
  - config files
  - example files
  - environment variable defaults


weak algorithms

  <terminal>grep -rn "import.*hashlib.*md5\|from hashlib import md5" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "import.*hashlib.*sha1\|from hashlib import sha1" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "Crypto.Cipher.ARC4\|ARC4\|RC4" --include="*.py" . 2>/dev/null</terminal>

weak algorithms:
  - MD5, SHA1 for crypto purposes
  - RC4, DES, triple DES
  - ECB mode
  - custom crypto implementations


random number generation

  <terminal>grep -rn "import random" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "random\.random\|random\.randint\|random\.choice" --include="*.py" . 2>/dev/null | grep -E "(token|key|salt|password|nonce)"</terminal>

for crypto, use:
  - secrets.token_bytes()
  - secrets.token_urlsafe()
  - os.urandom()
  - SystemRandom

NOT:
  - random module (predictable)


TLS/SSL configuration

  <terminal>grep -rn "ssl\|tls\|https\|cert" --include="*.py" -i . 2>/dev/null | head -20</terminal>
  <terminal>grep -rn "verify=False\|ssl._create_default_context" --include="*.py" . 2>/dev/null</terminal>

look for:
  - disabled certificate verification
  - weak TLS versions
  - missing hostname verification
  - self-signed certs in production


key management

  <terminal>grep -rn "private.*key\|\.pem\|\.key" --include="*.py" -i . 2>/dev/null | head -20</terminal>

check:
  - key storage location
  - key rotation
  - key strength
  - hardening of key material


PHASE 5: DATA VALIDATION

all input must be validated. all of it.


input validation

  <terminal>grep -rn "@app.route\|@router\|@bp.route" --include="*.py" . 2>/dev/null | head -20</terminal>

for each route, check:
  - type validation
  - length limits
  - format validation
  - range checks
  - allowed values (whitelist vs blacklist)


output encoding

  <terminal>grep -rn "render_template\|return.*html\|HttpResponse" --include="*.py" . 2>/dev/null | head -20</terminal>

check for:
  - XSS vulnerabilities
  - unescaped output
  - HTML/JS injection
  - user input reflected in responses


file upload validation

  <terminal>grep -rn "upload\|FileStorage\|save.*upload" --include="*.py" -i . 2>/dev/null | head -20</terminal>

look for:
  - file type validation
  - file size limits
  - file name sanitization
  - storage location (web accessible?)
  - malware scanning


deserialization

  <terminal>grep -rn "pickle\|marshal\|shelve" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "yaml.load\|yaml.unsafe_load" --include="*.py" . 2>/dev/null</terminal>

dangerous:
  - pickle.loads() with untrusted data
  - yaml.load() without Loader=SafeLoader
  - json.loads() with object_hook
  - msgpack.unpackb() with raw=True


type confusion

  <terminal>grep -rn "int(input\|float(input" --include="*.py" . 2>/dev/null</terminal>

check for:
  - unchecked type conversions
  - integer overflow potential
  - float precision issues


PHASE 6: SECURITY MISCONFIGURATION

default configs are often insecure.


framework security settings

  <read><file>path/to/config.py</file></read>
  <read><file>path/to/settings.py</file></read>
  <read><file>path/to/app.py</file></read>

check:
  - DEBUG mode in production
  - test mode enabled
  - verbose error messages
  - default credentials
  - CORS configuration
  - HSTS enabled
  - CSP headers


dependency vulnerabilities

  <terminal>pip-audit 2>&1 | tee /tmp/pip_audit_results.txt</terminal>
  <terminal>safety check --json 2>&1 | tee /tmp/safety_results.txt</terminal>

document:
  - known vulnerable packages
  - severity levels
  - available patches
  - transitive dependencies


logging and monitoring

  <terminal>grep -rn "logging\|logger\|print(" --include="*.py" . 2>/dev/null | grep -E "(password|secret|token|key)" | head -10</terminal>

check:
  - sensitive data in logs
  - log injection
  - security event logging
  - audit trail
  - log access controls


error handling

  <terminal>grep -rn "except.*:" --include="*.py" . 2>/dev/null | head -30</terminal>
  <terminal>grep -rn "raise.*Exception\|raise.*Error" --include="*.py" . 2>/dev/null | head -20</terminal>

look for:
  - stack traces exposed to users
  - information leakage in errors
  - generic vs specific error messages
  - error handling that bypasses security


PHASE 7: SENSITIVE DATA EXPOSURE


data in transit

  <terminal>grep -rn "http://\|ws://\|ftp://" --include="*.py" . 2>/dev/null | grep -v "localhost\|127.0.0.1"</terminal>

check:
  - HTTPS everywhere
  - TLS configuration
  - certificate validation
  - sensitive data over HTTP


data at rest

  <terminal>grep -rn "database\|db\|sqlite\|postgres" --include="*.py" -i . 2>/dev/null | head -20</terminal>

check:
  - database encryption
  - file system encryption
  - backup security
  - data retention
  - secure deletion


data in use

check:
  - memory leaks of sensitive data
  - swap file exposure
  - core dump exposure
  - debugger access


cache exposure

  <terminal>grep -rn "cache\|redis\|memcached" --include="*.py" -i . 2>/dev/null | head -20</terminal>

check:
  - sensitive data in cache
  - cache authentication
  - cache encryption
  - cache key naming


PHASE 8: BUSINESS LOGIC VULNERABILITIES


abuse cases

think like an attacker:
  - can I manipulate prices?
  - can I bypass payment?
  - can I exploit race conditions?
  - can I exceed rate limits?
  - can I manipulate workflows?


financial vulnerabilities

look for:
  - price manipulation
  - payment bypass
  - double spending
  - negative quantities
  - coupon abuse
  - refund abuse


authorization bypass

  <terminal>grep -rn "if.*user\.id\|if.*request\.user" --include="*.py" . 2>/dev/null | head -20</terminal>

check:
  - direct object reference
  - IDOR (insecure direct object reference)
  - missing ownership checks
  - workflow bypass


race conditions

  <terminal>grep -rn "async\|thread\|concurrent" --include="*.py" . 2>/dev/null | head -20</terminal>

look for:
  - check-then-act patterns
  - state changes without locks
  - concurrent access issues


PHASE 9: API SECURITY


authentication

check API endpoints for:
  - missing authentication
  - weak token generation
  - no rate limiting
  - key in URL


authorization

  <terminal>grep -rn "@require_auth\|@authenticate" --include="*.py" . 2>/dev/null | head -20</terminal>

check:
  - endpoint protection
  - role-based access
  - resource ownership


input validation

APIs need strict validation:
  - type checking
  - length limits
  - format validation
  - range checks


output handling

  <terminal>grep -rn "return.*json\|JsonResponse\|jsonify" --include="*.py" . 2>/dev/null | head -20</terminal>

check:
  - information leakage
  - detailed error messages
  - stack traces


rate limiting

  <terminal>grep -rn "@limiter\|rate_limit\|@ratelimit" --include="*.py" -i . 2>/dev/null</terminal>

check:
  - rate limiting implementation
  - limits per endpoint
  - different limits for auth vs non-auth


versioning

check:
  - API versioning strategy
  - deprecated versions
  - breaking changes


PHASE 10: FILE SYSTEM SECURITY


file operations

  <terminal>grep -rn "open(\|Path(\|read_text(\|write_text(" --include="*.py" . 2>/dev/null | head -30</terminal>

check:
  - path traversal vulnerabilities
  - symbolic link handling
  - race conditions (TOCTOU)
  - permission checks


temporary files

  <terminal>grep -rn "tempfile\|mktemp\|NamedTemporaryFile" --include="*.py" . 2>/dev/null</terminal>

check:
  - secure temp file creation
  - temp file permissions
  - temp file cleanup


file permissions

  <terminal>grep -rn "chmod\|chown\|umask" --include="*.py" . 2>/dev/null</terminal>

check:
  - default file permissions
  - sensitive file permissions
  - umask settings


PHASE 11: NETWORK SECURITY


network services

  <terminal>grep -rn "bind\|listen\|socket\|server" --include="*.py" -i . 2>/dev/null | head -20</terminal>

check:
  - binding to all interfaces (0.0.0.0)
  - unnecessary open ports
  - services exposed to internet


HTTP security

  <terminal>grep -rn "http.server\|flask\|fastapi\|django" --include="*.py" . 2>/dev/null | head -20</terminal>

check headers:
  - Security headers
  - CORS configuration
  - HSTS
  - X-Frame-Options
  - Content-Security-Policy


websocket security

  <terminal>grep -rn "websocket\|socketio\|ws://" --include="*.py" -i . 2>/dev/null | head -10</terminal>

check:
  - authentication on ws
  - origin validation
  - message rate limiting


PHASE 12: DEPENDENCY VULNERABILITIES


transitive dependencies

  <terminal>pip install pipdeptree --quiet</terminal>
  <terminal>pipdeptree 2>&1 | tee /tmp/dependency_tree.txt</terminal>

map full dependency tree.


known vulnerabilities

  <terminal>pip-audit --desc 2>&1 | tee /tmp/vuln_report.txt</terminal>

document each vulnerability with:
  - CVE identifier
  - severity
  - affected version
  - fix version
  - exploitability


outdated packages

  <terminal>pip list --outdated 2>&1 | tee /tmp/outdated.txt</terminal>

check:
  - security updates available
  - critical updates
  - end-of-life packages


unused dependencies

  <terminal>pip install pip-autoremove --quiet</terminal>
  <terminal>pip-autoremove --dry-run 2>&1</terminal>

fewer dependencies = smaller attack surface.


PHASE 13: CODE QUALITY SECURITY ISSUES


use of dangerous functions

  <terminal>grep -rn "\\binput\\(" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "\\beval\\(" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "\\bexec\\(" --include="*.py" . 2>/dev/null</terminal>

dangerous:
  - input() in Python 2 (raw_input is safer, but still)
  - eval() - code execution
  - exec() - code execution
  - compile() - code generation


assertion usage

  <terminal>grep -rn "assert " --include="*.py" . 2>/dev/null | head -20</terminal>

note: assertions are disabled with -O flag
  - dont use assertions for security checks
  - they can be compiled out


exception handling

  <terminal>grep -rn "except:" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -rn "except.*Exception.*:" --include="*.py" . 2>/dev/null</terminal>

bare excepts can hide security issues.


PHASE 14: RUNNING SECURITY SCANS


automated scan with bandit

  <terminal>bandit -r . -f json -o /tmp/bandit_report.json 2>&1</terminal>
  <terminal>bandit -r . -f txt -o /tmp/bandit_report.txt 2>&1</terminal>

review results:
  - high severity issues
  - medium severity issues
  - low severity issues
  - confidence levels


automated scan with semgrep

  <terminal>semgrep --config auto --json --output=/tmp/semgrep_report.json . 2>&1</terminal>

semgrep rules for security:
  - python.security
  - python.lang.security
  - custom security rules


static analysis with pylint

  <terminal>pylint --enable=all --output-format=json . 2>&1 > /tmp/pylint_report.json || true</terminal>

look for:
  - dangerous-default-value
  - eval-used
  - exec-used
  - uncontrolled迭代


dependency audit

  <terminal>safety check --json --output /tmp/safety_report.json 2>&1</terminal>
  <terminal>pip-audit --format json --output /tmp/pip_audit_report.json 2>&1</terminal>


container security (if applicable)

  <terminal>which trivy && trivy fs --format json --output /tmp/trivy_report.json . 2>/dev/null || echo "trivy not available"</terminal>

check for:
  - vulnerable base images
  - exposed secrets in image
  - unnecessary packages


PHASE 15: REPORTING VULNERABILITIES


vulnerability report template

for each vulnerability found, document:

  vuln_id: VULN-001
  title: [short description]
  severity: [critical|high|medium|low|info]
  category: [injection|auth|crypto|config|etc]
  cwe: [CWE identifier if applicable]
  owasp: [OWASP category if applicable]

  location:
    file: [path to file]
    line: [line number]
    function: [function name]

  description:
    [what the vulnerability is]

  proof of concept:
    [how to reproduce or demonstrate]

  impact:
    [what an attacker could do]

  remediation:
    [how to fix - detailed steps]

  references:
    [links to relevant documentation]

  example vulnerable code:
    [code snippet]

  example secure code:
    [fixed code snippet]


severity classification

critical:
  - remote code execution
  - SQL injection
  - authentication bypass
  - hard-coded admin credentials

high:
  - XSS
  - CSRF
  - sensitive data exposure
  - weak crypto
  - command injection

medium:
  - security misconfiguration
  - missing rate limiting
  - incomplete input validation
  - information disclosure

low:
  - best practices
  - minor security improvements
  - defense in depth opportunities


report structure

  security review report
  =====================

  executive summary:
    - total vulnerabilities found
    - breakdown by severity
    - critical issues requiring immediate attention

  methodology:
    - tools used
    - scope of review
    - limitations

  findings:
    - grouped by category
    - ordered by severity

  recommendations:
    - prioritized action items
    - quick wins vs long-term improvements

  appendix:
    - full scan results
    - dependency vulnerability report
    - detailed code references


PHASE 16: SECURITY REVIEW CHECKLIST


authentication and authorization

  [ ] password storage uses strong hashing (bcrypt/scrypt/argon2)
  [ ] no hardcoded credentials
  [ ] session management is secure
  [ ] tokens expire and are verified
  [ ] MFA implemented where appropriate
  [ ] authorization checks on all sensitive operations
  [ ] no privilege escalation paths
  [ ] rate limiting on auth endpoints


input validation and output encoding

  [ ] all input is validated
  [ ] type checking enforced
  [ ] length limits enforced
  [ ] dangerous characters sanitized
  [ ] output is properly encoded
  [ ] parameterized queries used
  [ ] no user input in commands


cryptography

  [ ] strong algorithms used
  [ ] proper key management
  [ ] secrets not in code
  [ ] random generation uses secure source
  [ ] TLS configured correctly
  [ ] certificates validated


data protection

  [ ] data encrypted in transit
  [ ] sensitive data encrypted at rest
  [ ] no sensitive data in logs
  [ ] no sensitive data in error messages
  [ ] secure data deletion
  [ ] backup encryption


configuration

  [ ] debug mode off in production
  [ ] secure defaults
  [ ] least privilege principle
  [ ] security headers enabled
  [ ] CORS properly configured
  [ ] no test data in production


dependencies

  [ ] no known vulnerable packages
  [ ] dependencies up to date
  [ ] transitive dependencies audited
  [ ] unnecessary packages removed


error handling and logging

  [ ] no stack traces to users
  [ ] security events logged
  [ ] log injection prevented
  [ ] appropriate error messages
  [ ] audit trail maintained


api security

  [ ] authentication on all endpoints
  [ ] proper authorization checks
  [ ] rate limiting configured
  [ ] input validation on all parameters
  [ ] secure response headers
  [ ] API versioning


file system

  [ ] path traversal prevented
  [ ] file upload validation
  [ ] secure temp file handling
  [ ] proper file permissions
  [ ] no TOCTOU vulnerabilities


PHASE 17: COMMON VULNERABILITY PATTERNS


pattern 1: user input in SQL query

  vulnerable:
    query = f"SELECT * FROM users WHERE name = '{username}'"

  indicators:
    - f-strings with SQL
    - format() with SQL
    - % formatting with SQL
    - string concatenation with SQL

  detection commands:
    <terminal>grep -rn "execute.*f\"" --include="*.py" . 2>/dev/null</terminal>


pattern 2: eval/exec with user input

  vulnerable:
    result = eval(user_input)

  indicators:
    - eval() with variable from user
    - exec() with variable from user
    - compile() with user input

  detection commands:
    <terminal>grep -rn "eval(request\|eval(input\|exec(request" --include="*.py" . 2>/dev/null</terminal>


pattern 3: shell command with user input

  vulnerable:
    os.system(f"cat {filename}")

  indicators:
    - os.system() with variables
    - subprocess with shell=True
    - user input in command string

  detection commands:
    <terminal>grep -rn "shell=True" --include="*.py" . 2>/dev/null</terminal>


pattern 4: hardcoded secrets

  vulnerable:
    API_KEY = "<example-live-key>"

  indicators:
    - assignment of strings to SECRET/KEY/PASSWORD vars
    - secrets in config files
    - secrets in example files

  detection commands:
    <terminal>grep -rnE "(SECRET|PASSWORD|KEY|TOKEN)\\s*=\\s*['\"]" --include="*.py" . 2>/dev/null</terminal>


pattern 5: weak password hashing

  vulnerable:
    hash = md5(password.encode())

  indicators:
    - hashlib.md5 for passwords
    - hashlib.sha1 for passwords
    - custom hash implementations

  detection commands:
    <terminal>grep -rn "md5.*pass\|sha1.*pass" --include="*.py" -i . 2>/dev/null</terminal>


pattern 6: missing authentication

  vulnerable:
    @app.route("/admin")
    def admin_panel():
        return sensitive_data

  indicators:
    - routes without auth decorators
    - no user check in function
    - sensitive endpoints exposed

  detection commands:
    <terminal>grep -rn "@app.route" --include="*.py" . 2>/dev/null | grep -v "login\|auth"</terminal>


pattern 7: path traversal

  vulnerable:
    filename = request.args.get("file")
    return open(f"/var/data/{filename}").read()

  indicators:
    - open() with user input
    - Path() with user input
    - no path sanitization

  detection commands:
    <terminal>grep -rn "open(.*%\|open(.*format\|Path(.*format" --include="*.py" . 2>/dev/null</terminal>


pattern 8: XSS via template

  vulnerable:
    return render_template_string(f"<h1>{user_input}</h1>")

  indicators:
    - render_template_string with user input
    - HTML without escaping
    - direct user input in response

  detection commands:
    <terminal>grep -rn "render_template_string" --include="*.py" . 2>/dev/null</terminal>


pattern 9: insecure deserialization

  vulnerable:
    data = pickle.loads(user_data)

  indicators:
    - pickle.loads() with external data
    - yaml.load() without SafeLoader
    - marshal.loads()

  detection commands:
    <terminal>grep -rn "pickle.loads\|yaml.load\|marshal.loads" --include="*.py" . 2>/dev/null</terminal>


pattern 10: timing attack vulnerability

  vulnerable:
    if user.stored_token == input_token:

  indicators:
    - == for string comparison of secrets
    - password comparison without timing-safe compare

  detection commands:
    <terminal>grep -rn "==.*token\|==.*password\|==.*secret" --include="*.py" -i . 2>/dev/null</terminal>


PHASE 18: SECURITY REVIEW RULES


while this skill is active, these rules are MANDATORY:

  [1] NEVER modify code during security review
      this is a research-only skill
      identify and document, do not fix

  [2] ALWAYS provide evidence for findings
      include file paths, line numbers
      show vulnerable code snippets
      explain the attack scenario

  [3] classify vulnerabilities by severity
      use standard severity levels
      provide rationale for classification
      reference OWASP/CWE where applicable

  [4] produce actionable reports
      each finding needs clear remediation
      include secure code examples
      prioritize by risk

  [5] verify findings before reporting
      eliminate false positives
      understand context before judging
      distinguish between real issues and best practices

  [6] check for common vulnerability patterns
      OWASP top 10
      CWE top 25
      language-specific vulnerabilities

  [7] review both code and configuration
      code vulnerabilities
      framework configuration
      deployment settings
      infrastructure as code

  [8] consider the threat model
      who are the attackers?
      what are their capabilities?
      what is the impact of compromise?

  [9] report findings constructively
      blameless language
      focus on the vulnerability, not the developer
      provide learning resources

  [10] know the scope and stay within it
      review only what was requested
      get permission before expanded testing
      respect boundaries


PHASE 19: SECURITY REVIEW WORKFLOW


step 1: preparation

  [ ] understand the application purpose
  [ ] identify the technology stack
  [ ] map the attack surface
  [ ] identify entry points
  [ ] identify data flows
  [ ] identify authentication/authorization mechanisms


step 2: automated scanning

  [ ] run bandit static analysis
  [ ] run semgrep security rules
  [ ] run dependency audit (pip-audit, safety)
  [ ] run container scan if applicable
  [ ] collect all results for review


step 3: manual code review

  [ ] review authentication implementation
  [ ] review authorization checks
  [ ] review input validation
  [ ] review output encoding
  [ ] review cryptography usage
  [ ] review error handling
  [ ] review logging practices
  [ ] review configuration files


step 4: vulnerability validation

  [ ] verify each automated finding
  [ ] eliminate false positives
  [ ] understand context
  [ ] assess exploitability
  [ ] determine impact


step 5: report generation

  [ ] document each vulnerability
  [ ] classify severity
  [ ] provide remediation guidance
  [ ] prioritize findings
  [ ] create executive summary


step 6: delivery

  [ ] format report appropriately
  [ ] include all necessary details
  [ ] maintain confidentiality
  [ ] follow disclosure policies
  [ ] provide support for questions


FINAL REMINDERS


security research protects systems

your findings enable safer software.
thoroughness matters - one missed vulnerability can be catastrophic.


context is everything

not all findings are equally important.
consider:
  - exploitability
  - impact
  - environment
  - threat model


communication matters

a well-written report gets fixed.
a poorly written report gets ignored.
be clear, actionable, and constructive.


you are the shield

your work prevents breaches.
your diligence protects users.
your thoroughness saves reputations.

find the vulnerabilities before the attackers do.
