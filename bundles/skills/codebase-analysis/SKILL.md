---
name: codebase-analysis
description: "Codebase Analysis skill - systematic investigation of unfamiliar codebases"
---

codebase-analysis mode: READ ONLY INVESTIGATION

when this skill is active, you follow systematic codebase exploration.
this is a comprehensive guide to analyzing unfamiliar software projects.


PHASE 0: ENVIRONMENT AND PROJECT TYPE VERIFICATION

before analyzing ANY codebase, identify the project type and tools.


identify primary language

  <terminal>head -20 README.md 2>/dev/null || echo "no README found"</terminal>

  <terminal>find . -maxdepth 2 -name "*.py" -o -name "*.js" -o -name "*.ts" -o -name "*.go" -o -name "*.rs" -o -name "*.java" -o -name "*.rb" -o -name "*.php" | head -20</terminal>

  <terminal>ls -la | grep -E "\.(py|js|ts|go|rs|java|rb|php)$"</terminal>


detect package manager

  python projects:
    <terminal>ls -la | grep -E "(requirements\.txt|pyproject\.toml|setup\.py|Pipfile|poetry\.lock|pyproject\.toml)"</terminal>

  javascript/node projects:
    <terminal>ls -la | grep -E "(package\.json|package-lock\.json|yarn\.lock|pnpm-lock\.yaml)"</terminal>

  rust projects:
    <terminal>ls -la | grep -E "Cargo\.toml"</terminal>

  go projects:
    <terminal>ls -la | grep -E "go\.mod"</terminal>

  java/maven projects:
    <terminal>ls -la | grep -E "pom\.xml"</terminal>

  java/gradle projects:
    <terminal>ls -la | grep -E "build\.gradle"</terminal>


detect framework

  python web frameworks:
    <terminal>grep -r "flask\|django\|fastapi\|tornado\|aiohttp" --include="*.py" . 2>/dev/null | head -5</terminal>

  javascript frameworks:
    <terminal>grep -r "react\|vue\|angular\|svelte\|next\|nuxt" package.json 2>/dev/null | head -5</terminal>

  <terminal>cat package.json 2>/dev/null | grep -E "(express|koa|hapi|nest)"</terminal>


detect build system

  <terminal>ls -la | grep -E "(Makefile|CMake|build\.sh|webpack\.config|vite\.config|tsconfig\.json)"</terminal>

  <terminal>cat Makefile 2>/dev/null | head -20</terminal>

  <terminal>cat pyproject.toml 2>/dev/null | grep -A5 "\[build-system\]"</terminal>


verify project structure

  <terminal>ls -la</terminal>

  <terminal>tree -L 2 -I 'node_modules|venv|__pycache__|target|.git' 2>/dev/null || find . -maxdepth 2 -type d | grep -v -E "(node_modules|venv|__pycache__|target|.git|\.pytest_cache)" | head -30</terminal>

  <terminal>find . -maxdepth 1 -type d | sort</terminal>


detect version control

  <terminal>ls -la .git 2>/dev/null && echo "git repo" || echo "no git"</terminal>

  <terminal>git remote -v 2>/dev/null | head -5</terminal>

  <terminal>git log --oneline -10 2>/dev/null</terminal>


detect testing framework

  python:
    <terminal>find . -name "test_*.py" -o -name "*_test.py" | head -10</terminal>
    <terminal>grep -r "pytest\|unittest\|nose" --include="*.py" . 2>/dev/null | head -5</terminal>

  javascript:
    <terminal>grep -E "(jest|mocha|jasmine|vitest|cypress)" package.json 2>/dev/null</terminal>

  <terminal>ls -la __tests__ tests/ spec/ 2>/dev/null</terminal>


PHASE 1: ENTRY POINTS AND EXECUTION FLOW


identify main entry points

  python projects:
    <terminal>find . -name "main.py" -o -name "__main__.py" -o -name "app.py" -o -name "run.py" | head -10</terminal>

    <terminal>grep -r "if __name__" --include="*.py" . 2>/dev/null | head -10</terminal>

  javascript projects:
    <terminal>cat package.json 2>/dev/null | grep -A3 '"bin"'</terminal>
    <terminal>cat package.json 2>/dev/null | grep -A3 '"main"'</terminal>
    <terminal>find . -name "index.js" -o -name "server.js" -o -name "app.js" | head -10</terminal>

  rust:
    <terminal>cat Cargo.toml 2>/dev/null | grep -A5 "\[bin\]"</terminal>
    <terminal>find src -name "main.rs"</terminal>

  go:
    <terminal>find . -name "main.go"</terminal>


