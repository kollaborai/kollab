---
name: architecture-mapping
description: "Architecture Mapping skill - discover and document system architecture"
---

architecture mapping mode: DISCOVER AND DOCUMENT, DO NOT MODIFY

when this skill is active, you follow systematic architecture investigation.
this is a comprehensive guide to mapping system architecture for documentation.


PHASE 0: ENVIRONMENT AND PREREQUISITES VERIFICATION

before conducting ANY architecture investigation, verify tools are available.


check code analysis tools

  <terminal>which tree</terminal>

if tree not installed:
  <terminal>brew install tree</terminal>
  # macos

  <terminal>apt-get install tree</terminal>
  # debian/ubuntu

  <terminal>dnf install tree</terminal>
  # fedora


check for graph generation tools

  <terminal>which dot</terminal>

if graphviz not installed:
  <terminal>brew install graphviz</terminal>
  # macos

  <terminal>apt-get install graphviz</terminal>
  # debian/ubuntu


check for language-specific analysis tools

python projects:
  <terminal>python -c "import ast; import sys; print('ast ready')"</terminal>

  <terminal>pip list | grep -E "pylint|bandit|radon|pydeps"</terminal>

javascript/node projects:
  <terminal>which node && npm list -g | grep -E "eslint|madge|dependency-cruiser"</terminal>

java projects:
  <terminal>which javap && echo "javap available"</terminal>


verify project access

  <terminal>ls -la</terminal>

  <terminal>find . -maxdepth 2 -type f -name "*.py" -o -name "*.js" -o -name "*.ts" -o -name "*.java" | head -20</terminal>


check for existing documentation

  <terminal>ls -la docs/ 2>/dev/null || echo "no docs directory"</terminal>

  <terminal>find . -maxdepth 3 -type f -name "*README*" -o -name "*ARCHITECTURE*" -o -name "*DESIGN*" 2>/dev/null</terminal>


PHASE 1: IDENTIFYING SYSTEM BOUNDARIES


understanding the scope

first step: define what constitutes "the system"

ask these questions:
  [ ] what is the main entry point?
  [ ] what are the external interfaces?
  [ ] where does system responsibility begin and end?
  [ ] what dependencies are external vs internal?


find entry points

python projects:
  <terminal>find . -maxdepth 3 -type f -name "main.py" -o -name "__main__.py" -o -name "app.py"</terminal>

  <terminal>find . -maxdepth 3 -type f -name "*.py" -exec grep -l "if __name__" {} \;</terminal>

  <terminal>grep -r "def main" . --include="*.py" | head -20</terminal>

javascript/node projects:
  <terminal>find . -maxdepth 3 -type f -name "index.js" -o -name "main.js" -o -name "server.js"</terminal>

  <terminal>cat package.json | grep -A5 '"main"'</terminal>

  <terminal>cat package.json | grep -A10 '"scripts"'</terminal>

java projects:
  <terminal>find . -type f -name "*.class" | head -5</terminal>

  <terminal>find . -type f -name "*.jar" | head -5</terminal>

  <terminal>grep -r "public static void main" . --include="*.java" | head -10</terminal>


find configuration files

configuration files reveal framework choices and boundaries:

  <terminal>find . -maxdepth 3 -type f \( -name "*.toml" -o -name "*.yaml" -o -name "*.yml" -o -name "*.json" -o -name "*.ini" -o -name "*.cfg" \) | grep -v node_modules | grep -v venv | head -20</terminal>

  <terminal>ls -la *.toml *.yaml *.yml *.json *.ini 2>/dev/null</terminal>


identify framework indicators

python frameworks:
  <terminal>grep -r "from fastapi\|import fastapi\|from flask\|import flask\|from django\|import django" . --include="*.py" | head -10</terminal>

  <terminal>grep -r "asyncio\|aiohttp\|tornado" . --include="*.py" | head -10</terminal>

javascript frameworks:
  <terminal>cat package.json | grep -E '"dependencies"|"devDependencies"' -A50 | grep -E '"express"|"koa"|"fastify"|"hapi"|"nest"|"react"|"vue"|"angular"'</terminal>

  <terminal>grep -r "require.*express\|import.*express" . --include="*.js" | head -5</terminal>


