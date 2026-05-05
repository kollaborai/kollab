---
name: tutorial-creation
description: "Tutorial Creation skill - write effective step-by-step tutorials"
---

tutorial-creation mode: LEARN BY DOING

when this skill is active, you follow tutorial writing best practices.
this is a comprehensive guide to creating effective technical tutorials.


PHASE 0: AUDIENCE ANALYSIS

before writing ANY tutorial, understand who will read it.


identify target audience

ask these questions:

  [ ] what is their skill level?
      beginner - needs explanations of basic concepts
      intermediate - knows basics, needs practical application
      advanced - needs optimization and edge cases

  [ ] what is their goal?
      learn a new technology
      solve a specific problem
      build a complete project
      integrate with existing system

  [ ] what do they already know?
      programming languages
      frameworks or tools
      domain knowledge
      related technologies

  [ ] what is their learning style?
      hands-on learners prefer code examples
      conceptual learners prefer explanations
      visual learners prefer diagrams

  [ ] how much time do they have?
      quick tutorial: 15-30 minutes
      medium tutorial: 1-2 hours
      long tutorial: half-day or multiple sessions


create audience persona

define a specific reader:

  target audience:
    - role: junior developer
    - experience: 1-2 years
    - knows: python basics, git fundamentals
    - doesn't know: web frameworks, api design
    - goal: build first rest api
    - available time: ~2 hours

write for this persona specifically.
when you try to write for everyone, you write for no one.


check for existing tutorials

  <terminal>find docs -name "*tutorial*" -o -name "*guide*" 2>/dev/null</terminal>
  <terminal>find docs -name "*getting*started*" 2>/dev/null</terminal>
  <terminal>ls docs/tutorials/ 2>/dev/null || echo "no tutorials directory"</terminal>

read existing tutorials to understand:
  - format and structure in use
  - writing style and tone
  - what topics are covered
  - what's missing

fill gaps, don't duplicate.


check for related documentation

  <terminal>find docs -name "README*" -o -name "architecture*" -o -name "api*" 2>/dev/null</terminal>

link to related docs:
  - API reference
  - architecture documentation
  - conceptual guides
  - troubleshooting guides

tutorials should complement, not replace, reference docs.


PHASE 1: TUTORIAL OBJECTIVES

define clear, measurable learning objectives.


define the goal

what will the reader be able to DO after completing the tutorial?

  good objectives:
    - build a rest api with user authentication
    - deploy a serverless function to aws
    - create a real-time chat application
    - integrate payment processing

  bad objectives:
    - learn about frameworks (too vague)
    - understand the system (not actionable)
    - know everything (impossible)


write SMART objectives

  [ ] Specific - exact skill to be learned
  [ ] Measurable - can test if achieved
  [ ] Achievable - realistic for the time
  [ ] Relevant - valuable to the reader
  [ ] Time-bound - fits in promised duration

  example:
    after this tutorial, you will:
      [ ] create a fastapi project from scratch
      [ ] define 5 rest endpoints with proper http methods
      [ ] implement jwt authentication
      [ ] write tests for all endpoints
      [ ] deploy to a cloud platform


define prerequisites

be explicit about what readers need before starting:

  prerequisites:
    knowledge:
      [ ] python programming basics
      [ ] understanding of http methods (get, post, put, delete)
      [ ] basic git commands

    tools:
      [ ] python 3.11 or later installed
      [ ] code editor (vs code, pycharm, etc.)
      [ ] git installed
      [ ] postman or similar api testing tool

    accounts:
      [ ] github account
      [ ] free tier cloud account

  setup instructions for prerequisites:
    <terminal>python --version</terminal>
    <terminal>git --version</terminal>

  if any check fails, provide setup links.


define scope

what will and won't be covered:

  in scope:
    - building a rest api with fastapi
    - sqlite database with sqlalchemy
    - jwt authentication
    - docker containerization
    - basic deployment

  out of scope:
    - production database setup
    - advanced authentication (oauth, 2fa)
    - caching strategies
    - monitoring and logging
    - ci/cd pipelines

  link to resources for out-of-scope topics.