identify cli entry points

  <terminal>cat pyproject.toml 2>/dev/null | grep -A10 "\[project\.scripts\]\|\[project\.entry-points\]"</terminal>

  <terminal>cat setup.py 2>/dev/null | grep -A10 "entry_points"</terminal>

  <terminal>grep -r "argparse\|click\|typer\|fire" --include="*.py" . 2>/dev/null | head -10</terminal>


identify web server entry points

  python:
    <terminal>grep -r "app\.run\|uvicorn\|gunicorn\|waitress" --include="*.py" . 2>/dev/null | head -10</terminal>

    <terminal>grep -r "@app\.route\|@router\|FastAPI\|Flask" --include="*.py" . 2>/dev/null | head -10</terminal>

  javascript:
    <terminal>grep -r "app\.listen\|server\.listen" --include="*.js" . 2>/dev/null | head -10</terminal>

    <terminal>cat package.json 2>/dev/null | grep -A5 '"scripts"'</terminal>


trace execution flow

  step 1: read the main entry file
    <read><file>PATH_TO_MAIN_FILE</file></read>

  step 2: identify imports and dependencies
    <terminal>grep "^import\|^from" PATH_TO_MAIN_FILE 2>/dev/null</terminal>

  step 3: find where initialization happens
    <terminal>grep -n "def main\|class.*App\|if __name__" PATH_TO_MAIN_FILE 2>/dev/null</terminal>

  step 4: trace the call chain
    identify the first function called
    identify what classes are instantiated
    identify what configuration is loaded


document the execution flow template

  entry point: [file and line where execution starts]

  initialization sequence:
    [1] [what happens first]
    [2] [what happens second]
    [3] [what happens third]

  key modules loaded:
    - [module name] - [purpose]
    - [module name] - [purpose]

  main loop/event handler:
    [location of main event loop or request handler]


PHASE 2: PROJECT STRUCTURE MAPPING


map directory structure

  <terminal>find . -type d -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "*/venv/*" -not -path "*/__pycache__/*" -not -path "*/target/*" -not -path "*/\.pytest_cache/*" | head -50</terminal>

  <terminal>tree -L 3 -I 'node_modules|venv|__pycache__|target|.git|dist|build' 2>/dev/null || echo "tree not installed"</terminal>


categorize directories by purpose

  source code locations:
    <terminal>find . -type d -name "src" -o -name "lib" -o -name "app" -o -name "core" -o -name "server"</terminal>

  test locations:
    <terminal>find . -type d -name "test*" -o -name "spec*" -o -name "__tests__"</terminal>

  config locations:
    <terminal>find . -type d -name "config" -o -name "settings" -o -name "conf"</terminal>

  documentation locations:
    <terminal>find . -type d -name "doc*" -o -name "examples"</terminal>


analyze file distribution

  python:
    <terminal>find . -name "*.py" -not -path "*/venv/*" -not -path "*/__pycache__/*" | wc -l</terminal>

    <terminal>find . -name "*.py" -not -path "*/venv/*" -not -path "*/__pycache__/*" -exec wc -l {} + | sort -rn | head -20</terminal>

  javascript:
    <terminal>find . -name "*.js" -not -path "*/node_modules/*" -not -path "*/dist/*" | wc -l</terminal>

    <terminal>find . -name "*.ts" -not -path "*/node_modules/*" | wc -l</terminal>

  identify largest files:
    <terminal>find . -name "*.py" -o -name "*.js" -o -name "*.ts" | grep -v node_modules | xargs wc -l 2>/dev/null | sort -rn | head -20</terminal>


identify code organization patterns

  pattern: feature-based
    src/
      auth/
      database/
      api/
      utils/

  pattern: layer-based
    src/
      models/
      controllers/
      services/
      repositories/

  pattern: platform-based
    frontend/
    backend/
    shared/

  identify which pattern this project follows:
    <terminal>ls -la src/ 2>/dev/null || ls -la app/ 2>/dev/null || ls -la lib/ 2>/dev/null</terminal>


PHASE 3: DEPENDENCY GRAPH ANALYSIS


