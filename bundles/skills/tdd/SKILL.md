---
name: tdd
description: "Test-Driven Development skill - write tests first, then implementation"
---

tdd mode: TESTS FIRST, CODE SECOND

when this skill is active, you follow strict TDD discipline.
this is a comprehensive guide to professional test-driven development.


PHASE 0: ENVIRONMENT VERIFICATION

before writing ANY code, verify the testing environment is ready.


check testing framework

  <terminal>python -m pytest --version</terminal>

if pytest not installed:
  <terminal>pip install pytest pytest-cov pytest-mock pytest-asyncio</terminal>

verify installation:
  <terminal>python -c "import pytest; print('pytest ready')"</terminal>


check project structure

  <terminal>ls -la</terminal>
  <terminal>ls -la tests/ 2>/dev/null || echo "no tests directory"</terminal>

if no tests directory:
  <terminal>mkdir -p tests</terminal>
  <create>
  <file>tests/__init__.py</file>
  <content>
  """Test suite for the project."""
  </content>
  </create>

  <create>
  <file>tests/conftest.py</file>
  <content>
  """Pytest configuration and shared fixtures."""
  import pytest

  # add fixtures here as needed
  </content>
  </create>


check for existing test configuration

  <terminal>cat pytest.ini 2>/dev/null || cat pyproject.toml 2>/dev/null | grep -A20 "\[tool.pytest"</terminal>

if no pytest config exists, create one:
  <create>
  <file>pytest.ini</file>
  <content>
  [pytest]
  testpaths = tests
  python_files = test_*.py
  python_classes = Test*
  python_functions = test_*
  addopts = -v --tb=short
  </content>
  </create>


check for coverage tools

  <terminal>python -m coverage --version 2>/dev/null || echo "coverage not installed"</terminal>

if not installed:
  <terminal>pip install pytest-cov coverage</terminal>


check existing test patterns in codebase

  <terminal>find . -name "test_*.py" -type f | head -10</terminal>
  <terminal>grep -r "def test_" tests/ 2>/dev/null | head -20</terminal>
  <terminal>grep -r "import pytest\|from pytest" tests/ 2>/dev/null | head -5</terminal>

understand existing patterns before adding new tests.
match the style already in use.


verify tests can run

  <terminal>python -m pytest tests/ --collect-only 2>&1 | head -20</terminal>

if collection errors, fix them before proceeding.


PHASE 1: THE TDD CYCLE

the fundamental rhythm of TDD:

  RED    -> write a failing test
  GREEN  -> write minimal code to pass
  REFACTOR -> clean up while tests stay green

this cycle repeats for every piece of functionality.
never skip steps. never write code before the test.


the red phase

purpose: define what the code SHOULD do before it exists.

requirements:
  [1] test must fail
  [2] test must fail for the RIGHT reason
  [3] test must be specific and focused

write the test:
  <create>
  <file>tests/test_feature.py</file>
  <content>
  """Tests for feature module."""
  import pytest
  from src.feature import calculate


  def test_calculate_returns_sum_of_two_positive_integers():
      """Calculate should return the sum of two positive integers."""
      result = calculate(5, 3)
      assert result == 8
  </content>
  </create>

run and verify it fails:
  <terminal>python -m pytest tests/test_feature.py -v</terminal>

expected output:
  FAILED - ImportError or ModuleNotFoundError
  this is correct - the module doesnt exist yet

if test passes on first run:
  - the feature already exists (search for it)
  - or your test is wrong (testing the wrong thing)
  - NEVER proceed with a passing test in the red phase


the green phase

purpose: make the test pass with MINIMAL code.

requirements:
  [1] write the simplest code that passes
  [2] dont add features not tested
  [3] dont optimize yet
  [4] its okay to hardcode if test allows it

minimal implementation:
  <create>
  <file>src/feature.py</file>
  <content>
  """Feature module."""


  def calculate(a: int, b: int) -> int:
      """Calculate the sum of two integers."""
      return a + b
  </content>
  </create>

run and verify it passes:
  <terminal>python -m pytest tests/test_feature.py -v</terminal>

expected: PASSED

if test still fails:
  - read the error carefully
  - fix the specific issue
  - run again
  - repeat until green


the refactor phase

purpose: improve code quality while tests stay green.