PHASE 2: DIRECTORY STRUCTURE ANALYSIS


generate tree structure

  <terminal>tree -L 3 -I 'node_modules|venv|__pycache__|*.pyc|.git' > architecture-directory-tree.txt</terminal>

  <terminal>cat architecture-directory-tree.txt</terminal>


analyze directory patterns

common architectural patterns by directory structure:

mvc pattern:
  src/
    models/      # data structures
    views/       # presentation layer
    controllers/ # request handling
  tests/

layered architecture:
  src/
    controllers/ # external interface
    services/    # business logic
    repositories/# data access
    models/      # domain models
  tests/

microservices:
  service-a/
    src/
      api/
      domain/
      infrastructure/
  service-b/
    src/
      api/
      domain/
      infrastructure/

plugin architecture:
  kollabor/
    plugins/
  plugins/
    plugin-one/
    plugin-two/


analyze module organization

  <terminal>find . -type f -name "*.py" | head -30 | xargs -I{} sh -c 'echo "=== {} ===" && head -20 {}'</terminal>

  <terminal>find . -type f -name "*.py" -exec wc -l {} + | sort -rn | head -20</terminal>


identify public vs private boundaries

  <terminal>find . -type f -name "__init__.py" | head -20</terminal>

  <terminal>find . -type f -name "*.py" -exec grep -l "^def " {} \; | head -20</terminal>

  <terminal>find . -type f -name "*.py" -exec grep -l "^class " {} \; | head -20</terminal>


PHASE 3: COMPONENT IDENTIFICATION


catalog all modules

python:
  <terminal>find . -type f -name "*.py" ! -path "*/tests/*" ! -path "*/venv/*" ! -path "*/__pycache__/*" | sort</terminal>

javascript:
  <terminal>find . -type f -name "*.js" ! -path "*/node_modules/*" ! -path "*/dist/*" ! -path "*/build/*" | sort</terminal>


identify component responsibilities

for each significant module, document:
  [ ] component name
  [ ] primary purpose
  [ ] public interface
  [ ] dependencies
  [ ] data structures

example component catalog format:

  component: UserService
  location: src/services/user_service.py
  purpose: manage user lifecycle and authentication
  public interface:
    - create_user(email, password, metadata)
    - authenticate_user(email, password)
    - get_user(user_id)
    - update_user(user_id, updates)
    - delete_user(user_id)
  dependencies:
    - UserRepository (data access)
    - PasswordHasher (security)
    - EmailService (notifications)
  data structures:
    - User (model)
    - UserCreationRequest (dto)
    - UserResponse (dto)


discover components through imports

python:
  <terminal>grep -r "^import\|^from" . --include="*.py" ! -path "*/tests/*" ! -path "*/venv/*" | grep -v "__pycache__" | sort -u | head -50</terminal>

analyze import patterns to reveal relationships:
  - circular dependencies (bad)
  - abstraction layers
  - shared utilities


identify core vs peripheral components

core components:
  - domain models
  - business logic
  - core services

peripheral components:
  - adapters/integrations
  - ui/presentation
  - infrastructure
  - utilities

  <terminal>find . -type f -name "*.py" -exec grep -l "class.*Service\|class.*Manager\|class.*Controller" {} \; | grep -v test | grep -v venv</terminal>


PHASE 4: DATA FLOW TRACING


trace request flow

web applications - trace incoming request:

  [1] identify web framework entry point
  [2] find route definitions
  [3] trace middleware chain
  [4] identify controller/handler
  [5] trace service layer calls
  [6] identify data access layer
  [7] trace response path

python flask example:
  <terminal>grep -r "@app.route\|@blueprint.route" . --include="*.py" | head -20</terminal>

  <terminal>grep -r "app = Flask(" . --include="*.py"</terminal>

python fastapi example:
  <terminal>grep -r "@app\.\(get\|post\|put\|delete\|patch\)" . --include="*.py" | head -20</terminal>

  <terminal>grep -r "APIRouter(" . --include="*.py" | head -20</terminal>


trace function call chains

for a given entry point:

  <read><file>src/main.py</file></read>