analyze direct dependencies

  python - requirements.txt:
    <read><file>requirements.txt</file></read>

  python - pyproject.toml:
    <terminal>cat pyproject.toml 2>/dev/null | grep -A20 "\[project\.dependencies\]"</terminal>

  javascript:
    <terminal>cat package.json 2>/dev/null | grep -A50 '"dependencies"'</terminal>

  rust:
    <terminal>cat Cargo.toml 2>/dev/null | grep -A50 "\[dependencies\]"</terminal>


identify external library usage

  python:
    <terminal>grep -rh "^import\|^from" --include="*.py" src/ app/ 2>/dev/null | sort | uniq -c | sort -rn | head -30</terminal>

    what to look for:
      - web frameworks (flask, django, fastapi)
      - database clients (sqlalchemy, pymongo, psycopg2)
      - auth libraries (authlib, pyjwt, passlib)
      - utilities (requests, click, python-dotenv)
      - data processing (pandas, numpy)

  javascript:
    <terminal>grep -rh "^import\|^const.*require" --include="*.js" --include="*.ts" src/ 2>/dev/null | sort | uniq -c | sort -rn | head -30</terminal>


identify internal module coupling

  python:
    <terminal>grep -rh "^from \.\|^from .*import" --include="*.py" src/ 2>/dev/null | sed 's/from //' | sed 's/ import.*//' | sort | uniq -c | sort -rn</terminal>

  find which modules are imported most:
    <terminal>grep -r "^from" --include="*.py" . 2>/dev/null | grep -v "from \." | grep -v "from __future__" | cut -d: -f2 | cut -d' ' -f2 | cut -d'.' -f1 | sort | uniq -c | sort -rn | head -20</terminal>


identify circular dependencies risk

  look for:
    - modules that import each other
    - top-level imports that may cause issues

  python check:
    <terminal>grep -r "^import" --include="*.py" . 2>/dev/null | grep -v "__pycache__" | cut -d: -f1 | xargs -I{} basename {} .py | sort | uniq -c | sort -rn | head -20</terminal>


document dependency insights

  critical external dependencies:
    - [library] - [version] - [purpose] - [alternatives]

  internal hot spots:
    - [module] - imported by [n] other modules

  coupling concerns:
    - [module a] <-> [module b] potential circular dependency
    - [module] - high coupling, may need refactoring

  dependency health:
    - [ ] dependencies actively maintained
    - [ ] no deprecated dependencies in use
    - [ ] dependency versions are compatible


PHASE 4: CONFIGURATION ANALYSIS


locate configuration files

  <terminal>find . -maxdepth 2 -type f \( -name "*.env*" -o -name "*config*" -o -name "*settings*" -o -name "*.yaml" -o -name "*.yml" -o -name "*.toml" -o -name "*.ini" \) | grep -v node_modules | head -20</terminal>

  <terminal>ls -la | grep -E "\.(env|yaml|yml|toml|ini|json)$"</terminal>

  <terminal>find . -name ".env*" -o -name "config.*" -o -name "settings.*" | head -20</terminal>


analyze configuration patterns

  environment variable usage:
    <terminal>grep -rh "os\.getenv\|os\.environ\|process\.env" --include="*.py" --include="*.js" --include="*.ts" . 2>/dev/null | sort | uniq | head -30</terminal>

  <terminal>grep -rh "getenv\|environ" --include="*.py" . 2>/dev/null | grep -v "__pycache__" | sort | uniq | head -30</terminal>


identify configuration schema

  <read><file>config.py 2>/dev/null || settings.py 2>/dev/null || src/config.py 2>/dev/null</file></read>

  look for:
    - configuration classes
    - default values
    - validation logic
    - environment override patterns


document configuration findings

  configuration locations:
    - [file] - [purpose]

  environment variables used:
    - [VAR_NAME] - [purpose] - [default value if any]

  configuration patterns:
    - [pattern used, e.g., 12-factor app, singleton config, etc.]


PHASE 5: DATA MODEL ANALYSIS