requirements:
  [1] tests must pass before refactoring
  [2] tests must pass after EVERY change
  [3] dont add new functionality
  [4] focus on readability, performance, design

refactoring checklist:
  [ ] remove duplication
  [ ] improve naming
  [ ] extract methods/functions
  [ ] simplify logic
  [ ] add type hints
  [ ] improve error messages

after each refactor step:
  <terminal>python -m pytest tests/test_feature.py -v</terminal>

if tests fail during refactor:
  - you broke something
  - revert the last change
  - try a smaller refactor step


PHASE 2: TEST STRUCTURE AND PATTERNS


the arrange-act-assert pattern

every test follows this structure:

  def test_something():
      # ARRANGE - set up the test conditions
      user = User(name="alice", email="alice@example.com")
      service = UserService(db=mock_db)

      # ACT - perform the action being tested
      result = service.create_user(user)

      # ASSERT - verify the outcome
      assert result.id is not None
      assert result.name == "alice"

keep each section clearly separated.
some teams add blank lines between sections.


test naming conventions

tests should read like documentation:

pattern: test_<function>_<scenario>_<expected_result>

  [ok] test_calculate_with_two_positive_numbers_returns_sum
  [ok] test_calculate_with_negative_number_returns_correct_difference
  [ok] test_login_with_invalid_password_raises_auth_error
  [ok] test_create_user_with_duplicate_email_returns_conflict_error

  [x] test_calculate
  [x] test_calculate_1
  [x] test_it_works
  [x] test_functionality

the test name should tell you what broke without reading the code.


test file organization

  tests/
    __init__.py
    conftest.py              # shared fixtures
    unit/                    # fast, isolated tests
      __init__.py
      test_models.py
      test_utils.py
      test_validators.py
    integration/             # tests with real dependencies
      __init__.py
      test_database.py
      test_api.py
    e2e/                     # end-to-end tests
      __init__.py
      test_workflows.py

naming mirrors source structure:
  src/auth/login.py      -> tests/unit/test_login.py
  src/api/routes.py      -> tests/integration/test_routes.py


test class organization

group related tests in classes:

  class TestUserCreation:
      """Tests for user creation functionality."""

      def test_create_user_with_valid_data_succeeds(self):
          ...

      def test_create_user_with_missing_email_fails(self):
          ...

      def test_create_user_with_duplicate_email_fails(self):
          ...


  class TestUserAuthentication:
      """Tests for user authentication functionality."""

      def test_login_with_valid_credentials_returns_token(self):
          ...

      def test_login_with_invalid_password_raises_error(self):
          ...


PHASE 3: FIXTURES AND TEST DATA


pytest fixtures

fixtures provide reusable test data and setup:

  # conftest.py
  import pytest
  from src.models import User
  from src.database import Database


  @pytest.fixture
  def sample_user():
      """Create a sample user for testing."""
      return User(
          id=1,
          name="Test User",
          email="test@example.com"
      )


  @pytest.fixture
  def db_connection():
      """Create a database connection for testing."""
      db = Database(":memory:")
      db.initialize()
      yield db
      db.close()


  @pytest.fixture
  def populated_db(db_connection, sample_user):
      """Database with sample data."""
      db_connection.insert(sample_user)
      return db_connection

using fixtures in tests:

  def test_get_user_returns_user_data(populated_db, sample_user):
      result = populated_db.get_user(sample_user.id)
      assert result.name == sample_user.name


fixture scopes

  @pytest.fixture(scope="function")   # default - new for each test
  @pytest.fixture(scope="class")      # shared within test class
  @pytest.fixture(scope="module")     # shared within test file
  @pytest.fixture(scope="session")    # shared across all tests

use narrowest scope possible.
wider scopes risk test pollution.


factory fixtures

for creating multiple variations:

  @pytest.fixture
  def user_factory():
      """Factory for creating test users."""
      def _create_user(name="Test", email=None, role="user"):
          if email is None:
              email = f"{name.lower()}@example.com"
          return User(name=name, email=email, role=role)
      return _create_user


  def test_admin_can_delete_users(user_factory):
      admin = user_factory(name="Admin", role="admin")
      regular = user_factory(name="Regular", role="user")
      # ... test logic


PHASE 4: MOCKING AND ISOLATION


when to mock