document the call chain:
  main.py -> app.initialize()
                -> load_config()
                -> init_database()
                -> register_routes()
                -> start_server()

  <terminal>grep -r "def initialize\|class.*Manager\|class.*Service" . --include="*.py" | grep -v test | head -30</terminal>


trace data transformations

identify where data changes form:

  [ ] dto to model conversions
  [ ] model to entity conversions
  [ ] serialization/deserialization
  [ ] formatting/presentation layer

  <terminal>grep -r "serialize\|deserialize\|to_dict\|from_dict\|to_model\|from_model" . --include="*.py" | head -20</terminal>


trace database interactions

  <terminal>grep -r "session\|connection\|cursor\|query\|execute" . --include="*.py" | grep -E "SELECT|INSERT|UPDATE|DELETE|CREATE" | head -20</terminal>

python orm tracing:
  <terminal>grep -r "session\.add\|session\.commit\|session\.query\|\.filter\|\.all\(\)\|\.first\(\)" . --include="*.py" | head -30</terminal>


PHASE 5: DEPENDENCY MAPPING


map internal dependencies

direct dependencies:
  <terminal>grep -r "^import\|^from" . --include="*.py" ! -path "*/tests/*" ! -path "*/venv/*" | sed 's/from \(.*\) import.*/\1/' | sort -u</terminal>

  <terminal>grep -r "^import\|^from" . --include="*.py" ! -path "*/tests/*" ! -path "*/venv/*" | sed 's/import \(.*\)/\1/' | sort -u</terminal>


detect circular dependencies

  <terminal>grep -r "^import" . --include="*.py" -A1 | grep -B1 "^--$" | head -40</terminal>

python-specific circular detection:
  <terminal>python -c "
import ast
import sys
from pathlib import Path

def find_imports(filename):
    with open(filename) as f:
        tree = ast.parse(f.read(), filename=filename)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split('.')[0])
    return imports

# check pairs for circular references
"</terminal>


map external dependencies

python:
  <terminal>cat requirements.txt 2>/dev/null || cat pyproject.toml 2>/dev/null | grep -A50 "dependencies"</terminal>

  <terminal>pip list | grep -v "Package\|---"</terminal>

javascript:
  <terminal>cat package.json | grep -A100 '"dependencies"'</terminal>

  <terminal>npm list --depth=0 2>/dev/null</terminal>


categorize dependencies by layer

application layers:
  [ ] presentation (ui, api)
  [ ] business logic (services, domain)
  [ ] data access (repositories, orm)
  [ ] infrastructure (database, messaging, caching)
  [ ] utilities (logging, validation, formatting)


PHASE 6: API INTERFACE DOCUMENTATION


discover rest endpoints

python frameworks:
  <terminal>grep -r "@app\.\|@router\.\|@bp\." . --include="*.py" | grep -E "get|post|put|delete|patch" | head -30</terminal>

  <terminal>grep -r "route(" . --include="*.py" | head -20</terminal>

javascript express:
  <terminal>grep -r "app\.\(get\|post\|put\|delete\|patch\)\|router\.\(get\|post\|put\|delete\|patch\)" . --include="*.js" | head -30</terminal>


document api schema

for each endpoint discovered:
  [ ] http method
  [ ] path/route
  [ ] path parameters
  [ ] query parameters
  [ ] request body schema
  [ ] response schema
  [ ] status codes
  [ ] authentication requirements

example documentation format:

  endpoint: POST /api/users
  handler: UserService.create_user
  request body:
    email: string (required, unique)
    password: string (required, min 8 chars)
    name: string (optional)
  response:
    201: {id: uuid, email: string, name: string, created_at: timestamp}
    400: {error: string, details: array}
    409: {error: "user_exists"}
  authentication: bearer token required


discover message/event interfaces

event-driven systems:

  <terminal>grep -r "emit\|publish\|subscribe\|on(" . --include="*.py" --include="*.js" | head -30</terminal>

  <terminal>grep -r "Event\|Message\|Command\|Query" . --include="*.py" | grep "class " | head -30</terminal>


discover rpc/service interfaces