PHASE 2: TUTORIAL STRUCTURE

effective tutorials follow a proven structure.


the tutorial outline

  title: [clear, action-oriented]
  subtitle: [what will be built and how long it takes]

  introduction:
    - what you'll build
    - why it matters
    - who it's for
    - what you'll learn
    - time estimate

  prerequisites:
    - knowledge needed
    - tools required
    - setup instructions

  overview:
    - high-level architecture
    - components to be built
    - final result preview

  steps (progressive):
    1. project setup
    2. basic implementation
    3. core functionality
    4. advanced features
    5. testing
    6. deployment

  summary:
    - what was accomplished
    - next steps
    - related tutorials


introduction section

hook the reader immediately:

  what you'll build
  ---------------
  in this tutorial, you'll build a complete rest api for a task
  management application. users will be able to:

    - create accounts and authenticate
    - create, read, update, and delete tasks
    - organize tasks by project
    - set due dates and priorities

  by the end, you'll have a production-ready api deployed to the cloud.

  why this matters
  ---------------
  building apis is a fundamental skill for backend development.
  the patterns you'll learn here apply to any api project:

    - proper endpoint design
    - authentication and authorization
    - data validation
    - error handling
    - testing and deployment

  who this is for
  --------------
  this tutorial is for developers who:

    - know python basics
    - want to learn backend development
    - need to build apis for their projects
    - have 2-3 hours to complete it

  what you'll learn
  -----------------
    - fastapi framework fundamentals
    - database modeling with sqlalchemy
    - jwt authentication implementation
    - input validation and error handling
    - unit and integration testing
    - containerization with docker
    - cloud deployment basics


PHASE 3: THE FIRST STEP

the first step determines if readers continue.


project setup

make setup foolproof with verification:

  step 1: create the project
  ---------------------------

  create a new directory for your project:

    <terminal>mkdir task-api</terminal>
    <terminal>cd task-api</terminal>

  create a virtual environment:

    <terminal>python -m venv venv</terminal>

  activate it:

    on mac/linux:
      <terminal>source venv/bin/activate</terminal>

    on windows:
      <terminal>venv\Scripts\activate</terminal>

  verify activation:
    <terminal>which python</terminal>

  you should see the path to your venv python, not the system python.


install dependencies

  create a requirements.txt file:

    <create>
    <file>requirements.txt</file>
    <content>
fastapi==0.109.0
uvicorn[standard]==0.27.0
sqlalchemy==2.0.25
pydantic==2.5.3
pydantic-settings==2.1.0
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-multipart==0.0.6
pytest==7.4.4
httpx==0.26.0
    </content>
    </create>

  install dependencies:

    <terminal>pip install -r requirements.txt</terminal>

  verify installation:

    <terminal>pip list | grep fastapi</terminal>
    <terminal>pip list | grep uvicorn</terminal>


create project structure

  <terminal>mkdir -p app/{api,core,models,schemas,services}</terminal>
  <terminal>mkdir -p tests</terminal>

  your directory should now look like:

    task-api/
    ├── app/
    │   ├── api/          # api endpoints
    │   ├── core/         # configuration
    │   ├── models/       # database models
    │   ├── schemas/      # pydantic schemas
    │   └── services/     # business logic
    ├── tests/            # tests
    └── requirements.txt

  verify structure:

    <terminal>ls -r app/</terminal>


create a hello world

start with something that works:

  create the main application file:

    <create>
    <file>app/main.py</file>
    <content>
from fastapi import fastapi

app = fastapi(title="task api")

@app.get("/")
def read_root():
    return {"message": "welcome to task api"}
    </content>
    </create>

  run the application:

    <terminal>uvicorn app.main:app --reload</terminal>

  you should see:

    info:     started server process
    info:     waiting for application startup.
    info:     application startup complete.
    info:     uvicorn running on http://127.0.0.1:8000

  open http://127.0.0.1:8000 in your browser.

  you should see:
    {"message": "welcome to task api"}

  [ok] if you see this message, you're ready to continue!
  [x] if you see an error, check the troubleshooting section below.


verify each step