identify data models

  python:
    <terminal>find . -name "models.py" -o -name "model.py" -o -name "schemas.py" -o -name "types.py" | head -10</terminal>

    <terminal>grep -r "class.*\(Model\|Base\)" --include="*.py" . 2>/dev/null | grep -v "__pycache__" | head -30</terminal>

    <terminal>grep -r "@dataclass\|from pydantic\|from sqlalchemy" --include="*.py" . 2>/dev/null | head -20</terminal>

  javascript/typescript:
    <terminal>grep -r "interface\|type.*=" --include="*.ts" --include="*.tsx" . 2>/dev/null | head -30</terminal>

    <terminal>grep -r "mongoose\.Schema\|sequelize\.define" --include="*.js" --include="*.ts" . 2>/dev/null | head -20</terminal>


analyze model relationships

  read key model files:
    <read><file>PATH_TO_MODELS</file></read>

  identify:
    - one-to-many relationships
    - many-to-many relationships
    - foreign keys
    - cascade behaviors

  python orm hints:
    <terminal>grep -r "relationship\|ForeignKey\|back_populates\|backref" --include="*.py" . 2>/dev/null | head -30</terminal>


identify database schema

  <terminal>find . -name "schema.sql" -o -name "migrations" -o -name "alembic"</terminal>

  <terminal>ls -la migrations/ 2>/dev/null || echo "no migrations directory"</terminal>

  <terminal>find . -name "*.sql" | head -10</terminal>


document data model

  core entities:
    - [entity name] - [purpose] - [key fields]

  relationships:
    - [entity a] -> [entity b] - [relationship type]

  data persistence:
    - database: [postgresql/mysql/sqlite/mongodb/etc]
    - orm: [sqlalchemy/django orm/mongoose/etc]
    - migration tool: [alembic/flyway/etc]


PHASE 6: API AND INTERFACE ANALYSIS


identify api endpoints

  rest apis:
    <terminal>grep -r "@app\.route\|@bp\.route\|@router\|@.*\.get\|@.*\.post" --include="*.py" . 2>/dev/null | head -30</terminal>

    <terminal>grep -r "app\.(get|post|put|delete|patch)" --include="*.js" --include="*.ts" . 2>/dev/null | head -30</terminal>

  graphql:
    <terminal>find . -name "*.graphql" -o -name "schema.graphql" | head -10</terminal>

    <terminal>grep -r "type Query\|type Mutation" --include="*.graphql" --include="*.ts" --include="*.js" . 2>/dev/null | head -20</terminal>


map api structure

  <terminal>grep -rh "@.*\.route\|@.*\.get\|@.*\.post" --include="*.py" . 2>/dev/null | sort</terminal>

  <terminal>grep -rh "router\.(get|post|put|delete|patch)" --include="*.ts" --include="*.js" . 2>/dev/null | sort</terminal>

  for each endpoint found:
    - read the handler function
    - identify request format
    - identify response format
    - identify authentication requirements


identify authentication/authorization

  <terminal>grep -r "@login_required\|@auth\|authenticate\|authorize" --include="*.py" --include="*.js" --include="*.ts" . 2>/dev/null | head -20</terminal>

  <terminal>grep -r "jwt\|oauth\|session\|cookie" --include="*.py" --include="*.js" . 2>/dev/null | grep -v "__pycache__" | grep -v node_modules | head -30</terminal>


identify middleware and interceptors

  <terminal>grep -r "middleware\|interceptor\|before_request\|after_request" --include="*.py" --include="*.js" . 2>/dev/null | head -20</terminal>

  <terminal>find . -name "*middleware*" | grep -v node_modules</terminal>


document api surface

  api base url: [from config or main file]

  public endpoints:
    - [method] [path] - [description]

  authenticated endpoints:
    - [method] [path] - [auth type] - [description]

  webhooks/events:
    - [path] - [trigger]

  middleware chain:
    [1] [middleware name] - [purpose]
    [2] [middleware name] - [purpose]


PHASE 7: ERROR HANDLING AND LOGGING


analyze error handling patterns

  python:
    <terminal>grep -r "try:\|except\|raise " --include="*.py" . 2>/dev/null | wc -l</terminal>

    <terminal>grep -r "except Exception" --include="*.py" . 2>/dev/null | head -20</terminal>

    <terminal>grep -r "class.*Error\|class.*Exception" --include="*.py" . 2>/dev/null | head -30</terminal>

  javascript:
    <terminal>grep -r "try {\|catch\|throw new" --include="*.js" --include="*.ts" . 2>/dev/null | wc -l</terminal>