grpc, thrift, custom rpc:
  <terminal>find . -name "*.proto" -o -name "*.thrift"</terminal>

  <terminal>grep -r "@grpc\|@service\|@rpc" . --include="*.py" --include="*.js" | head -20</terminal>


PHASE 7: ARCHITECTURE PATTERNS RECOGNITION


identify architectural style

monolithic indicators:
  [ ] single codebase
  [ ] shared database
  [ ] direct function calls
  [ ] tight coupling between modules

  <terminal>find . -name "requirements.txt" -o -name "package.json" | wc -l</terminal>

microservices indicators:
  [ ] multiple independent services
  [ ] service-specific databases
  [ ] inter-service communication via api/messaging
  [ ] separate deployments

  <terminal>find . -maxdepth 2 -type d -name "service-*" -o -name "*-service"</terminal>

layered architecture indicators:
  [ ] clear separation of concerns
  [ ] dependency direction follows layers
  [ ] upper layers depend on lower layers only

hexagonal/clean architecture indicators:
  [ ] domain at center, no framework dependencies
  [ ] ports and adapters pattern
  [ ] inward-facing interfaces

event-driven indicators:
  [ ] message bus/event system
  [ ] async processing
  [ ] event sourcing
  [ ] cqrs (command query responsibility segregation)


identify design patterns in use

creational patterns:
  <terminal>grep -r "Factory\|Builder\|Singleton\|Prototype" . --include="*.py" --include="*.js" | grep "class " | head -20</terminal>

structural patterns:
  <terminal>grep -r "Adapter\|Decorator\|Facade\|Proxy\|Bridge" . --include="*.py" --include="*.js" | grep "class " | head -20</terminal>

behavioral patterns:
  <terminal>grep -r "Observer\|Strategy\|Command\|Visitor\|Iterator" . --include="*.py" --include="*.js" | grep "class " | head -20</terminal>


identify integration patterns

  <terminal>grep -r "api\|client\|http\|request\|fetch" . --include="*.py" --include="*.js" | grep -i "def \|class \|function " | head -30</terminal>

integration patterns:
  [ ] api client (http/rest)
  [ ] message queue (rabbitmq, kafka, sqs)
  [ ] database integration
  [ ] file system integration
  [ ] cron/scheduled jobs


PHASE 8: DATA ARCHITECTURE ANALYSIS


identify data storage

databases:
  <terminal>grep -r "sqlite\|postgres\|mysql\|mongodb\|redis\|elasticsearch" . --include="*.py" --include="*.js" --include="*.json" --include="*.yaml" | head -20</terminal>

  <terminal>find . -name "*.db" -o -name "*.sqlite" -o -name "schema.sql"</terminal>

file storage:
  <terminal>grep -r "open(\|file\|path\|Path(" . --include="*.py" | grep -v "test" | head -20</terminal>


map data models

python orm models:
  <terminal>grep -r "class.*\(Base\|Model\|Entity\)" . --include="*.py" | grep -v test | head -30</terminal>

  <terminal>find . -name "models.py" -o -name "entities.py" -o -name "schemas.py" | head -10</terminal>

document each model:
  [ ] model name
  [ ] fields and types
  [ ] constraints
  [ ] relationships
  [ ] indexes


identify data access patterns

active record (database logic in model):
  <terminal>grep -r "save()\|delete()\|update()" . --include="*.py" | grep "class " | head -20</terminal>

repository pattern (separate data access):
  <terminal>find . -name "*repository*.py" -o -name "*dao*.py"</terminal>

  <terminal>grep -r "class.*Repository\|class.*DAO" . --include="*.py" | head -20</terminal>

data mapper (explicit mapper classes):
  <terminal>grep -r "class.*Mapper\|class.*DataMapper" . --include="*.py" | head -20</terminal>


PHASE 9: COMMUNICATION PATTERNS


identify synchronous communication

http/rest:
  <terminal>grep -r "requests\.\|urllib\|httpx\|aiohttp\|fetch" . --include="*.py" | head -20</terminal>

  <terminal>grep -r "axios\|fetch\|XMLHttpRequest" . --include="*.js" | head -20</terminal>

grpc:
  <terminal>find . -name "*.proto"</terminal>

  <terminal>grep -r "grpc\|@grpc" . --include="*.py" --include="*.js" | head -10</terminal>