after every step, provide a verification:

  verification:
    <terminal>curl http://127.0.0.1:8000/</terminal>

  expected output:
    {"message":"welcome to task api"}

  [ ] if this works, continue to the next step
  [ ] if not, see troubleshooting below


troubleshooting for step 1

  problem: "module not found" error
  solution: make sure virtual environment is activated
    <terminal>source venv/bin/activate</terminal>
    <terminal>pip install -r requirements.txt</terminal>

  problem: port 8000 already in use
  solution: use a different port
    <terminal>uvicorn app.main:app --reload --port 8001</terminal>

  problem: command not found: uvicorn
  solution: install uvicorn
    <terminal>pip install uvicorn[standard]</terminal>


PHASE 4: BUILDING PROGRESSIVELY

each step builds on the previous.


complexity progression

  step 1: hello world (5 min)
    - verify setup works

  step 2: single endpoint (10 min)
    - create one read endpoint
    - understand the request/response cycle

  step 3: full crud (20 min)
    - create, read, update, delete operations
    - understand http methods

  step 4: database integration (20 min)
    - connect to database
    - persist data

  step 5: data models (15 min)
    - define proper schema
    - add relationships

  step 6: validation (15 min)
    - input validation
    - error handling

  step 7: authentication (20 min)
    - user registration
    - login and tokens

  step 8: authorization (10 min)
    - protect endpoints
    - user-specific data

  step 9: testing (20 min)
    - unit tests
    - integration tests

  step 10: deployment (15 min)
    - containerize
    - deploy to cloud

total: ~2.5 hours


building - each endpoint

when teaching a new concept:

  [1] explain what we're building
  [2] show the code
  [3] explain how it works
  [4] run and verify
  [5] explain what could go wrong

  example - adding a create endpoint:

  step 4: create tasks
  -------------------

  we need an endpoint to create tasks. let's add it.

  add the pydantic schema for task creation:

    <edit>
    <file>app/schemas/tasks.py</file>
    <find>from pydantic import basemodel</find>
    <replace>
from pydantic import basemodel, fieldvalidator
from datetime import datetime
from enum import enum

class priority(str, enum):
    low = "low"
    medium = "medium"
    high = "high"

class taskcreate(basemodel):
    title: str
    description: str | none = none
    priority: priority = priority.medium
    due_date: datetime | none = none

    @fieldvalidator("title")
    @classmethod
    def title_not_empty(cls, v):
        if not v or not v.strip():
            raise valueerror("title cannot be empty")
        return v.strip()
    </replace>
    </edit>

  add the endpoint:

    <edit>
    <file>app/api/tasks.py</file>
    <find>@app.get("/tasks")</find>
    <replace>
@app.post("/tasks", status_code=201)
def create_task(task: taskcreate):
    """create a new task."""
    new_task = {
        "id": len(tasks) + 1,
        **task.model_dump(),
        "created_at": datetime.now(),
        "completed": false
    }
    tasks.append(new_task)
    return new_task

@app.get("/tasks")</replace>
    </edit>

  how it works:
    - the @post decorator defines a post endpoint
    - status_code=201 returns "created" status
    - fastapi validates the request against taskcreate schema
    - invalid requests return 422 with validation errors
    - we return the created task with its new id

  test it:

    <terminal>curl -x post http://127.0.0.1:8000/tasks \
      -h "content-type: application/json" \
      -d '{"title": "write documentation", "priority": "high"}'</terminal>

  response:
    {
      "id": 1,
      "title": "write documentation",
      "description": null,
      "priority": "high",
      "due_date": null,
      "created_at": "2024-01-25t10:30:00",
      "completed": false
    }

  [ok] if you see the created task, continue!
  [x] if you see an error, check below.


error responses

show what happens with invalid input:

  test validation:
    <terminal>curl -x post http://127.0.0.1:8000/tasks \
      -h "content-type: application/json" \
      -d '{"title": ""}'</terminal>

  response:
    {
      "detail": [
        {
          "loc": ["body", "title"],
          "msg": "title cannot be empty",
          "type": "value_error"
        }
      ]
    }

  the validation automatically protects your endpoint from bad data.
  this is one of fastapi's superpowers.