identify custom exceptions

  <terminal>find . -name "exceptions.py" -o -name "errors.py" -o -name "error.py"</terminal>

  <read><file>PATH_TO_EXCEPTIONS_FILE</file></read>

  document:
    - exception hierarchy
    - when each exception is raised
    - how exceptions are handled globally


analyze logging patterns

  python:
    <terminal>grep -r "import logging\|logger\." --include="*.py" . 2>/dev/null | head -30</terminal>

    <terminal>grep -rh "logger\.(debug|info|warning|error|critical)" --include="*.py" . 2>/dev/null | sort | uniq -c | sort -rn</terminal>

  javascript:
    <terminal>grep -r "console\.(log|warn|error|debug)" --include="*.js" --include="*.ts" . 2>/dev/null | wc -l</terminal>

    <terminal>grep -r "winston\|pino\|bunyan" --include="*.js" --include="*.ts" . 2>/dev/null | head -20</terminal>


identify log configuration

  <terminal>find . -name "logging.conf" -o -name "*log*.json" -o -name "*log*.yaml"</terminal>

  <terminal>grep -r "basicConfig\|getLogger\|dictConfig" --include="*.py" . 2>/dev/null | head -20</terminal>


document error handling

  error handling strategy:
    - [global exception handler location]
    - [error reporting format]

  logging configuration:
    - [log levels used]
    - [log destinations: file/stdout/service]
    - [log format]

  custom exceptions:
    - [exception name] - [when raised]


PHASE 8: TESTING COVERAGE ANALYSIS


identify test files

  <terminal>find . -name "test_*.py" -o -name "*_test.py" -o -name "*.test.js" -o -name "*.spec.js" -o -name "*.spec.ts" | grep -v node_modules | head -30</terminal>

  <terminal>find . -type d -name "test*" -o -name "spec*" -o -name "__tests__" | head -10</terminal>


analyze test structure

  <terminal>ls -la tests/ 2>/dev/null || ls -la test/ 2>/dev/null || ls -la __tests__/ 2>/dev/null</terminal>

  <terminal>find tests/ -type f -name "*.py" 2>/dev/null | wc -l</terminal>

  <terminal>grep -r "def test_\|describe\|it(" tests/ 2>/dev/null | wc -l</terminal>


identify test utilities and fixtures

  python:
    <terminal>find tests/ -name "conftest.py" -o -name "fixtures.py" -o -name "utils.py" -o -name "helpers.py"</terminal>

    <terminal>grep -r "@pytest\.fixture\|@fixture" tests/ 2>/dev/null | head -30</terminal>

  javascript:
    <terminal>find tests/ -name "setup.*" -o -name "helpers.*" -o -name "mocks.*"</terminal>


identify test doubles and mocks

  <terminal>grep -r "mock\|stub\|spy" tests/ 2>/dev/null | head -30</terminal>

  <terminal>grep -r "mocker\|patch\|unittest\.mock" tests/ 2>/dev/null | head -20</terminal>


analyze test coverage

  <terminal>python -m pytest --collect-only 2>/dev/null | tail -5</terminal>

  <terminal>python -m pytest tests/ --collect-only -q 2>/dev/null | tail -3</terminal>

  check if coverage is configured:
    <terminal>grep -r "pytest-cov\|coverage" pyproject.toml setup.cfg pytest.ini 2>/dev/null</terminal>


document test coverage

  test framework: [pytest/unittest/jest/mocha/etc]

  test locations:
    - [directory] - [type of tests]

  test counts:
    - unit tests: [approximately]
    - integration tests: [approximately]
    - e2e tests: [approximately]

  coverage status:
    - coverage tool: [yes/no]
    - coverage percentage: [if available]

  testing gaps:
    - [modules without tests]
    - [scenarios not covered]


PHASE 9: ASYNC AND CONCURRENCY PATTERNS


identify async code

  python:
    <terminal>grep -r "async def\|await " --include="*.py" . 2>/dev/null | wc -l</terminal>

    <terminal>grep -r "asyncio\|aiohttp\|asyncpg" --include="*.py" . 2>/dev/null | head -20</terminal>

  javascript:
    <terminal>grep -r "async.*function\|await " --include="*.js" --include="*.ts" . 2>/dev/null | wc -l</terminal>

    <terminal>grep -r "Promise\|\.then(" --include="*.js" --include="*.ts" . 2>/dev/null | head -20</terminal>