identify asynchronous communication

message queues:
  <terminal>grep -r "pika\|kafka\|celery\|rq\|bull\|sqs" . --include="*.py" --include="*.js" --include="*.json" | head -20</terminal>

  <terminal>grep -r "publish\|subscribe\|emit\|broadcast" . --include="*.py" --include="*.js" | head -20</terminal>

websockets:
  <terminal>grep -r "websocket\|socket.io\|ws://" . --include="*.py" --include="*.js" | head -20</terminal>

pub/sub:
  <terminal>grep -r "pubsub\|redis.*pub\|event.*bus\|message.*bus" . --include="*.py" --include="*.js" | head -20</terminal>


identify caching strategies

  <terminal>grep -r "redis\|memcache\|cache" . --include="*.py" --include="*.js" | head -20</terminal>

caching patterns:
  [ ] cache-aside (application managed)
  [ ] read-through (cache provider managed)
  [ ] write-through
  [ ] write-behind


PHASE 10: SECURITY ARCHITECTURE


identify authentication mechanisms

  <terminal>grep -r "auth\|login\|jwt\|session\|token\|password" . --include="*.py" --include="*.js" | grep -E "def |class |function " | head -30</terminal>

authentication methods:
  [ ] jwt (json web tokens)
  [ ] session-based
  [ ] oauth/oauth2
  [ ] api keys
  [ ] basic auth
  [ ] custom


identify authorization patterns

  <terminal>grep -r "permission\|role\|access\|authorize\|can_\|may_" . --include="*.py" --include="*.js" | grep -E "def |class |function " | head -30</terminal>

authorization patterns:
  [ ] rbac (role-based access control)
  [ ] abac (attribute-based)
  [ ] acl (access control lists)
  [ ] custom middleware


identify data protection

  <terminal>grep -r "encrypt\|hash\|salt\|bcrypt\|argon2" . --include="*.py" --include="*.js" | head -20</terminal>

  <terminal>grep -r "SECRET\|PASSWORD\|API_KEY\|private" . --include="*.py" --include="*.js" --include="*.env*" | head -20</terminal>


PHASE 11: TEXT-BASED ARCHITECTURE DIAGRAMS


component diagram format

component name diagram:

  [user] -> (web interface) -> [api gateway]
                                      |
                    +-----------------+-----------------+
                    |                 |                 |
                [auth service]   [user service]   [content service]
                    |                 |                 |
                    +-----------------+-----------------+
                                      |
                                  [database layer]
                                      |
                    +-----------------+-----------------+
                    |                 |                 |
              [users db]        [content db]        [cache]


data flow diagram format

request flow example:

  client request
      |
      v
  [load balancer]
      |
      v
  [api gateway]
      |
      +--> [auth middleware] --(valid)--> [route handler]
      |                                       |
      |                                   [service layer]
      |                                       |
      |                                   [repository layer]
      |                                       |
      +--(response)------------------ [database]


deployment diagram format

deployment architecture:

  [user browser]
       |
       v (https)
  [cdn / static files]
       |
       v
  [load balancer]
       |
       +-------+-------+
       |       |       |
       v       v       v
  [app-1] [app-2] [app-3]
       |       |       |
       +-------+-------+
               |
               v
        [primary database]
               |
               v
        [read replica]


sequence diagram format

user authentication flow:

  client           api_gateway        auth_service         database
    |                    |                  |                  |
    |--POST /login------>|                  |                  |
    |                    |                  |                  |
    |                    |--validate------->|                  |
    |                    |                  |                  |
    |                    |                  |--query---------->|
    |                    |                  |                  |
    |                    |                  |--user_data------>|
    |                    |                  |                  |
    |                    |--(token)---------|                  |
    |                    |                  |                  |
    |<--(200 + jwt)------|                  |                  |


PHASE 12: ARCHITECTURE DOCUMENTATION TEMPLATE


system overview template

system: [system name]
version: [version]
last updated: [date]

overview:
  [description of system purpose and scope]

  key characteristics:
    [ ] architectural style (monolith/microservices/etc)
    [ ] primary framework/language
    [ ] main data stores
    [ ] external integrations