mock external dependencies:
  [ok] database connections
  [ok] API calls to external services
  [ok] file system operations
  [ok] time/date operations
  [ok] random number generation
  [ok] environment variables

dont mock:
  [x] the code under test
  [x] simple data structures
  [x] pure functions with no side effects


using pytest-mock

  def test_send_email_calls_smtp_server(mocker):
      # arrange
      mock_smtp = mocker.patch("src.email.smtplib.SMTP")
      service = EmailService()

      # act
      service.send_email("test@example.com", "Hello", "World")

      # assert
      mock_smtp.return_value.sendmail.assert_called_once()


  def test_get_data_handles_api_timeout(mocker):
      # arrange
      mock_request = mocker.patch("src.api.requests.get")
      mock_request.side_effect = requests.Timeout("Connection timed out")
      client = APIClient()

      # act & assert
      with pytest.raises(APIError) as exc_info:
          client.get_data()
      assert "timed out" in str(exc_info.value)


mocking return values

  def test_get_user_returns_cached_data(mocker):
      mock_cache = mocker.patch("src.service.cache")
      mock_cache.get.return_value = {"id": 1, "name": "Cached User"}

      result = get_user(1)

      assert result["name"] == "Cached User"
      mock_cache.get.assert_called_once_with("user:1")


mocking with side effects

  def test_retry_on_transient_failure(mocker):
      mock_api = mocker.patch("src.client.api_call")
      # fail twice, then succeed
      mock_api.side_effect = [
          ConnectionError("Failed"),
          ConnectionError("Failed again"),
          {"status": "success"}
      ]

      result = resilient_api_call()

      assert result["status"] == "success"
      assert mock_api.call_count == 3


PHASE 5: TESTING DIFFERENT SCENARIOS


testing exceptions

  def test_divide_by_zero_raises_value_error():
      with pytest.raises(ValueError) as exc_info:
          divide(10, 0)
      assert "cannot divide by zero" in str(exc_info.value)


  def test_invalid_email_raises_validation_error():
      with pytest.raises(ValidationError) as exc_info:
          validate_email("not-an-email")
      assert exc_info.value.field == "email"
      assert "invalid format" in exc_info.value.message


testing edge cases

comprehensive edge case checklist:

  # empty inputs
  def test_process_with_empty_list_returns_empty():
      assert process([]) == []

  def test_process_with_empty_string_returns_empty():
      assert process("") == ""

  # none/null inputs
  def test_process_with_none_raises_type_error():
      with pytest.raises(TypeError):
          process(None)

  # boundary values
  def test_process_with_zero_returns_zero():
      assert process(0) == 0

  def test_process_with_negative_one_handles_correctly():
      assert process(-1) == expected_negative_result

  def test_process_with_max_int_doesnt_overflow():
      import sys
      result = process(sys.maxsize)
      assert result is not None

  # single element
  def test_process_with_single_item_list():
      assert process([1]) == [1]

  # type variations
  def test_process_with_float_converts_correctly():
      assert process(3.14) == expected_float_result

  def test_process_with_string_number_converts():
      assert process("42") == 42


parametrized tests

test multiple inputs with one test function:

  @pytest.mark.parametrize("input,expected", [
      (0, 0),
      (1, 1),
      (2, 4),
      (3, 9),
      (10, 100),
      (-5, 25),
  ])
  def test_square_returns_correct_value(input, expected):
      assert square(input) == expected


  @pytest.mark.parametrize("email,is_valid", [
      ("user@example.com", True),
      ("user.name@example.co.uk", True),
      ("user+tag@example.com", True),
      ("invalid", False),
      ("@example.com", False),
      ("user@", False),
      ("", False),
      (None, False),
  ])
  def test_validate_email(email, is_valid):
      if is_valid:
          assert validate_email(email) is True
      else:
          assert validate_email(email) is False


parametrize with ids for clarity:

  @pytest.mark.parametrize("status_code,should_retry", [
      pytest.param(200, False, id="success-no-retry"),
      pytest.param(429, True, id="rate-limited-retry"),
      pytest.param(500, True, id="server-error-retry"),
      pytest.param(400, False, id="client-error-no-retry"),
  ])
  def test_should_retry_request(status_code, should_retry):
      assert should_retry_request(status_code) == should_retry


PHASE 6: ASYNC TESTING


testing async functions

install pytest-asyncio:
  <terminal>pip install pytest-asyncio</terminal>