identify threading and multiprocessing

  python:
    <terminal>grep -r "Thread\|Process\|Pool\|Queue\|Lock\|Semaphore" --include="*.py" . 2>/dev/null | head -30</terminal>

    <terminal>grep -r "@threaded\|@synchronized" --include="*.py" . 2>/dev/null | head -10</terminal>

  javascript:
    <terminal>grep -r "Worker\|cluster\|child_process" --include="*.js" --include="*.ts" . 2>/dev/null | head -20</terminal>


identify event loop patterns

  python:
    <terminal>grep -r "loop\.run\|asyncio\.run\|loop\.create_task" --include="*.py" . 2>/dev/null | head -20</terminal>

    <terminal>grep -r "asyncio\.gather\|asyncio\.create_task" --include="*.py" . 2>/dev/null | head -20</terminal>


identify shared state and locks

  <terminal>grep -r "global\|_instance\|singleton" --include="*.py" . 2>/dev/null | head -30</terminal>

  <terminal>grep -r "Lock\|RLock\|Semaphore\|Event\|Condition" --include="*.py" . 2>/dev/null | head -20</terminal>


document concurrency patterns

  async framework: [asyncio/tornado/etc]

  async entry points:
    - [function] - [location]

  concurrency risks:
    - [shared mutable state identified]
    - [potential race conditions]

  event loop:
    - [where event loop is created]
    - [how tasks are scheduled]


PHASE 10: CODE PATTERNS AND ANTI-PATTERNS


identify design patterns

  creational patterns:
    <terminal>grep -r "class.*Factory\|def create_" --include="*.py" . 2>/dev/null | head -20</terminal>

    <terminal>grep -r "class.*Builder\|class.*Singleton" --include="*.py" . 2>/dev/null | head -20</terminal>

  structural patterns:
    <terminal>grep -r "class.*Adapter\|class.*Decorator\|class.*Proxy" --include="*.py" . 2>/dev/null | head -20</terminal>

  behavioral patterns:
    <terminal>grep -r "class.*Observer\|class.*Strategy\|class.*Command" --include="*.py" . 2>/dev/null | head -20</terminal>


identify code smells

  long functions:
    <terminal>find . -name "*.py" -exec awk '/^def /{if(NR>200)print FILENAME":"NR":"NR-200}' {} \; 2>/dev/null | head -20</terminal>

  duplicated code indicators:
    <terminal>find . -name "*.py" -exec wc -l {} + | sort -rn | head -20</terminal>

  god objects:
    <terminal>find . -name "*.py" -exec awk 'NF>0 && /^class /{name=$2} /^def /{count[name]++} END{for(n in count)if(count[n]>20)print n, count[n]}' {} \; 2>/dev/null</terminal>


identify architectural patterns

  <terminal>find . -type d -name "service*" -o -name "repository*" -o -name "controller*" -o -name "view*"</terminal>

  <terminal>grep -r "class.*Service\|class.*Repository\|class.*Controller" --include="*.py" . 2>/dev/null | head -30</terminal>


document patterns found

  design patterns:
    - [pattern name] - [location] - [purpose]

  code quality concerns:
    - [smell type] - [location] - [severity]

  architectural pattern:
    - [mvc/mvp/mvvm/layered/hexagonal/etc]


PHASE 11: DOCUMENTATION ASSESSMENT


assess documentation quality

  <terminal>find . -name "README*" -o -name "CONTRIBUTING*" -o -name "CHANGELOG*" -o -name "ARCHITECTURE*" | head -20</terminal>

  <terminal>find . -name "*.md" -type f | grep -v node_modules | head -20</terminal>

  <terminal>ls -la docs/ 2>/dev/null || echo "no docs directory"</terminal>


analyze code documentation

  python docstrings:
    <terminal>grep -r '"""' --include="*.py" . 2>/dev/null | wc -l</terminal>

    <terminal>grep -r "^def " --include="*.py" . 2>/dev/null | wc -l</terminal>

    calculate docstring coverage ratio

  type hints:
    <terminal>grep -r " -> \|: int\|: str\|: List\|: Dict" --include="*.py" . 2>/dev/null | head -30</terminal>

  javascript jsdoc/tsg:
    <terminal>grep -r "/\*\*" --include="*.js" --include="*.ts" . 2>/dev/null | wc -l</terminal>