component catalog template

core components:

  component: [name]
  file: [location]
  purpose: [what it does]
  type: [service/controller/repository/etc]

  responsibilities:
    - [responsibility 1]
    - [responsibility 2]
    - [responsibility 3]

  public interface:
    - [method/function 1]: [description]
    - [method/function 2]: [description]

  dependencies:
    - [dependency 1]: [how its used]
    - [dependency 2]: [how its used]

  data structures:
    - [structure 1]: [purpose]
    - [structure 2]: [purpose]


data flow template

flow: [flow name]
trigger: [what initiates this flow]

steps:
  [1] [component] -> [action]
  [2] [component] -> [action]
  [3] [component] -> [action]

data transformations:
  - [transformation 1]: [from type] -> [to type]
  - [transformation 2]: [from type] -> [to type]

error handling:
  - [error scenario 1]: [handling approach]
  - [error scenario 2]: [handling approach]


integration catalog template

integration: [name]
type: [api/database/message queue/file]

direction: [inbound/outbound/bidirectional]

protocol:
  - transport: [http/tcp/etc]
  - format: [json/xml/protobuf/etc]
  - authentication: [method]

operations:
  - [operation 1]: [description]
  - [operation 2]: [description]

reliability:
  - retry strategy: [description]
  - timeout: [value]
  - fallback: [description]


PHASE 13: ARCHITECTURE CHECKLIST


structural checklist

component organization:
  [ ] all major components identified
  [ ] component responsibilities documented
  [ ] component interfaces documented
  [ ] dependencies between components mapped
  [ ] circular dependencies noted

layer separation:
  [ ] presentation layer identified
  [ ] business logic layer identified
  [ ] data access layer identified
  [ ] dependency direction documented
  [ ] layer violations noted


data flow checklist

request handling:
  [ ] entry points documented
  [ ] middleware chain documented
  [ ] request routing documented
  [ ] handler functions identified
  [ ] response path documented

data transformation:
  [ ] input validation documented
  [ ] dto/model conversions documented
  [ ] serialization format documented
  [ ] output formatting documented


integration checklist

external systems:
  [ ] all external apis catalogued
  [ ] authentication methods documented
  [ ] rate limits documented
  [ ] error handling documented

data stores:
  [ ] all databases identified
  [ ] schema relationships documented
  [ ] access patterns documented
  [ ] migration strategy noted


security checklist

authentication:
  [ ] auth method documented
  [ ] token storage documented
  [ ] session management documented
  [ ] password handling documented

authorization:
  [ ] access control model documented
  [ ] role definitions documented
  [ ] permission checks noted

data protection:
  [ ] encryption at rest noted
  [ ] encryption in transit noted
  [ ] sensitive data handling documented
  [ ] secrets management documented


PHASE 14: ARCHITECTURE ASSESSMENT


assess coupling

coupling indicators:
  [ ] many imports between modules = high coupling
  [ ] circular dependencies = very high coupling
  [ ] direct database access from ui = tight coupling
  [ ] shared global state = tight coupling

measure coupling:
  <terminal>grep -r "^import\|^from" . --include="*.py" ! -path "*/tests/*" | wc -l</terminal>

  <terminal>for file in $(find . -name "*.py" ! -path "*/venv/*"); do echo "$file: $(grep '^from\|^import' "$file" | wc -l) imports"; done | sort -t: -k2 -rn | head -20</terminal>


assess cohesion

cohesion indicators:
  [ ] module has single clear purpose = high cohesion
  [ ] functions relate to each other = high cohesion
  [ ] mixed concerns in module = low cohesion
  [ ] god objects = low cohesion

identify low cohesion:
  <terminal>find . -name "*.py" -exec wc -l {} \; | sort -rn | head -10</terminal>

  <terminal>find . -name "*.py" -size +50k</terminal>


assess complexity

cyclomatic complexity estimation:
  <terminal>grep -r "if\|elif\|for\|while\|except\|case" . --include="*.py" | wc -l</terminal>

identify complex modules:
  <terminal>for file in $(find . -name "*.py" ! -path "*/tests/*"); do echo "$file: $(grep 'if\|elif\|for\|while\|except' "$file" | wc -l) branches"; done | sort -t: -k2 -rn | head -20</terminal>