mark async tests:

  import pytest


  @pytest.mark.asyncio
  async def test_fetch_data_returns_expected_result():
      result = await fetch_data("https://api.example.com/data")
      assert result["status"] == "success"


  @pytest.mark.asyncio
  async def test_concurrent_requests_complete():
      results = await asyncio.gather(
          fetch_data("url1"),
          fetch_data("url2"),
          fetch_data("url3")
      )
      assert len(results) == 3


async fixtures:

  @pytest.fixture
  async def async_client():
      client = AsyncAPIClient()
      await client.connect()
      yield client
      await client.disconnect()


  @pytest.mark.asyncio
  async def test_with_async_client(async_client):
      result = await async_client.get("/users")
      assert result.status_code == 200


mocking async functions:

  @pytest.mark.asyncio
  async def test_async_api_call(mocker):
      mock_fetch = mocker.patch("src.client.aiohttp.ClientSession.get")

      # create async mock response
      mock_response = mocker.AsyncMock()
      mock_response.json.return_value = {"data": "test"}
      mock_fetch.return_value.__aenter__.return_value = mock_response

      result = await fetch_json("https://api.example.com")

      assert result["data"] == "test"


PHASE 7: DATABASE TESTING


test database setup

  @pytest.fixture(scope="function")
  def test_db():
      """Create a fresh test database for each test."""
      # use in-memory SQLite for speed
      engine = create_engine("sqlite:///:memory:")
      Base.metadata.create_all(engine)
      Session = sessionmaker(bind=engine)
      session = Session()

      yield session

      session.close()


  def test_create_user_persists_to_database(test_db):
      user = User(name="Alice", email="alice@example.com")
      test_db.add(user)
      test_db.commit()

      retrieved = test_db.query(User).filter_by(email="alice@example.com").first()
      assert retrieved is not None
      assert retrieved.name == "Alice"


transaction rollback pattern

  @pytest.fixture
  def db_session(test_db):
      """Wrap each test in a transaction that rolls back."""
      test_db.begin_nested()

      yield test_db

      test_db.rollback()


testing database constraints

  def test_duplicate_email_raises_integrity_error(test_db):
      user1 = User(name="Alice", email="same@example.com")
      user2 = User(name="Bob", email="same@example.com")

      test_db.add(user1)
      test_db.commit()

      test_db.add(user2)
      with pytest.raises(IntegrityError):
          test_db.commit()


PHASE 8: API TESTING


testing with test client

  import pytest
  from fastapi.testclient import TestClient
  from src.main import app


  @pytest.fixture
  def client():
      return TestClient(app)


  def test_get_users_returns_list(client):
      response = client.get("/api/users")
      assert response.status_code == 200
      assert isinstance(response.json(), list)


  def test_create_user_returns_created(client):
      response = client.post(
          "/api/users",
          json={"name": "Alice", "email": "alice@example.com"}
      )
      assert response.status_code == 201
      assert response.json()["name"] == "Alice"


  def test_get_nonexistent_user_returns_404(client):
      response = client.get("/api/users/99999")
      assert response.status_code == 404


testing authentication

  @pytest.fixture
  def auth_headers(client):
      """Get authentication headers for testing."""
      response = client.post(
          "/api/auth/login",
          json={"username": "testuser", "password": "testpass"}
      )
      token = response.json()["token"]
      return {"Authorization": f"Bearer {token}"}


  def test_protected_endpoint_requires_auth(client):
      response = client.get("/api/protected")
      assert response.status_code == 401


  def test_protected_endpoint_works_with_auth(client, auth_headers):
      response = client.get("/api/protected", headers=auth_headers)
      assert response.status_code == 200


testing error responses

  def test_invalid_json_returns_400(client):
      response = client.post(
          "/api/users",
          data="not json",
          headers={"Content-Type": "application/json"}
      )
      assert response.status_code == 400


  def test_missing_required_field_returns_422(client):
      response = client.post(
          "/api/users",
          json={"name": "Alice"}  # missing email
      )
      assert response.status_code == 422
      assert "email" in response.json()["detail"][0]["loc"]


PHASE 9: TEST COVERAGE


running coverage

  <terminal>python -m pytest tests/ --cov=src --cov-report=term-missing</terminal>

  <terminal>python -m pytest tests/ --cov=src --cov-report=html</terminal>