PHASE 5: CHECKPOINTS AND VERIFICATION

readers need to verify progress.


checkpoint structure

after each major section, add a checkpoint:

  checkpoint: basic api working
  ----------------------------

  at this point, you should have:
    [ ] a running fastapi server
    [ ] a working /tasks endpoint (get)
    [ ] a working /tasks endpoint (post)
    [ ] data validation on create

  verify everything works:

    <terminal>curl http://127.0.0.1:8000/tasks</terminal>

    <terminal>curl -x post http://127.0.0.1:8000/tasks \
      -h "content-type: application/json" \
      -d '{"title": "test task"}'</terminal>

  expected results:
    - first call returns the list of tasks
    - second call returns the new task

  [ ] if both work, continue to the next section
  [ ] if not, review the previous steps


save your progress

encourage readers to commit after each checkpoint:

  git checkpoint:

    <terminal>git add .</terminal>
    <terminal>git commit -m "add basic task endpoints"</terminal>

  this creates a recovery point.
  if something breaks later, you can always return.


PHASE 6: EXPLAINING CONCEPTS

balance theory and practice.


the concept sandwich

  [1] show what we're building (motivation)
  [2] explain the concept briefly (theory)
  [3] show the code (practice)
  [4] explain how the code implements the concept
  [5] show it working (verification)

  example - explaining authentication:

  why authentication matters:
  ---------------
  right now, anyone can create, read, or delete tasks.
  we need to know who is making requests so:
    - users only see their own tasks
    - users can't delete other users' tasks
    - we can track who created what

  how jwt authentication works:
  ---------------
  jwt (json web token) authentication works like this:

    1. user sends credentials (email/password)
    2. server verifies credentials
    3. server creates a token with user info
    4. server signs the token with a secret key
    5. server sends token back to user
    6. user includes token in subsequent requests
    7. server verifies token signature
    8. server extracts user info from token

  the token is stateless - the server doesn't need to store sessions.
  it just verifies the signature is valid.

  implementing authentication:
  ---------------
  [code implementation...]

  testing authentication:
  ---------------
  [verification steps...]


concept depth guidelines

  how much to explain?

  for beginners:
    - explain the concept fully
    - use analogies
    - show diagrams
    - explain every line of code

  for intermediate:
    - explain the key concepts
    - focus on why, not just what
    - explain non-obvious code
    - link to deeper resources

  for advanced:
    - brief concept overview
    - focus on implementation details
    - discuss trade-offs
    - show alternatives


diagrams and visuals

create ascii diagrams for clarity:

  request flow:
  -------------
  client --> nginx --> fastapi --> database
    |         |         |          |
    |         |         |          +---> postgresql
    |         |         |
    |         |         +---> business logic
    |         |               |
    |         |               +---> validation
    |         |               +---> authorization
    |         |
    |         +---> ssl termination
    |
    +---> browser/postman


authentication flow:
-------------------

login:
  user                api                database
   |                   |                    |
   |--creds----------->|                    |
   |                   |--verify----------->|
   |                   |<------found--------|
   |                   |                    |
   |<---token----------|                    |
   |                   |                    |

authenticated request:
  user                api
   |                   |
   |--request+token--->|
   |                   |--verify signature
   |                   |--extract user id
   |<---response-------|


PHASE 7: CODE QUALITY IN TUTORIALS

tutorial code should be production-quality.


best practices in examples

  [ ] type hints
      def create_user(user: usercreate) -> user:
          ...

  [ ] validation
      use pydantic for input validation
      never trust user input

  [ ] error handling
      return proper error codes
      include helpful error messages

  [ ] security
      hash passwords
      validate input
      use environment variables for secrets

  [ ] testing
      show how to test what you build
      include test examples

  [ ] documentation
      docstrings for functions
      comments for complex logic


what to skip in tutorials

for brevity, you can skip:

  [ ] extensive logging
      mention it exists, don't show every log line

  [ ] comprehensive error handling
      show the pattern, don't handle every edge case

  [ ] full test coverage
      show test examples, don't test every case

  [ ] production configuration
      show development config, mention production needs

  add a note when skipping:

  note: in production, you would also want:
    - structured logging
    - more comprehensive error handling
    - additional monitoring
    - rate limiting