assess modifiability

change impact analysis:
  [ ] changing a format affects how many files?
  [ ] adding a field affects how many modules?
  [ ] replacing a dependency requires how many changes?

  <terminal>grep -r "import.*requests\|from requests" . --include="*.py" | wc -l</terminal>

  <terminal>grep -r "class.*Model\|class.*Entity" . --include="*.py" | head -20</terminal>


PHASE 15: DOCUMENTING ARCHITECTURE DECISIONS


record architectural decisions

use adr (architecture decision record) format:

adr template:

  title: [decision title]
  status: [proposed/accepted/deprecated/superseded]
  date: [yyyy-mm-dd]
  context: [what situation required this decision]
  decision: [what was decided]
  consequences: [positive and negative outcomes]

example:

  title: use postgresql as primary database
  status: accepted
  date: 2024-01-15
  context: application requires relational data with complex queries
              and transactions. team has postgresql experience.
  decision: postgresql will be the primary data store.
  consequences:
    positive: familiar to team, excellent query capabilities
    positive: good tooling and monitoring ecosystem
    negative: requires separate database server
    negative: vertical scaling required for write volume


identify decision points

look for evidence of decisions:
  [ ] framework selection
  [ ] database choice
  [ ] architecture pattern
  [ ] integration approaches
  [ ] deployment strategy

  <terminal>find . -name "requirements.txt" -o -name "package.json" -o -name "pom.xml"</terminal>

  <terminal>find . -name "docker-compose.yml" -o -name "Dockerfile" -o -name "kubernetes"</terminal>


PHASE 16: RESEARCH RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] NEVER modify code during architecture mapping
      this is research only
      document findings, do not change implementation

  [2] use ONLY <terminal> and <read> tags
      no <edit> or <create> tags allowed
      observation, not modification

  [3] document everything discovered
      if you find a component, document it
      if you find a pattern, document it
      if you find an issue, document it

  [4] verify before documenting
      trace imports to confirm dependencies
      trace function calls to confirm data flow
      trace file reads to confirm data access

  [5] maintain structural hierarchy
      start with big picture
      drill into specific areas
      maintain context between levels

  [6] use concrete evidence
      quote actual code in findings
      show actual directory structure
      provide actual file locations

  [7] distinguish observation from interpretation
      clearly separate what you see from what you think it means
      label assumptions as such


PHASE 17: ARCHITECTURE MAPPING SESSION CHECKLIST


before starting:

  [ ] code analysis tools available
  [ ] project directory accessible
  [ ] output directory created for findings
  [ ] existing documentation reviewed
  [ ] entry point identified


during investigation:

  [ ] directory structure documented
  [ ] all source files catalogued
  [ ] entry points identified
  [ ] framework determined
  [ ] major components catalogued
  [ ] component responsibilities documented
  [ ] dependencies mapped
  [ ] data flows traced
  [ ] interfaces documented
  [ ] integrations catalogued
  [ ] security mechanisms noted
  [ ] architectural patterns identified


for each component:

  [ ] file location
  [ ] purpose and responsibility
  [ ] public interface
  [ ] dependencies (incoming and outgoing)
  [ ] data structures used
  [ ] integration points


for each data flow:

  [ ] trigger/initiator
  [ ] processing steps
  [ ] data transformations
  [ ] storage operations
  [ ] response path
  [ ] error handling


after completing:

  [ ] all findings documented
  [ ] diagrams created (text-based)
  [ ] component catalog complete
  [ ] integration catalog complete
  [ ] decision records drafted
  [ ] assumptions listed
  [ ] gaps in documentation noted


FINAL REMINDERS


architecture mapping is forensic work

you are a detective, not an architect.
observe, document, analyze.
do not judge, do not suggest changes.
let the evidence speak.


documentation is for humans

write clearly.
use examples.
show context.
explain connections.

future developers will thank you.


when in doubt

document more rather than less.
capture the uncertainty.
label your assumptions.
leave breadcrumbs for the next investigator.


the goal

complete understanding of how the system works.
comprehensive documentation for future work.
evidence base for architectural decisions.

now go discover and document.