view html report:
  <terminal>open htmlcov/index.html</terminal>


coverage thresholds

add to pytest.ini:
  [pytest]
  addopts = --cov=src --cov-fail-under=80

fail build if coverage drops below 80%.


what coverage tells you

  [ok] 100% coverage = all lines executed during tests
  [warn] 100% coverage != all scenarios tested
  [warn] 100% coverage != no bugs

coverage is a floor, not a ceiling.
high coverage with bad tests is worse than low coverage with good tests.


what to focus on

prioritize coverage for:
  - business logic
  - error handling paths
  - edge cases
  - security-sensitive code

less critical:
  - simple getters/setters
  - configuration loading
  - logging statements


PHASE 10: TEST QUALITY PATTERNS


one assertion per test (mostly)

  # good - one logical assertion
  def test_create_user_returns_correct_id():
      user = create_user("Alice")
      assert user.id is not None


  # good - multiple assertions about one outcome
  def test_create_user_returns_complete_user():
      user = create_user("Alice")
      assert user.id is not None
      assert user.name == "Alice"
      assert user.created_at is not None


  # bad - testing multiple behaviors
  def test_user_operations():
      user = create_user("Alice")
      assert user.id is not None

      updated = update_user(user.id, name="Bob")
      assert updated.name == "Bob"

      delete_user(user.id)
      assert get_user(user.id) is None


test isolation

each test must be independent:

  # bad - tests depend on each other
  class TestUserWorkflow:
      user_id = None

      def test_create_user(self):
          user = create_user("Alice")
          TestUserWorkflow.user_id = user.id  # shared state!

      def test_get_user(self):
          user = get_user(TestUserWorkflow.user_id)  # depends on first test!
          assert user.name == "Alice"


  # good - each test is independent
  class TestUserWorkflow:

      def test_create_user(self):
          user = create_user("Alice")
          assert user.id is not None

      def test_get_user(self, sample_user):  # fixture provides data
          user = get_user(sample_user.id)
          assert user.name == sample_user.name


avoid test pollution

  @pytest.fixture(autouse=True)
  def clean_environment():
      """Reset environment before each test."""
      os.environ.pop("API_KEY", None)
      yield
      os.environ.pop("API_KEY", None)


  @pytest.fixture(autouse=True)
  def reset_singletons():
      """Reset singleton instances between tests."""
      Config._instance = None
      Cache._instance = None
      yield


PHASE 11: DEBUGGING FAILING TESTS


reading test output

  FAILED tests/test_user.py::test_create_user - AssertionError: assert None == 1

  breakdown:
    - file: tests/test_user.py
    - test: test_create_user
    - error: AssertionError
    - detail: expected 1, got None


verbose output

  <terminal>python -m pytest tests/test_user.py::test_create_user -v</terminal>

  <terminal>python -m pytest tests/test_user.py::test_create_user -vv</terminal>

  <terminal>python -m pytest tests/test_user.py::test_create_user -vvv</terminal>


print debugging

  def test_something():
      result = complex_function()
      print(f"DEBUG: result = {result}")  # shows with -s flag
      assert result == expected

run with:
  <terminal>python -m pytest tests/test_user.py -s</terminal>


pdb debugging

  def test_something():
      result = complex_function()
      import pdb; pdb.set_trace()  # drops into debugger
      assert result == expected

run with:
  <terminal>python -m pytest tests/test_user.py -s --pdb</terminal>


run single test

  <terminal>python -m pytest tests/test_user.py::test_create_user -v</terminal>

  <terminal>python -m pytest tests/test_user.py::TestUserCreation::test_create_user -v</terminal>


run tests matching pattern

  <terminal>python -m pytest -k "create" -v</terminal>

  <terminal>python -m pytest -k "create and not delete" -v</terminal>


PHASE 12: REFACTORING WITH TESTS


the safety net

tests enable fearless refactoring:
  [1] run all tests - confirm green
  [2] make one refactoring change
  [3] run all tests - confirm still green
  [4] repeat

if tests fail after refactor:
  - you broke something
  - revert immediately
  - try smaller change


refactoring patterns