PHASE 8: TESTING SECTION

teach readers to test their work.


unit testing

show how to test individual components:

  step 9: testing
  --------------

  first, install test dependencies:

    <terminal>pip install pytest pytest-cov httpx</terminal>

  create a test file:

    <create>
    <file>tests/test_tasks.py</file>
    <content>
import pytest
from fastapi.testclient import testclient
from app.main import app

client = testclient(app)

def test_create_task():
    """test creating a new task."""
    response = client.post(
        "/tasks",
        json={
            "title": "test task",
            "priority": "high"
        }
    )

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "test task"
    assert data["priority"] == "high"
    assert "id" in data

def test_create_task_with_empty_title():
    """test that empty title is rejected."""
    response = client.post(
        "/tasks",
        json={"title": ""}
    )

    assert response.status_code == 422
    assert "title" in response.json()["detail"][0]["loc"]

def test_get_tasks():
    """test retrieving the task list."""
    # first create a task
    client.post("/tasks", json={"title": "test task"})

    # then get all tasks
    response = client.get("/tasks")

    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    </content>
    </create>

  run the tests:

    <terminal>pytest tests/test_tasks.py -v</terminal>

  expected output:
    tests/test_tasks.py::test_create_task passed
    tests/test_tasks.py::test_create_task_with_empty_title passed
    tests/test_tasks.py::test_get_tasks passed

  [ok] if all tests pass, your api is working correctly!
  [x] if any test fails, review the error and fix your code


integration testing

show how to test the full flow:

  <create>
  <file>tests/test_integration.py</file>
  <content>
import pytest
from fastapi.testclient import testclient
from app.main import app

client = testclient(app)

def test_full_task_workflow():
    """test complete create-read-update-delete workflow."""
    # create
    response = client.post(
        "/tasks",
        json={"title": "integration test task"}
    )
    assert response.status_code == 201
    task_id = response.json()["id"]

    # read
    response = client.get(f"/tasks/{task_id}")
    assert response.status_code == 200
    assert response.json()["title"] == "integration test task"

    # update
    response = client.put(
        f"/tasks/{task_id}",
        json={"title": "updated task", "completed": true}
    )
    assert response.status_code == 200
    assert response.json()["completed"] is true

    # delete
    response = client.delete(f"/tasks/{task_id}")
    assert response.status_code == 200

    # verify deleted
    response = client.get(f"/tasks/{task_id}")
    assert response.status_code == 404
  </content>
  </create>


PHASE 9: DEPLOYMENT

help readers get their work into the world.


deployment checklist

  [ ] containerize the application
  [ ] configure environment variables
  [ ] set up database
  [ ] deploy to platform
  [ ] verify deployment
  [ ] set up monitoring


docker deployment

  step 10: deploy
  --------------

  create a dockerfile:

    <create>
    <file>dockerfile</file>
    <content>
from python:3.11-slim

workdir /app

copy requirements.txt .
run pip install --no-cache-dir -r requirements.txt

copy . .

expose 8000

cmd ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    </content>
    </create>

  build the image:

    <terminal>docker build -t task-api .</terminal>

  run the container:

    <terminal>docker run -p 8000:8000 task-api</terminal>

  verify it's running:

    <terminal>curl http://127.0.0.1:8000/</terminal>


cloud deployment

provide at least one cloud deployment option:

  deploy to railway (simple):

    <terminal>npm install -g railway</terminal>
    <terminal>railway login</terminal>
    <terminal>railway init</terminal>
    <terminal>railway up</terminal>

  or deploy to render:

    1. create a render.com account
    2. create new web service
    3. connect your github repository
    4. render auto-deploys on push

  add a render.yaml to your repo:

    <create>
    <file>render.yaml</file>
    <content>
services:
  - type: web
    name: task-api
    runtime: docker
    plan: free
    envvars:
      - key: database_url
        fromdatabase:
          name: task-db
          property: connectionstring
databases:
  - name: task-db
    databaseName: taskdb
    user: taskuser
  </content>
    </create>


