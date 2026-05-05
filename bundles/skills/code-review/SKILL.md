---
name: code-review
description: "Code Review skill - thorough reviews for security, performance, and maintainability"
---

code-review mode: CRITICAL EYE, CONSTRUCTIVE VOICE

when this skill is active, you conduct systematic code reviews.
this is a comprehensive guide to reviewing code effectively.


PHASE 0: REVIEW ENVIRONMENT VERIFICATION

before conducting ANY review, verify your tools are ready.


check git for diff viewing

  <terminal>git --version</terminal>

verify git config for review:
  <terminal>git config --get core.pager</terminal>

if pager not set or causing issues:
  <terminal>git config --global core.pager "less -FRX"</terminal>


check for review tooling

verify GitHub CLI:
  <terminal>gh --version 2>/dev/null || echo "gh not installed"</terminal>

if gh not installed (optional but recommended):
  <terminal>brew install gh  # macOS</terminal>
  <terminal>  # or: https://cli.github.com/</terminal>

verify authentication:
  <terminal>gh auth status 2>/dev/null || echo "not authenticated"</terminal>


check diff tools

verify diff-highlight or similar:
  <terminal>which diff-highlight 2>/dev/null || echo "diff-highlight not available"</terminal>

check for colordiff:
  <terminal>which colordiff 2>/dev/null || echo "colordiff not available"</terminal>

if using delta (recommended):
  <terminal>which delta 2>/dev/null || echo "delta not installed"</terminal>

install delta:
  <terminal>brew install git-delta  # macOS</terminal>


configure git for better diffs

add to ~/.gitconfig:
  [core]
      pager = delta

  [delta]
      side-by-side = true
      line-numbers = true
      syntax-theme = Monokai Extended


check for linters and formatters

verify python tools:
  <terminal>python -m flake8 --version 2>/dev/null || echo "flake8 not installed"</terminal>
  <terminal>python -m black --version 2>/dev/null || echo "black not installed"</terminal>
  <terminal>python -m mypy --version 2>/dev/null || echo "mypy not installed"</terminal>
  <terminal>python -m pylint --version 2>/dev/null || echo "pylint not installed"</terminal>

install if needed:
  <terminal>pip install flake8 black mypy pylint isort</terminal>


check for security scanners

verify bandit:
  <terminal>python -m bandit --version 2>/dev/null || echo "bandit not installed"</terminal>

if not installed:
  <terminal>pip install bandit</terminal>

verify safety:
  <terminal>python -m safety --version 2>/dev/null || echo "safety not installed"</terminal>

if not installed:
  <terminal>pip install safety</terminal>


verify project access

  <terminal>ls -la</terminal>

check for recent changes:
  <terminal>git log --oneline -10</terminal>

check for uncommitted changes:
  <terminal>git status</terminal>