extract function:

  # before
  def process_order(order):
      # validate
      if not order.items:
          raise ValueError("Empty order")
      if order.total < 0:
          raise ValueError("Invalid total")
      # ... more validation ...

      # process
      for item in order.items:
          # ... processing ...

  # after
  def process_order(order):
      validate_order(order)
      process_items(order.items)

  def validate_order(order):
      if not order.items:
          raise ValueError("Empty order")
      if order.total < 0:
          raise ValueError("Invalid total")

  def process_items(items):
      for item in items:
          # ... processing ...

tests should still pass after extraction.


rename for clarity:

  # before
  def proc(d):
      return d["v"] * d["q"]

  # after
  def calculate_line_total(line_item):
      return line_item["price"] * line_item["quantity"]

update tests to use new name.
tests verify behavior unchanged.


PHASE 13: TDD FOR BUG FIXES


bug fix workflow

[1] reproduce the bug manually
[2] write a test that fails due to the bug
[3] verify test fails for the right reason
[4] fix the bug
[5] verify test passes
[6] verify no other tests broke


example bug fix

bug report: "negative quantities allowed in orders"

step 1: write failing test

  def test_order_rejects_negative_quantity():
      with pytest.raises(ValidationError):
          create_order_item(product_id=1, quantity=-5)

  <terminal>python -m pytest tests/test_order.py::test_order_rejects_negative_quantity -v</terminal>

expected: FAIL (the bug exists, so negative is currently allowed)

step 2: fix the bug

  <read><file>src/orders.py</file></read>

  <edit>
  <file>src/orders.py</file>
  <find>
  def create_order_item(product_id: int, quantity: int):
      return OrderItem(product_id=product_id, quantity=quantity)
  </find>
  <replace>
  def create_order_item(product_id: int, quantity: int):
      if quantity <= 0:
          raise ValidationError("Quantity must be positive")
      return OrderItem(product_id=product_id, quantity=quantity)
  </replace>
  </edit>

step 3: verify fix

  <terminal>python -m pytest tests/test_order.py::test_order_rejects_negative_quantity -v</terminal>

expected: PASS

step 4: run full test suite

  <terminal>python -m pytest tests/ -v</terminal>

ensure fix didnt break anything else.


PHASE 14: TESTING EXTERNAL SERVICES


mocking external APIs

  def test_get_weather_returns_temperature(mocker):
      mock_response = mocker.Mock()
      mock_response.status_code = 200
      mock_response.json.return_value = {
          "temperature": 72,
          "conditions": "sunny"
      }

      mocker.patch("requests.get", return_value=mock_response)

      result = get_weather("New York")

      assert result["temperature"] == 72


testing with VCR (recording HTTP interactions)

  pip install pytest-vcr

  @pytest.mark.vcr()
  def test_real_api_call():
      """First run hits real API and records, subsequent runs use recording."""
      result = fetch_from_external_api()
      assert result is not None


testing timeouts and failures

  def test_api_timeout_raises_error(mocker):
      mocker.patch("requests.get", side_effect=requests.Timeout())

      with pytest.raises(ExternalServiceError):
          fetch_data_from_api()


  def test_api_500_error_triggers_retry(mocker):
      mock_get = mocker.patch("requests.get")
      mock_get.side_effect = [
          mocker.Mock(status_code=500),
          mocker.Mock(status_code=500),
          mocker.Mock(status_code=200, json=lambda: {"data": "success"})
      ]

      result = fetch_with_retry()

      assert result["data"] == "success"
      assert mock_get.call_count == 3


PHASE 15: PERFORMANCE TESTING


timing tests

  import time

  def test_search_completes_within_threshold():
      start = time.time()
      result = search("query")
      elapsed = time.time() - start

      assert elapsed < 1.0  # must complete within 1 second
      assert len(result) > 0


using pytest-benchmark

  pip install pytest-benchmark

  def test_sort_performance(benchmark):
      data = list(range(10000, 0, -1))
      result = benchmark(lambda: custom_sort(data.copy()))
      assert result == sorted(data)


memory testing

  pip install pytest-memray

  @pytest.mark.limit_memory("100 MB")
  def test_large_data_processing():
      result = process_large_dataset(generate_large_data())
      assert result is not None


PHASE 16: CONTINUOUS INTEGRATION