identify api documentation

  <terminal>find . -name "swagger.*" -o -name "openapi.*" -o -name "*.yaml" -o -name "*.json" | grep -E "(api|doc|spec)" | head -10</terminal>

  <terminal>grep -r "@swagger\|@api.doc" --include="*.py" . 2>/dev/null | head -20</terminal>


document documentation status

  user documentation:
    - [ ] README with installation
    - [ ] usage examples
    - [ ] contributing guidelines
    - [ ] changelog

  developer documentation:
    - [ ] architecture overview
    - [ ] api documentation
    - [ ] development setup
    - [ ] code comments/docstrings

  documentation coverage:
    - docstrings: [estimated percentage]
    - type hints: [yes/partial/no]


PHASE 12: SECURITY ASSESSMENT


identify security mechanisms

  authentication:
    <terminal>grep -r "authenticate\|login\|password\|credential" --include="*.py" --include="*.js" . 2>/dev/null | grep -v "__pycache__" | grep -v node_modules | head -20</terminal>

  authorization:
    <terminal>grep -r "authorize\|permission\|role\|access" --include="*.py" --include="*.js" . 2>/dev/null | grep -v "__pycache__" | grep -v node_modules | head -20</terminal>

  encryption:
    <terminal>grep -r "encrypt\|decrypt\|cipher\|crypto" --include="*.py" --include="*.js" . 2>/dev/null | grep -v "__pycache__" | grep -v node_modules | head -20</terminal>


identify sensitive data handling

  <terminal>grep -ri "secret\|password\|api_key\|token" --include="*.py" --include="*.js" --include="*.env*" . 2>/dev/null | grep -v node_modules | grep -v "__pycache__" | head -30</terminal>

  <terminal>find . -name ".env*" -o -name "*secret*" -o -name "*credential*" | grep -v node_modules</terminal>


identify input validation

  <terminal>grep -r "validate\|sanitize\|escape\|clean" --include="*.py" --include="*.js" . 2>/dev/null | grep -v node_modules | head -30</terminal>

  <terminal>grep -r "pydantic\|cerberus\|marshmallow\|joi" --include="*.py" --include="*.js" . 2>/dev/null | head -20</terminal>


identify sql injection risks

  <terminal>grep -r "execute.*%s\|execute.*format\|execute.*+" --include="*.py" . 2>/dev/null | head -20</terminal>

  <terminal>grep -r "SELECT.*FROM.*\(format\|%\|+\)" --include="*.py" --include="*.js" . 2>/dev/null | head -20</terminal>


document security findings

  authentication:
    - method: [jwt/session/oauth/etc]
    - implementation: [library/location]

  authorization:
    - model: [rbac/acl/abac/etc]
    - implementation: [location]

  security concerns:
    - [potential vulnerability] - [location] - [severity]

  sensitive data:
    - [type] - [storage method]


PHASE 13: PERFORMANCE CONSIDERATIONS


identify caching mechanisms

  <terminal>grep -r "cache\|Cache" --include="*.py" --include="*.js" . 2>/dev/null | grep -v node_modules | grep -v "__pycache__" | head -30</terminal>

  <terminal>grep -r "redis\|memcached\|lru_cache" --include="*.py" . 2>/dev/null | head -20</terminal>


identify database query patterns

  <terminal>grep -r "SELECT.*\*" --include="*.py" . 2>/dev/null | head -20</terminal>

  <terminal>grep -r "\.filter\|\.where\|\.find" --include="*.py" . 2>/dev/null | head -30</terminal>

  <terminal>grep -r "N+1\|eager\|preload\|joinedload" --include="*.py" . 2>/dev/null | head -20</terminal>


identify expensive operations

  <terminal>grep -r "for.*in.*range\|while.*:" --include="*.py" . 2>/dev/null | head -30</terminal>

  <terminal>grep -r "time\.sleep\|asyncio\.sleep" --include="*.py" . 2>/dev/null | head -20</terminal>


identify connection pooling

  <terminal>grep -r "pool\|Pool\|connection" --include="*.py" . 2>/dev/null | head -30</terminal>

  <terminal>grep -r "engine\.connect\|session\.begin" --include="*.py" . 2>/dev/null | head -20</terminal>


document performance findings

  caching:
    - [type] - [location] - [ttl]

  database optimization:
    - [indexes used]
    - [n+1 query risks]

  performance concerns:
    - [concern] - [location] - [impact]