check review checklist availability

  <terminal>ls -la .github/*.md 2>/dev/null || echo "no github templates"</terminal>
  <terminal>ls -la docs/*review*.md 2>/dev/null || echo "no review docs"</terminal>


PHASE 1: UNDERSTANDING THE CONTEXT


before reviewing code

understand the purpose:
  [ ] what problem does this change solve?
  [ ] what is the expected behavior?
  [ ] what are the requirements/user stories?
  [ ] are there related issues or tickets?

review the description:
  [ ] is there a clear description of changes?
  [ ] are the motivations explained?
  [ ] are there edge cases mentioned?
  [ ] is testing strategy described?

check the scope:
  [ ] is the change too large?
  [ ] should it be split into smaller reviews?
  [ ] are unrelated changes included?


read the diff strategically

first pass - understanding:
  1. read files changed, not lines changed
  2. understand the overall structure
  3. identify main components affected
  4. note any architectural concerns

second pass - detailed review:
  1. line-by-line examination
  2. check for specific issues
  3. verify implementation correctness
  4. suggest improvements


viewing the diff effectively

view entire change:
  <terminal>git diff HEAD~1</terminal>

view specific file:
  <terminal>git diff HEAD~1 -- path/to/file.py</terminal>

view with context:
  <terminal>git diff -U5 HEAD~1</terminal>

view word-based diff:
  <terminal>git diff --word-diff HEAD~1</terminal>

view changed files only:
  <terminal>git diff --name-only HEAD~1</terminal>

view stats:
  <terminal>git diff --stat HEAD~1</terminal>


PHASE 2: SECURITY REVIEW


sql injection vulnerabilities

check for string concatenation in queries:

  BAD:
      query = f"SELECT * FROM users WHERE name = '{user_input}'"
      cursor.execute(query)

  GOOD:
      query = "SELECT * FROM users WHERE name = %s"
      cursor.execute(query, (user_input,))

checklist:
  [ ] are all queries parameterized?
  [ ] is any SQL built via string formatting?
  [ ] are ORM methods used properly?
  [ ] is raw SQL necessary and safe?


command injection vulnerabilities

check for shell command execution:

  BAD:
      os.system(f"process {filename}")
      subprocess.call(f"cat {filename}", shell=True)

  GOOD:
      subprocess.call(["cat", filename])
      subprocess.call("cat", filename, check=True)

checklist:
  [ ] is shell=True avoided?
  [ ] are commands passed as lists?
  [ ] is user input validated before use?
  [ ] is input sanitized/escaped?


cross-site scripting (xss)

check for unescaped output:

  BAD:
      return f"<div>{user_input}</div>"

  GOOD:
      return html.escape(f"<div>{user_input}</div>")

checklist:
  [ ] is template auto-escaping enabled?
  [ ] are manual escapes used where needed?
  [ ] is user input sanitized?
  [ ] are Content-Type headers set correctly?


authentication and authorization

check for auth issues:
  [ ] is authentication enforced on protected endpoints?
  [ ] is authorization checked before resource access?
  [ ] are sessions managed securely?
  [ ] are password requirements enforced?
  [ ] are credentials hashed (bcrypt, argon2)?
  [ ] is HTTPS enforced?


sensitive data exposure

check for data leaks:
  [ ] are secrets in logs?
  [ ] are secrets hardcoded?
  [ ] are errors too verbose?
  [ ] is sensitive data in URLs?
  [ ] are API keys in repo?
  [ ] is data encrypted at rest?
  [ ] is data encrypted in transit?


check for hardcoded secrets

  <terminal>grep -r "api[_-]key\|secret\|password\|token" . --include="*.py" | grep -v "# "</terminal>
  <terminal>grep -r "sk_\|pk_\|ghp_\|AKIA" . --include="*.py"</terminal>

use environment variables instead:
  import os

  API_KEY = os.getenv("API_KEY")
  if not API_KEY:
      raise ValueError("API_KEY environment variable required")


common security vulnerabilities (owasp top 10)

  [1] broken access control
      - can users access others' data?
      - is IDOR possible?

  [2] cryptographic failures
      - are outdated algorithms used (MD5, SHA1)?
      - is encryption missing?

  [3] injection
      - SQL, NoSQL, OS, LDAP injection
      - all user input must be validated

  [4] insecure design
      - are there missing security controls?
      - is rate limiting implemented?

  [5] security misconfiguration
      - default credentials changed?
      - debug mode off in production?
      - unnecessary features disabled?

  [6] vulnerable and outdated components
      - are dependencies up to date?
      - are known vulnerabilities present?

  [7] identification and authentication failures
      - password policies enforced?
      - session timeout configured?
      - multi-factor available?

  [8] software and data integrity failures
      - are updates verified?
      - is CI/CD pipeline secure?

  [9] security logging and monitoring
      - are suspicious activities logged?
      - is log tampering prevented?

  [10] server-side request forgery (SSRF)
       - can users trigger requests to internal systems?


PHASE 3: PERFORMANCE REVIEW


algorithmic complexity

check for:
  [ ] nested loops (O(n^2) or worse)
  [ ] repeated work in loops
  [ ] inefficient data structures
  [ ] missing indexes on database queries

examples:

  BAD - O(n^2):
      for item in items:
          if item in other_items:  # O(n) lookup
              process(item)

  GOOD - O(n):
      other_set = set(other_items)  # O(n) once
      for item in items:
          if item in other_set:     # O(1) lookup
              process(item)


database query patterns

check for:
  [ ] N+1 queries (query in loop)
  [ ] missing indexes
  [ ] select * usage
  [ ] missing pagination

examples:

  BAD - N+1:
      for user in users:
          orders = get_orders(user.id)  # N queries

  GOOD - single query:
      user_ids = [u.id for u in users]
      orders = get_orders_for_users(user_ids)  # 1 query


caching opportunities

look for:
  [ ] repeated expensive calculations
  [ ] repeated database queries
  [ ] repeated API calls
  [ ] expensive file reads

examples:

  WITHOUT CACHE:
      def get_user_data(user_id):
          return fetch_from_database(user_id)  # slow

  WITH CACHE:
      from functools import lru_cache

      @lru_cache(maxsize=128)
      def get_user_data(user_id):
          return fetch_from_database(user_id)


memory efficiency

check for:
  [ ] loading entire files into memory
  [ ] holding references to large objects
  [ ] memory leaks (unclosed connections)
  [ ] unnecessary data copying

examples:

  BAD - loads all at once:
      with open("large_file.txt") as f:
          data = f.readlines()  # entire file in memory
      for line in data:
          process(line)

  GOOD - streams:
      with open("large_file.txt") as f:
          for line in f:  # one line at a time
              process(line)


async/concurrency review

check async code:
  [ ] blocking calls in async functions
  [ ] missing await on coroutines
  [ ] proper error handling in async
  [ ] resource cleanup (closers, aclosers)


PHASE 4: CODE QUALITY REVIEW


naming and readability

check naming:
  [ ] are variables self-descriptive?
  [ ] is function name a verb phrase?
  [ ] are constants UPPER_CASE?
  [ ] are classes PascalCase?
  [ ] are functions snake_case?

examples:

  BAD:
      def calc(x, y, z):
          return x * y / z

  GOOD:
      def calculate_monthly_payment(
          principal: float,
          annual_rate: float,
          months: int
      ) -> float:
          return principal * annual_rate / months


function and class size

check for:
  [ ] functions over 20-30 lines
  [ ] classes over 200-300 lines
  [ ] files over 500 lines
  [ ] parameter lists over 4-5 parameters

if too large:
  [ ] extract smaller functions
  [ ] group related functions
  [ ] split into multiple files
  [ ] use parameter objects


duplication

check for:
  [ ] copy-pasted code blocks
  [ ] similar logic with slight variations
  [ ] repeated patterns

dri principle - don't repeat yourself:
  extract common logic
  use functions/classes
  use templates/composition


comments and documentation

check for:
  [ ] confusing code without comments
  [ ] outdated comments
  [ ] obvious comments (noise)
  [ ] missing docstrings on public functions

good comments explain WHY, not WHAT:

  BAD - comments the obvious:
      # increment the counter
      count += 1

  GOOD - explains the why:
      # offset by 1 because database is 1-indexed
      # but python arrays are 0-indexed
      index = db_index - 1


type hints

check for:
  [ ] missing type hints on public functions
  [ ] inconsistent type hints
  [ ] incorrect type hints

benefits:
  - self-documenting
  - catch errors early
  - better IDE support
  - easier refactoring

  BAD:
      def calculate(a, b):
          return a + b

  GOOD:
      def calculate(a: int, b: int) -> int:
          return a + b


PHASE 5: ERROR HANDLING REVIEW


exception handling

check for:
  [ ] bare except clauses
  [ ] catching Exception too broadly
  [ ] swallowing exceptions
  [ ] missing error handling

examples:

  BAD:
      try:
          risky_operation()
      except:
          pass  # silent failure

  GOOD:
      try:
          risky_operation()
      except (ValueError, TypeError) as e:
          logger.error("operation failed: %s", e)
          raise  # or handle appropriately


resource cleanup

check for:
  [ ] unclosed files
  [ ] unclosed database connections
  [ ] unclosed network sockets
  [ ] unreleased locks

use context managers:
  with open("file.txt") as f:
      data = f.read()
  # file automatically closed

  with database.transaction():
      # operations
      pass
  # transaction automatically committed/rolled back


validation

check for:
  [ ] input validation at boundaries
  [ ] validation of external data
  [ ] type checking
  [ ] range checking
  [ ] sanitization of user input

examples:

  BAD:
      def process_age(age):
          return age * 365  # what if age is negative?

  GOOD:
      def process_age(age: int) -> int:
          if not isinstance(age, int):
              raise TypeError("age must be an integer")
          if age < 0 or age > 150:
              raise ValueError("age must be between 0 and 150")
          return age * 365


PHASE 6: TESTING REVIEW


test coverage

check for:
  [ ] new code has tests
  [ ] edge cases are tested
  [ ] error paths are tested
  [ ] tests are readable

run coverage:
  <terminal>python -m pytest tests/ --cov=src --cov-report=term-missing</terminal>


test quality

check tests for:
  [ ] clear intent (what is being tested?)
  [ ] independence (no test pollution)
  [ ] meaningful assertions
  [ ] appropriate use of mocks

examples:

  BAD:
      def test_it_works():
          # what does "it" refer to?
          assert function() is not None

  GOOD:
      def test_calculate_discount_returns_zero_for_invalid_code():
          result = calculate_discount("INVALID_CODE", 100)
          assert result == 0


test organization

check for:
  [ ] tests mirror code structure
  [ ] related tests grouped
  [ ] descriptive test names
  [ ] proper fixtures used


PHASE 7: MAINTAINABILITY REVIEW


coupling and cohesion

check for:
  [ ] high coupling (too many dependencies)
  [ ] low cohesion (unrelated things together)
  [ ] circular dependencies

signs of high coupling:
  - changes require touching many files
  - classes know too much about each other
  - difficult to test in isolation


separation of concerns

check for:
  [ ] business logic mixed with I/O
  [ ] UI mixed with business logic
  [ ] data access mixed with business rules
  [ ] configuration mixed with code

layers should be separate:
  - presentation (UI)
  - business logic (domain)
  - data access (persistence)
  - infrastructure


extensibility

check for:
  [ ] hard to change code
  [ ] hard to add features
  [ ] hard to reuse code

good signs:
  - open/closed principle (open for extension, closed for modification)
  - dependency injection
  - strategy pattern for variations
  - hooks/callbacks for customization


technical debt

identify debt:
  [ ] TODO comments (are they stale?)
  [ ] FIXME comments (urgent issues?)
  [ ] HACK comments (workarounds?)
  [ ] commented out code (remove or uncomment)

decide:
  - should this be fixed now?
  - can it be deferred?
  - should there be an issue?


PHASE 8: PROJECT CONVENTIONS REVIEW


style guide compliance

check for:
  [ ] PEP 8 compliance (Python)
  [ ] project-specific style guide
  [ ] consistent formatting
  [ ] consistent naming

run linters:
  <terminal>python -m flake8 src/</terminal>
  <terminal>python -m black --check src/</terminal>
  <terminal>python -m isort --check-only src/</terminal>


import organization

check for:
  [ ] imports grouped (stdlib, third-party, local)
  [ ] imports sorted alphabetically
  [ ] no unused imports
  [ ] no circular imports

correct order:
  # standard library
  import os
  import sys

  # third party
  import requests
  from fastapi import FastAPI

  # local
  from myapp.models import User


dependency management

check for:
  [ ] new dependencies are necessary
  [ ] alternatives in stdlib?
  [ ] dependency is well-maintained
  [ ] license compatibility

check for vulnerabilities:
  <terminal>python -m safety check</terminal>
  <terminal>pip-audit 2>/dev/null || echo "pip-audit not installed"</terminal>


PHASE 9: GIT WORKFLOW REVIEW


commit quality

check commits:
  [ ] are commits atomic (one logical change)?
  [ ] are commit messages clear?
  [ ] is there a single squashed commit?
  [ ] are merge commits avoided?

good commit format:
  type(scope): brief description

  detailed explanation of what and why

  closes #123


PR/branch strategy

check:
  [ ] is branch from main?
  [ ] is PR description clear?
  [ ] are related issues linked?
  [ ] is PR size reasonable?


conflict resolution

check merge commits:
  [ ] are conflicts resolved properly?
  [ ] are conflict markers removed?
  [ ] is the result tested?


PHASE 10: DOCUMENTATION REVIEW


code documentation

check for:
  [ ] docstrings on public functions
  [ ] docstrings on classes
  [ ] docstrings on modules
  [ ] parameter types documented
  [ ] return types documented
  [ ] exceptions documented

google style docstring:
  def calculate_discount(price: float, discount_pct: float) -> float:
      """Calculate discounted price.

      Args:
          price: Original price.
          discount_pct: Discount percentage (0-100).

      Returns:
          Discounted price.

      Raises:
          ValueError: If discount_pct is negative or > 100.
      """


readme and changelog

check:
  [ ] is README updated for user-facing changes?
  [ ] is CHANGELOG updated?
  [ ] are breaking changes documented?
  [ ] are migration guides provided?


inline comments

check for:
  [ ] non-obvious logic explained
  [ ] workarounds documented
  [ ] external references cited
  [ ] TODO/FIXME with context


PHASE 11: GIVING FEEDBACK


feedback principles

be constructive:
  - focus on the code, not the person
  - explain why, not just what
  - suggest improvements, don't just criticize
  - acknowledge good work

be specific:
  - point to exact lines
  - provide examples
  - link to documentation

be prioritized:
  - must fix vs nice to have
  - security/performance vs style


comment templates

for issues:
  [ISSUE] In `file.py:123`, this creates a security vulnerability.
  The problem is that user_input is not sanitized before being used in
  the SQL query. An attacker could inject malicious SQL.

  Consider using parameterized queries instead:
  ```python
  cursor.execute("SELECT * FROM users WHERE name = %s", (user_input,))
  ```

for suggestions:
  [SUGGESTION] In `file.py:456`, we could improve readability by
  extracting this logic into a named function. This would also make
  it easier to test.

for questions:
  [QUESTION] In `file.py:789`, what's the reason for using a list
  comprehension here instead of a generator? Is the intermediate
  list needed?


responding to feedback

as reviewer:
  [ ] be open to discussion
  [ ] admit if you're wrong
  [ ] learn from others
  [ ] be patient with explanations

as author:
  [ ] address each comment
  [ ] explain if not making change
  [ ] thank the reviewer
  [ ] update PR as discussed


PHASE 12: COMMON REVIEW ISSUES


frequently overlooked issues

  [1] off-by-one errors
      loops that skip first/last element
      range() arguments incorrect

  [2] mutation of default arguments
      def foo(items=[]):
          pass  # list is shared across calls!

  [3] missing error handling
      no try/except on risky operations
      no validation of inputs

  [4] resource leaks
      unclosed files/connections
      missing cleanup code

  [5] race conditions
      shared state without locks
      missing atomic operations

  [6] unhandled edge cases
      empty inputs
      None values
      boundary conditions


red flags in code

  - TODO or FIXME comments in production code
  - commented out code (delete or uncomment)
  - large copy-pasted blocks
  - deeply nested code (indent > 4)
  - functions longer than a page
  - many parameters (>5)
  - global variables
  - multiple returns
  - complex boolean expressions
  - magic numbers without explanation


PHASE 13: AUTOMATED REVIEW CHECKS


run linters

flake8 (style and errors):
  <terminal>python -m flake8 src/ --max-line-length=100</terminal>

black (formatting):
  <terminal>python -m black --check src/</terminal>

isort (import sorting):
  <terminal>python -m isort --check-only src/</terminal>

mypy (type checking):
  <terminal>python -m mypy src/</terminal>

pylint (comprehensive):
  <terminal>python -m pylint src/</terminal>


run security scanners

bandit (security issues):
  <terminal>python -m bandit -r src/</terminal>

safety (dependency vulnerabilities):
  <terminal>python -m safety check --full-report</terminal>


run tests

  <terminal>python -m pytest tests/ -v</terminal>

with coverage:
  <terminal>python -m pytest tests/ --cov=src --cov-report=term-missing</terminal>


check for common patterns

grep for potential issues:
  <terminal>grep -r "print(" src/ --include="*.py" | grep -v "# "</terminal>
  <terminal>grep -r "import \*" src/ --include="*.py"</terminal>
  <terminal>grep -r "except:" src/ --include="*.py"</terminal>
  <terminal>grep -r "TODO\|FIXME\|XXX\|HACK" src/ --include="*.py"</terminal>


PHASE 14: REVIEW CHECKLIST


before approval

must have:
  [ ] tests for new functionality
  [ ] tests pass locally
  [ ] linters pass
  [ ] documentation updated
  [ ] changelog updated
  [ ] no secrets committed
  [ ] no debug prints left
  [ ] security review passed
  [ ] performance considered
  [ ] error handling in place


for each file changed

  [ ] purpose is clear
  [ ] implementation is correct
  [ ] style is consistent
  [ ] no obvious bugs
  [ ] error handling present
  [ ] tests are adequate
  [ ] docs are updated


before requesting changes

categorize your comments:
  [MUST] blocking issues (security, bugs, critical)
  [SHOULD] important improvements
  [COULD] optional enhancements
  [NIT] minor style issues (optional to fix)


PHASE 15: CODE REVIEW RULES (MANDATORY)


while this skill is active, these rules are MANDATORY:

  [1] REVIEW THE CHANGE, NOT THE PERSON
      focus on code quality, not coder ability
      be constructive, not critical
      suggest, don't command

  [2] UNDERSTAND BEFORE CRITIQUING
      read the whole change first
      understand the context
      ask questions before judging

  [3] EXPLAIN THE WHY
      don't just say "change this"
      explain the reasoning
      provide examples

  [4] PRIORITIZE ISSUES
      flag security and correctness issues
      note performance concerns
      suggest style improvements separately

  [5] BE SPECIFIC
      point to exact lines
      provide code examples
      link to documentation

  [6] ACKNOWLEDGE GOOD WORK
      note what you like
      appreciate effort
      encourage good patterns

  [7] BE THOROUGH BUT EFFICIENT
      review all changed code
      focus on what matters
      don't bikeshed

  [8] FOLLOW UP
      re-review after changes
      ensure all issues addressed
      close the loop

  [9] LEARN AND TEACH
      share knowledge
      explain concepts
      be open to learning yourself

  [10] DOCUMENT YOUR REVIEW
       keep notes of common issues
       update checklists
       improve the process


FINAL REMINDERS


code review is mentoring

the goal is better code and better developers.
teach and learn in every review.
invest in your team's growth.


quality takes time

a rushed review misses issues.
thorough review prevents bugs later.
the time invested pays dividends.


be human

code review can be stressful.
be kind and constructive.
appreciate the effort behind every PR.


when in doubt

  [ ] ask questions instead of asserting
  [ ] suggest rather than demand
  [ ] discuss in person if needed
  [ ] offer to pair on solutions


the goal

shipping quality software.
building a learning team.
creating shared understanding.
maintaining code health.

now go review some code.