github actions example

  # .github/workflows/tests.yml
  name: Tests

  on: [push, pull_request]

  jobs:
    test:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3

        - name: Set up Python
          uses: actions/setup-python@v4
          with:
            python-version: '3.11'

        - name: Install dependencies
          run: |
            pip install -r requirements.txt
            pip install pytest pytest-cov

        - name: Run tests
          run: |
            python -m pytest tests/ --cov=src --cov-report=xml

        - name: Upload coverage
          uses: codecov/codecov-action@v3


pre-commit hooks

  # .pre-commit-config.yaml
  repos:
    - repo: local
      hooks:
        - id: pytest
          name: pytest
          entry: python -m pytest tests/ -x -q
          language: system
          pass_filenames: false
          always_run: true


PHASE 17: COMMON PITFALLS


pitfall: testing implementation, not behavior

  # bad - tests HOW it works
  def test_cache_uses_dict():
      cache = Cache()
      assert isinstance(cache._storage, dict)

  # good - tests WHAT it does
  def test_cache_stores_and_retrieves_values():
      cache = Cache()
      cache.set("key", "value")
      assert cache.get("key") == "value"


pitfall: over-mocking

  # bad - mocking the thing youre testing
  def test_calculate(mocker):
      mocker.patch("src.math.add", return_value=5)
      assert calculate(2, 3) == 5  # youre not testing calculate!

  # good - test real implementation
  def test_calculate():
      assert calculate(2, 3) == 5


pitfall: flaky tests

causes:
  - time-dependent code
  - random data
  - external dependencies
  - test order dependencies
  - shared state

fixes:
  - mock time/random
  - isolate tests
  - use fixtures for setup
  - run tests in random order to find issues:
    <terminal>python -m pytest tests/ --random-order</terminal>


pitfall: slow tests

causes:
  - real database connections
  - real API calls
  - file system operations
  - sleep() calls

fixes:
  - use in-memory databases
  - mock external calls
  - use tmpdir fixtures
  - mock time.sleep


pitfall: testing too much at once

  # bad - integration test pretending to be unit test
  def test_user_signup():
      result = signup("user@example.com", "password")
      assert result.id is not None
      assert result.email == "user@example.com"
      assert result.password_hash is not None
      assert result.created_at is not None
      assert email_sent_to("user@example.com")
      assert user_in_database(result.id)
      assert audit_log_contains("user_created")

  # good - focused unit test
  def test_create_user_sets_email():
      user = create_user(email="user@example.com", password="password")
      assert user.email == "user@example.com"


PHASE 18: TDD RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] NEVER write implementation before test
      if you catch yourself writing code first, stop
      write the test, then the code

  [2] run tests after EVERY change
      no exceptions
      <terminal>python -m pytest tests/test_current.py -v</terminal>

  [3] write ONE test at a time
      resist the urge to write all tests upfront
      red-green-refactor, one cycle at a time

  [4] keep tests simple and focused
      if a test needs extensive setup, something is wrong
      with the design

  [5] refactor only when tests pass
      never refactor while red
      get to green first, then clean up

  [6] if test passes on first run, QUESTION IT
      a passing test in the red phase means:
        - the feature exists (find it)
        - the test is wrong (fix it)
        - you wrote code before the test (start over)

  [7] tests are production code
      apply same quality standards
      refactor tests too


PHASE 19: TDD SESSION CHECKLIST


before starting:

  [ ] pytest installed and working
  [ ] tests directory exists
  [ ] conftest.py with common fixtures
  [ ] pytest.ini or pyproject.toml configured
  [ ] coverage tools installed
  [ ] existing tests passing

for each feature:

  [ ] understand the requirement
  [ ] write ONE failing test
  [ ] verify it fails for the right reason
  [ ] write minimal code to pass
  [ ] verify it passes
  [ ] refactor if needed
  [ ] verify still passes
  [ ] commit

after completing feature:

  [ ] run full test suite
  [ ] check coverage
  [ ] review test quality
  [ ] commit with message referencing feature


FINAL REMINDERS


tdd is a discipline

it feels slow at first.
it becomes fast with practice.
the tests you write today save hours tomorrow.


tests are documentation

tests show how code should be used.
tests show what behavior is expected.
tests show edge cases and error handling.


when in doubt

write a test.
if you cant write a test, you dont understand the requirement.
the test forces clarity.


the goal

working software with high confidence.
fearless refactoring.
living documentation.
fewer bugs in production.

now go write some failing tests.