PHASE 14: BUILD AND DEPLOYMENT


identify build process

  <terminal>cat Makefile 2>/dev/null | head -30</terminal>

  <terminal>cat package.json 2>/dev/null | grep -A10 '"scripts"'</terminal>

  <terminal>cat pyproject.toml 2>/dev/null | grep -A10 "\[build-system\]"</terminal>


identify ci/cd configuration

  <terminal>find .github -name "*.yml" -o -name "*.yaml" 2>/dev/null</terminal>

  <terminal>find .gitlab-ci.yml .gitlab-ci.yml docker-compose.yml Dockerfile 2>/dev/null</terminal>

  <terminal>cat .github/workflows/*.yml 2>/dev/null | head -50</terminal>


identify deployment configuration

  <terminal>find . -name "Dockerfile*" -o -name "docker-compose*" -o -name "kubernetes*" -o -name "helm*" | head -10</terminal>

  <terminal>find . -name "*.tf" -o -name "terraform*" | head -10</terminal>

  <terminal>cat Dockerfile 2>/dev/null | head -30</terminal>


identify environment configuration

  <terminal>find . -name ".env*" -o -name "*production*" -o -name "*staging*" | head -20</terminal>

  <terminal>grep -r "NODE_ENV\|FLASK_ENV\|DJANGO_SETTINGS\|ENVIRONMENT" --include="*.py" --include="*.js" . 2>/dev/null | head -20</terminal>


document build and deployment

  build process:
    - [steps to build]

  ci/cd:
    - [platform] - [pipeline file]

  deployment:
    - [method] - [configuration]

  environments:
    - [development]
    - [staging]
    - [production]


PHASE 15: ANALYSIS REPORT TEMPLATE


use this template to structure your findings:


project: [name]

overview:
  language: [primary language(s)]
  framework: [main framework(s)]
  purpose: [what the project does]
  size: [lines of code, file count]


entry points:
  main: [file and line]
  cli: [if applicable]
  web: [if applicable]
  worker: [if applicable]


architecture:
  pattern: [architectural pattern identified]
  layers:
    - [layer] - [directory] - [purpose]
  key modules:
    - [module] - [purpose] - [dependencies]


data model:
  database: [type]
  orm: [if applicable]
  key entities:
    - [entity] - [fields] - [relationships]


api surface:
  base: [url/path]
  public endpoints:
    - [method] [path] - [description]
  authenticated endpoints:
    - [method] [path] - [description]


dependencies:
  critical:
    - [library] - [purpose] - [version]
  internal:
    - [hot spots]
    - [coupling concerns]


testing:
  framework: [name]
  coverage: [percentage if known]
  gaps:
    - [areas without tests]


code quality:
  patterns:
    - [design patterns found]
  concerns:
    - [code smells identified]
  documentation:
    - [quality assessment]


security:
  auth: [method]
  concerns:
    - [potential issues]


performance:
  caching: [strategy]
  concerns:
    - [potential bottlenecks]


deployment:
  build: [process]
  ci/cd: [platform]
  environments: [list]


recommendations:
  [1] [high priority improvement]
  [2] [medium priority improvement]
  [3] [low priority improvement]


PHASE 16: ANALYSIS RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] NEVER modify files
      research agent only reads and reports
      use <read> and <terminal> tags only
      no <edit> or <create> tags

  [2] verify before assuming
      use commands to confirm hypotheses
      don't guess based on file names
      read actual content to understand

  [3] document everything
      keep structured notes
      use the report template
      include file paths and line numbers

  [4] follow the dependency chain
      start from entry points
      trace imports and calls
      understand before judging

  [5] identify patterns, not just problems
      recognize intentional architecture
      distinguish between style and bug
      note both good and bad patterns

  [6] be thorough
      explore all directories
      check configuration files
      read documentation

  [7] stay objective
      report findings neutrally
      provide evidence for claims
      note uncertainty


FINAL REMINDERS


codebase analysis is reconnaissance

you are mapping terrain, not building roads.
understanding precedes recommendation.


context is everything

a pattern that is wrong in one context
may be intentional in another.
consider the project's constraints.


the report is your output

a clear, structured report enables action.
include evidence for every claim.
provide file paths and line numbers.


you are the first step

analysis leads to planning.
planning leads to implementation.
your thoroughness enables quality.

now go explore some code.