PHASE 10: COMMON PITFALLS

help readers avoid mistakes.


pitfall warnings

add warning boxes for common mistakes:

  warning: don't commit secrets
  --------------------------
  never commit api keys, passwords, or tokens to git.

  instead, use environment variables:

    <create>
    <file>.env</file>
    <content>
    database_url=postgresql://user:pass@localhost/dbname
    secret_key=your-secret-key-here
    </content>
    </create>

  and add .env to .gitignore:

    <terminal>echo ".env" >> .gitignore</terminal>

  warning: default secret keys
  --------------------------
  never use default secret keys in production.

  generate a secure key:

    <terminal>python -c "import secrets; print(secrets.token_urlsafe(32))"</terminal>

  warning: sqlite in production
  ---------------------------
  sqlite is fine for development but not for production.
  use postgresql or mysql for production deployments.


PHASE 11: TROUBLESHOOTING

help readers when things go wrong.


common issues

  common issues:
  -------------

  issue: "address already in use" error
  solution: another process is using port 8000
    <terminal>lsof -i :8000</terminal>
    <terminal>kill -9 <pid></terminal>

  issue: "module not found: fastapi"
  solution: make sure virtual environment is activated
    <terminal>source venv/bin/activate</terminal>
    <terminal>pip install -r requirements.txt</terminal>

  issue: tests pass but curl fails
  solution: make sure the server is running
    <terminal>uvicorn app.main:app --reload</terminal>

  issue: database errors after restart
  solution: make sure database migration ran
    <terminal>alembic upgrade head</terminal>


debug mode section

show how to enable debug output:

  enable debug logging:

    <edit>
    <file>app/main.py</file>
    <find>import logging</find>
    <replace>
import logging

logging.basicConfig(level=logging.debug)
logger = logging.getlogger(__name__)
    </replace>
    </edit>

  add debug endpoints:

    @app.get("/debug/health")
    def health_check():
        return {
            "status": "healthy",
            "database": db_connected(),
            "redis": cache_connected()
        }


PHASE 12: NEXT STEPS

keep readers learning after the tutorial.


continue learning

suggest what to learn next:

  next steps:
  ----------

  [ ] add more features
      - task comments
      - file attachments
      - task assignments
      - notifications

  [ ] improve the api
      - pagination for large datasets
      - filtering and sorting
      - full-text search
      - rate limiting

  [ ] harden the application
      - add comprehensive logging
      - implement caching with redis
      - set up monitoring
      - add ci/cd pipeline

  [ ] related tutorials
      - building a frontend for your api
      - advanced authentication with oauth
      - websocket real-time updates
      - microservices architecture


resources

link to further learning:

  official documentation:
    - fastapi docs: https://fastapi.tiangolo.com
    - sqlalchemy docs: https://docs.sqlalchemy.org
    - pytest docs: https://docs.pytest.org

  related tutorials:
    - building a graphql api with fastapi
    - docker and docker compose deep dive
    - testing strategies for python apis

  community:
    - fastapi discord server
    - r/fastapi on reddit
    - stack overflow tag: fastapi


PHASE 13: TUTORIAL QUALITY CHECKLIST


before publishing, verify:

content:
  [ ] title is clear and action-oriented
  [ ] introduction explains value
  [ ] prerequisites are explicit
  [ ] objectives are measurable
  [ ] scope is well-defined

code:
  [ ] all code examples are tested
  [ ] code follows best practices
  [ ] code is properly formatted
  [ ] file paths are clear
  [ ] copy-paste works

structure:
  [ ] logical flow from start to finish
  [ ] each step builds on previous
  [ ] checkpoints to verify progress
  [ ] troubleshooting for common issues

verification:
  [ ] each section has verification step
  [ ] expected outputs are shown
  [ ] errors are explained
  [ ] solutions are provided

accessibility:
  [ ] language is clear and direct
  [ ] jargon is explained
  [ ] examples are relatable
  [ ] multiple learning styles supported

completeness:
  [ ] tutorial can be completed in stated time
  [ ] no steps are skipped
  [ ] all commands are provided
  [ ] all files are shown


PHASE 14: TUTORIAL WRITING RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] TEST EVERY CODE EXAMPLE
      if you haven't run it, don't include it
      broken examples destroy trust

  [2] START WITH WORKING CODE
      the first step must produce something that works
      immediate success builds confidence

  [3] VERIFY EACH STEP
      after every step, show how to verify it works
      readers should know if they're on track

  [4] PROVIDE SOLUTIONS
      for every problem you identify, provide a solution
      never leave readers stuck

  [5] BE EXPLICIT ABOUT PREREQUISITES
      list exactly what readers need before starting
      don't assume knowledge

  [6] USE CONCRETE EXAMPLES
      build something real, not abstract
      task api is better than "example api"

  [7] PROGRESSIVE COMPLEXITY
      start simple, add complexity gradually
      no sudden jumps in difficulty

  [8] CHECKPOINTS AFTER EACH SECTION
      readers must be able to verify their progress
      provide git commits or verification commands

  [9] INCLUDE TROUBLESHOOTING
      anticipate common mistakes
      provide solutions

  [10] WRITE FOR ONE PERSON
      define your audience persona
      write specifically for them
      don't try to please everyone


PHASE 15: TUTORIAL TEMPLATES


quick tutorial template (15-30 min)

  title: [verb] [noun] in [timeframe]
  subtitle: [what you'll accomplish]

  in this tutorial, you'll [specific outcome].

  prerequisites:
    - [requirement 1]
    - [requirement 2]

  step 1: [first thing to do]
  [setup/initial work]

  step 2: [second thing]
  [core functionality]

  step 3: [third thing]
  [finishing touches]

  summary:
  you now have [what was built].
  next: [what to do next]


medium tutorial template (1-2 hours)

  title: [verb] [complete project]
  subtitle: [description] | [time]

  what you'll build:
  [project description with features]

  what you'll learn:
  - [learning objective 1]
  - [learning objective 2]
  - [learning objective 3]

  prerequisites:
  [detailed prerequisites with setup instructions]

  part 1: setup
  [environment setup]

  part 2: basics
  [core functionality]

  part 3: features
  [main features]

  part 4: polish
  [refinement and testing]

  part 5: deploy
  [deployment steps]

  what's next:
  [continuation options]


long tutorial template (half-day)

  title: [comprehensive project]
  subtitle: a complete guide to [topic]

  about this tutorial:
  [full overview with time estimates]

  part 1: foundation (1 hour)
  [fundamental concepts and setup]

  part 2: core (2 hours)
  [main implementation]

  part 3: advanced (1.5 hours)
  [advanced features]

  part 4: production (1 hour)
  [deployment and monitoring]

  summary and next steps


PHASE 16: MEASURING TUTORIAL SUCCESS


metrics to track

  quantitative:
    - completion rate
    - time to complete
    - errors encountered
    - questions asked

  qualitative:
    - reader confidence
    - understanding of concepts
    - satisfaction with result
    - likelihood to recommend


feedback collection

add feedback prompts:

  how did this tutorial go?
  -------------------------
  [ ] great! i learned a lot
  [ ] good, but i got stuck a few times
  [ ] confusing, needs improvement

  what was confusing?
  ___________________
  [text area for feedback]

  what would make this better?
  ____________________________
  [text area for suggestions]


iterate based on feedback

track common sticking points:

  if many readers fail at step 5:
    - break step 5 into smaller steps
    - add more explanation
    - add verification checkpoints

  if many readers skip a section:
    - maybe it's not needed
    - make it optional
    - move to separate tutorial


FINAL REMINDERS


tutorials are for learning

the goal isn't just to complete the tutorial.
the goal is to learn the concepts.
focus on understanding, not just following steps.


build confidence

each step should increase confidence.
early wins create momentum.
verifiable progress creates trust.


empathize with the reader

remember when you were learning.
what confused you?
  explain that.
what helped you understand?
  include that.

the reader is smart but inexperienced.
write for them.


when in doubt

add a verification step.
if the reader doesn't know if they succeeded,
they'll lose confidence.
show them how to check their work.

now go teach someone something new.
