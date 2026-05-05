---
name: api-integration
description: "API Integration skill - integrate with external APIs reliably"
---

api-integration mode: RELIABLE EXTERNAL SERVICE CONNECTIONS

when this skill is active, you follow disciplined API integration practices.
this is a comprehensive guide to integrating with REST and GraphQL APIs.


PHASE 0: ENVIRONMENT PREREQUISITES VERIFICATION

before integrating ANY external API, verify your environment is ready.


check http client library

  <terminal>python -c "import requests; print(requests.__version__)"</terminal>

if requests not installed:
  <terminal>pip install requests</terminal>


check async http client

  <terminal>python -c "import httpx; print(httpx.__version__)"</terminal>

if httpx not installed:
  <terminal>pip install httpx</terminal>


verify api credentials exist

  <terminal>ls -la .env 2>/dev/null || echo "no .env file"</terminal>

  <terminal>echo $API_KEY 2>/dev/null || echo "API_KEY not set"</terminal>

if credentials missing:
  <create>
  <file>.env.example</file>
  <content>
  # API Credentials
  API_KEY=your_api_key_here
  API_SECRET=your_api_secret_here
  API_BASE_URL=https://api.example.com
  </content>
  </create>

  remind user to create .env from .env.example


check for environment variable loader

  <terminal>python -c "import dotenv; print('python-dotenv installed')" 2>/dev/null || echo "need python-dotenv"</terminal>

if not installed:
  <terminal>pip install python-dotenv</terminal>


check project structure for api code

  <terminal>ls -la src/api/ 2>/dev/null || ls -la core/api/ 2>/dev/null || echo "no api directory"</terminal>

  <terminal>find . -name "*client*.py" -type f | grep -v __pycache__ | head -5</terminal>

understand existing api patterns before adding new integrations.


verify request/response validation tools

  <terminal>python -c "import pydantic; print('pydantic available')" 2>/dev/null || echo "pydantic not installed"</terminal>

if not installed:
  <terminal>pip install pydantic</terminal>


PHASE 1: API INTEGRATION FUNDAMENTALS


understand the api contract

before writing code, gather this information:

  [ ] base URL (e.g., https://api.example.com/v1)
  [ ] authentication method (API key, OAuth, JWT)
  [ ] rate limits (requests per minute/hour)
  [ ] available endpoints
  [ ] request/response formats
  [ ] error response format
  [ ] pagination style
  [ ] webhooks or streaming support

read the documentation. bookmark the reference.
save the openapi spec if available.


basic rest client structure

  <create>
  <file>src/api/base_client.py</file>
  <content>
  """Base API client with common functionality."""
  from typing import Any, Dict, Optional
  import requests
  from requests.adapters import HTTPAdapter
  from urllib3.util.retry import Retry


  class BaseAPIClient:
      """Base class for API clients."""

      def __init__(
          self,
          base_url: str,
          api_key: Optional[str] = None,
          timeout: int = 30,
          max_retries: int = 3
      ):
          self.base_url = base_url.rstrip("/")
          self.api_key = api_key
          self.timeout = timeout

          # configure session with retry logic
          self.session = requests.Session()
          retry_strategy = Retry(
              total=max_retries,
              backoff_factor=1,
              status_forcelist=[429, 500, 502, 503, 504],
              allowed_methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
          )
          adapter = HTTPAdapter(max_retries=retry_strategy)
          self.session.mount("http://", adapter)
          self.session.mount("https://", adapter)

      def _build_url(self, path: str) -> str:
          """Build full URL from path."""
          return f"{self.base_url}/{path.lstrip('/')}"

      def _get_headers(self) -> Dict[str, str]:
          """Build default headers."""
          headers = {
              "Content-Type": "application/json",
              "Accept": "application/json"
          }
          if self.api_key:
              headers["Authorization"] = f"Bearer {self.api_key}"
          return headers
  </content>
  </create>


async rest client with httpx

  <create>
  <file>src/api/async_client.py</file>
  <content>
  """Async API client using httpx."""
  from typing import Any, Dict, Optional
  import httpx
  import asyncio
  from httpx import AsyncClient, Response, TimeoutException


  class AsyncAPIClient:
      """Async API client for high-performance integration."""

      def __init__(
          self,
          base_url: str,
          api_key: Optional[str] = None,
          timeout: float = 30.0,
          limits: Optional[httpx.Limits] = None
      ):
          self.base_url = base_url.rstrip("/")
          self.api_key = api_key
          self.timeout = timeout

          # configure connection limits
          if limits is None:
              limits = httpx.Limits(
                  max_keepalive_connections=20,
                  max_connections=100,
                  keepalive_expiry=5.0
              )

          self._client: Optional[AsyncClient] = None
          self._limits = limits

      async def __aenter__(self):
          """Enter context manager."""
          await self.connect()
          return self

      async def __aexit__(self, exc_type, exc_val, exc_tb):
          """Exit context manager."""
          await self.close()

      async def connect(self):
          """Initialize the async client."""
          if self._client is None:
              headers = {
                  "Content-Type": "application/json",
                  "Accept": "application/json"
              }
              if self.api_key:
                  headers["Authorization"] = f"Bearer {self.api_key}"

              self._client = AsyncClient(
                  base_url=self.base_url,
                  headers=headers,
                  timeout=self.timeout,
                  limits=self._limits
              )

      async def close(self):
          """Close the async client."""
          if self._client:
              await self._client.aclose()
              self._client = None

      def _ensure_connected(self):
          """Ensure client is connected."""
          if self._client is None:
              raise RuntimeError("Client not connected. Use async with or call connect().")
  </content>
  </create>


PHASE 2: AUTHENTICATION PATTERNS


api key authentication

simplest form - key in header or query param:

  <read><file>src/api/base_client.py</file></read>

  <edit>
  <file>src/api/base_client.py</file>
  <find>
      def _get_headers(self) -> Dict[str, str]:
          """Build default headers."""
          headers = {
              "Content-Type": "application/json",
              "Accept": "application/json"
          }
          if self.api_key:
              headers["Authorization"] = f"Bearer {self.api_key}"
          return headers
  </find>
  <replace>
      def _get_headers(self) -> Dict[str, str]:
          """Build default headers."""
          headers = {
              "Content-Type": "application/json",
              "Accept": "application/json"
          }
          if self.api_key:
              # common patterns: Bearer token, API key, or custom header
              headers["Authorization"] = f"Bearer {self.api_key}"
              # alternative: headers["X-API-Key"] = self.api_key
          return headers
  </replace>
  </edit>


basic auth (username/password)

  import requests
  from requests.auth import HTTPBasicAuth

  response = requests.get(
      "https://api.example.com/endpoint",
      auth=HTTPBasicAuth("username", "password")
  )


oauth2 client credentials flow

  <create>
  <file>src/api/oauth_client.py</file>
  <content>
  """OAuth2 client credentials authentication."""
  from typing import Optional
  import time
  import requests
  from dataclasses import dataclass


  @dataclass
  class TokenResponse:
      """OAuth token response."""
      access_token: str
      token_type: str
      expires_in: int
      refresh_token: Optional[str] = None

      @property
      def expires_at(self) -> float:
          """Calculate expiration timestamp."""
          return time.time() + self.expires_in - 60  # 1 minute buffer


  class OAuth2Client:
      """OAuth2 client with automatic token refresh."""

      def __init__(
          self,
          token_url: str,
          client_id: str,
          client_secret: str,
          scope: Optional[str] = None
      ):
          self.token_url = token_url
          self.client_id = client_id
          self.client_secret = client_secret
          self.scope = scope
          self._token: Optional[TokenResponse] = None

      def get_token(self) -> str:
          """Get valid access token, refreshing if needed."""
          if self._token is None or time.time() >= self._token.expires_at:
              self._fetch_token()
          return self._token.access_token

      def _fetch_token(self):
          """Fetch new token from auth server."""
          data = {
              "grant_type": "client_credentials",
              "client_id": self.client_id,
              "client_secret": self.client_secret
          }
          if self.scope:
              data["scope"] = self.scope

          response = requests.post(self.token_url, data=data)
          response.raise_for_status()

          token_data = response.json()
          self._token = TokenResponse(
              access_token=token_data["access_token"],
              token_type=token_data["token_type"],
              expires_in=token_data["expires_in"],
              refresh_token=token_data.get("refresh_token")
          )
  </content>
  </create>


oauth2 authorization code flow

  <create>
  <file>src/api/auth_code_client.py</file>
  <content>
  """OAuth2 authorization code flow for user authentication."""
  from typing import Optional
  import uuid
  from urllib.parse import urlencode
  import webbrowser
  from http.server import HTTPServer, BaseHTTPRequestHandler
  import requests


  class OAuth2AuthCodeFlow:
      """Handle OAuth2 authorization code flow with local callback."""

      def __init__(
          self,
          auth_url: str,
          token_url: str,
          client_id: str,
          client_secret: str,
          redirect_uri: str = "http://localhost:8080/callback",
          scope: str = "openid profile email"
      ):
          self.auth_url = auth_url
          self.token_url = token_url
          self.client_id = client_id
          self.client_secret = client_secret
          self.redirect_uri = redirect_uri
          self.scope = scope
          self.state = str(uuid.uuid4())
          self.auth_code: Optional[str] = None

      def get_auth_url(self) -> str:
          """Generate authorization URL."""
          params = {
              "response_type": "code",
              "client_id": self.client_id,
              "redirect_uri": self.redirect_uri,
              "scope": self.scope,
              "state": self.state
          }
          return f"{self.auth_url}?{urlencode(params)}"

      def start_flow(self):
          """Start authorization flow by opening browser."""
          auth_url = self.get_auth_url()
          print(f"Opening browser for authorization: {auth_url}")
          webbrowser.open(auth_url)

          # start local server to handle callback
          self._start_callback_server()

      def _start_callback_server(self):
          """Start local HTTP server for callback."""
          class CallbackHandler(BaseHTTPRequestHandler):
              def __init__(self, parent, *args, **kwargs):
                  self.parent = parent
                  super().__init__(*args, **kwargs)

              def do_GET(self):
                  if self.path.startswith("/callback"):
                      # parse query params
                      from urllib.parse import urlparse, parse_qs
                      query = parse_qs(urlparse(self.path).query)

                      code = query.get("code", [None])[0]
                      state = query.get("state", [None])[0]

                      if state == self.parent.state and code:
                          self.parent.auth_code = code
                          self.send_response(200)
                          self.end_headers()
                          self.wfile.write(b"Authorization successful! You can close this window.")
                      else:
                          self.send_response(400)
                          self.end_headers()
                          self.wfile.write(b"Authorization failed!")

                  def log_message(self, format, *args):
                      pass  # suppress logs

          server = HTTPServer(("localhost", 8080), lambda *args, **kwargs: CallbackHandler(self, *args, **kwargs))
          print("Waiting for authorization callback on http://localhost:8080")
          server.handle_request()

      def exchange_code_for_token(self) -> dict:
          """Exchange authorization code for access token."""
          if not self.auth_code:
              raise RuntimeError("No authorization code received")

          data = {
              "grant_type": "authorization_code",
              "code": self.auth_code,
              "redirect_uri": self.redirect_uri,
              "client_id": self.client_id,
              "client_secret": self.client_secret
          }

          response = requests.post(self.token_url, data=data)
          response.raise_for_status()
          return response.json()
  </content>
  </create>


jwt authentication

  <create>
  <file>src/api/jwt_auth.py</file>
  <content>
  """JWT authentication for API clients."""
  from typing import Dict, Optional
  import time
  import jwt


  class JWTAuth:
      """JWT token generation and validation."""

      def __init__(
          self,
          secret_key: str,
          algorithm: str = "HS256",
          issuer: Optional[str] = None,
          audience: Optional[str] = None
      ):
          self.secret_key = secret_key
          self.algorithm = algorithm
          self.issuer = issuer
          self.audience = audience

      def generate_token(
          self,
          subject: str,
          payload: Optional[Dict] = None,
          expires_in: int = 3600
      ) -> str:
          """Generate a JWT token."""
          now = int(time.time())

          jwt_payload = {
              "sub": subject,
              "iat": now,
              "exp": now + expires_in,
              **(payload or {})
          }

          if self.issuer:
              jwt_payload["iss"] = self.issuer
          if self.audience:
              jwt_payload["aud"] = self.audience

          return jwt.encode(jwt_payload, self.secret_key, algorithm=self.algorithm)

      def validate_token(self, token: str) -> Dict:
          """Validate and decode a JWT token."""
          try:
              return jwt.decode(
                  token,
                  self.secret_key,
                  algorithms=[self.algorithm],
                  issuer=self.issuer,
                  audience=self.audience
              )
          except jwt.ExpiredSignatureError:
              raise ValueError("Token has expired")
          except jwt.InvalidTokenError as e:
              raise ValueError(f"Invalid token: {e}")
  </content>
  </create>


PHASE 3: MAKING REQUESTS


get requests

  <read><file>src/api/base_client.py</file></read>

  <edit>
  <file>src/api/base_client.py</file>
  <find>
      def _get_headers(self) -> Dict[str, str]:
          """Build default headers."""
          headers = {
              "Content-Type": "application/json",
              "Accept": "application/json"
          }
          if self.api_key:
              # common patterns: Bearer token, API key, or custom header
              headers["Authorization"] = f"Bearer {self.api_key}"
              # alternative: headers["X-API-Key"] = self.api_key
          return headers
  </find>
  <replace>
      def _get_headers(self) -> Dict[str, str]:
          """Build default headers."""
          headers = {
              "Content-Type": "application/json",
              "Accept": "application/json"
          }
          if self.api_key:
              headers["Authorization"] = f"Bearer {self.api_key}"
          return headers

      def get(self, path: str, params: Optional[Dict] = None) -> Response:
          """Make GET request."""
          url = self._build_url(path)
          response = self.session.get(
              url,
              headers=self._get_headers(),
              params=params,
              timeout=self.timeout
          )
          response.raise_for_status()
          return response

      def post(self, path: str, data: Optional[Dict] = None, json: Optional[Dict] = None) -> Response:
          """Make POST request."""
          url = self._build_url(path)
          response = self.session.post(
              url,
              headers=self._get_headers(),
              data=data,
              json=json,
              timeout=self.timeout
          )
          response.raise_for_status()
          return response

      def put(self, path: str, data: Optional[Dict] = None, json: Optional[Dict] = None) -> Response:
          """Make PUT request."""
          url = self._build_url(path)
          response = self.session.put(
              url,
              headers=self._get_headers(),
              data=data,
              json=json,
              timeout=self.timeout
          )
          response.raise_for_status()
          return response

      def patch(self, path: str, data: Optional[Dict] = None, json: Optional[Dict] = None) -> Response:
          """Make PATCH request."""
          url = self._build_url(path)
          response = self.session.patch(
              url,
              headers=self._get_headers(),
              data=data,
              json=json,
              timeout=self.timeout
          )
          response.raise_for_status()
          return response

      def delete(self, path: str) -> Response:
          """Make DELETE request."""
          url = self._build_url(path)
          response = self.session.delete(
              url,
              headers=self._get_headers(),
              timeout=self.timeout
          )
          response.raise_for_status()
          return response
  </replace>
  </edit>


add missing imports
  <read><file>src/api/base_client.py</file></read>

  <edit>
  <file>src/api/base_client.py</file>
  <find>
  """Base API client with common functionality."""
  from typing import Any, Dict, Optional
  import requests
  from requests.adapters import HTTPAdapter
  from urllib3.util.retry import Retry
  </find>
  <replace>
  """Base API client with common functionality."""
  from typing import Any, Dict, Optional
  import requests
  from requests.adapters import HTTPAdapter
  from requests.models import Response
  from urllib3.util.retry import Retry
  </replace>
  </edit>


async requests

  <read><file>src/api/async_client.py</file></read>

  <edit>
  <file>src/api/async_client.py</file>
  <find>
      def _ensure_connected(self):
          """Ensure client is connected."""
          if self._client is None:
              raise RuntimeError("Client not connected. Use async with or call connect().")
  </find>
  <replace>
      def _ensure_connected(self):
          """Ensure client is connected."""
          if self._client is None:
              raise RuntimeError("Client not connected. Use async with or call connect().")

      async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Response:
          """Make async GET request."""
          self._ensure_connected()
          return await self._client.get(path, params=params)

      async def post(
          self,
          path: str,
          data: Optional[Dict[str, Any]] = None,
          json: Optional[Dict[str, Any]] = None
      ) -> Response:
          """Make async POST request."""
          self._ensure_connected()
          return await self._client.post(path, data=data, json=json)

      async def put(
          self,
          path: str,
          data: Optional[Dict[str, Any]] = None,
          json: Optional[Dict[str, Any]] = None
      ) -> Response:
          """Make async PUT request."""
          self._ensure_connected()
          return await self._client.put(path, data=data, json=json)

      async def patch(
          self,
          path: str,
          data: Optional[Dict[str, Any]] = None,
          json: Optional[Dict[str, Any]] = None
      ) -> Response:
          """Make async PATCH request."""
          self._ensure_connected()
          return await self._client.patch(path, data=data, json=json)

      async def delete(self, path: str) -> Response:
          """Make async DELETE request."""
          self._ensure_connected()
          return await self._client.delete(path)
  </replace>
  </edit>


PHASE 4: ERROR HANDLING


custom exception hierarchy

  <create>
  <file>src/api/exceptions.py</file>
  <content>
  """API exception hierarchy for specific error handling."""


  class APIError(Exception):
      """Base exception for all API errors."""

      def __init__(
          self,
          message: str,
          status_code: Optional[int] = None,
          response_data: Optional[dict] = None
      ):
          super().__init__(message)
          self.status_code = status_code
          self.response_data = response_data or {}


  class AuthenticationError(APIError):
      """Authentication failed - invalid credentials."""
      pass


  class AuthorizationError(APIError):
      """Authorization failed - insufficient permissions."""
      pass


  class RateLimitError(APIError):
      """Rate limit exceeded."""

      def __init__(
          self,
          message: str,
          retry_after: Optional[int] = None,
          response_data: Optional[dict] = None
      ):
          super().__init__(message, status_code=429, response_data=response_data)
          self.retry_after = retry_after


  class ValidationError(APIError):
      """Request validation failed (400)."""
      pass


  class NotFoundError(APIError):
      """Resource not found (404)."""
      pass


  class ConflictError(APIError):
      """Resource conflict (409)."""
      pass


  class ServerError(APIError):
      """Server error (5xx)."""
      pass


  class TimeoutError(APIError):
      """Request timed out."""
      pass


  class ConnectionError(APIError):
      """Connection failed."""
      pass
  </content>
  </create>


error handling middleware

  <read><file>src/api/base_client.py</file></read>

  <edit>
  <file>src/api/base_client.py</file>
  <find>
  """Base API client with common functionality."""
  from typing import Any, Dict, Optional
  import requests
  from requests.adapters import HTTPAdapter
  from requests.models import Response
  from urllib3.util.retry import Retry
  </find>
  <replace>
  """Base API client with common functionality."""
  from typing import Any, Dict, Optional
  import requests
  from requests.adapters import HTTPAdapter
  from requests.models import Response
  from urllib3.util.retry import Retry

  from .exceptions import (
      APIError,
      AuthenticationError,
      AuthorizationError,
      RateLimitError,
      ValidationError,
      NotFoundError,
      ConflictError,
      ServerError
  )
  </replace>
  </edit>

  <edit>
  <file>src/api/base_client.py</file>
  <find>
      def delete(self, path: str) -> Response:
          """Make DELETE request."""
          url = self._build_url(path)
          response = self.session.delete(
              url,
              headers=self._get_headers(),
              timeout=self.timeout
          )
          response.raise_for_status()
          return response
  </find>
  <replace>
      def delete(self, path: str) -> Response:
          """Make DELETE request."""
          url = self._build_url(path)
          response = self.session.delete(
              url,
              headers=self._get_headers(),
              timeout=self.timeout
          )
          self._handle_errors(response)
          return response

      def _handle_errors(self, response: Response):
          """Handle API response errors with specific exceptions."""
          if response.ok:
              return

          status_code = response.status_code

          try:
              error_data = response.json()
              message = error_data.get("message", error_data.get("error", "Unknown error"))
          except ValueError:
              error_data = {}
              message = response.text or "Unknown error"

          if status_code == 401:
              raise AuthenticationError(message, status_code, error_data)
          elif status_code == 403:
              raise AuthorizationError(message, status_code, error_data)
          elif status_code == 404:
              raise NotFoundError(message, status_code, error_data)
          elif status_code == 409:
              raise ConflictError(message, status_code, error_data)
          elif status_code == 429:
              retry_after = response.headers.get("Retry-After")
              retry_after = int(retry_after) if retry_after else None
              raise RateLimitError(message, retry_after, error_data)
          elif 400 <= status_code < 500:
              raise ValidationError(message, status_code, error_data)
          elif 500 <= status_code < 600:
              raise ServerError(message, status_code, error_data)
          else:
              raise APIError(message, status_code, error_data)
  </replace>
  </edit>


PHASE 5: RATE LIMITING


understanding rate limits

common rate limit types:
  - requests per minute/hour
  - concurrent connections
  - burst allowance
  - tiered limits (free vs paid)

check headers:
  - X-RateLimit-Limit
  - X-RateLimit-Remaining
  - X-RateLimit-Reset
  - Retry-After


token bucket rate limiter

  <create>
  <file>src/api/rate_limiter.py</file>
  <content>
  """Rate limiting for API requests."""
  from typing import Optional
  import time
  from collections import deque
  from threading import Lock


  class TokenBucket:
      """Token bucket rate limiter."""

      def __init__(self, rate: float, capacity: int):
          """
          Args:
              rate: tokens per second
              capacity: bucket capacity
          """
          self.rate = rate
          self.capacity = capacity
          self.tokens = float(capacity)
          self.last_update = time.time()
          self._lock = Lock()

      def _refill(self):
          """Refill tokens based on elapsed time."""
          now = time.time()
          elapsed = now - self.last_update
          self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
          self.last_update = now

      def acquire(self, tokens: float = 1.0) -> bool:
          """Try to acquire tokens. Returns True if successful."""
          with self._lock:
              self._refill()
              if self.tokens >= tokens:
                  self.tokens -= tokens
                  return True
              return False

      def wait_for_token(self, tokens: float = 1.0):
          """Wait until tokens are available."""
          while not self.acquire(tokens):
              # calculate wait time
              self._refill()
              deficit = tokens - self.tokens
              wait_time = deficit / self.rate
              if wait_time > 0:
                  time.sleep(wait_time)


  class SlidingWindowRateLimiter:
      """Sliding window rate limiter."""

      def __init__(self, max_requests: int, window_seconds: int):
          """
          Args:
              max_requests: maximum requests in window
              window_seconds: time window in seconds
          """
          self.max_requests = max_requests
          self.window_seconds = window_seconds
          self.requests = deque()
          self._lock = Lock()

      def _clean_old_requests(self):
          """Remove requests outside the time window."""
          now = time.time()
          cutoff = now - self.window_seconds
          while self.requests and self.requests[0] < cutoff:
              self.requests.popleft()

      def acquire(self) -> bool:
          """Try to acquire a request slot."""
          with self._lock:
              self._clean_old_requests()
              if len(self.requests) < self.max_requests:
                  self.requests.append(time.time())
                  return True
              return False

      def wait_for_slot(self):
          """Wait until a request slot is available."""
          while not self.acquire():
              self._clean_old_requests()
              if self.requests:
                  # wait until oldest request expires
                  oldest = self.requests[0]
                  wait_time = self.window_seconds - (time.time() - oldest)
                  if wait_time > 0:
                      time.sleep(wait_time)
  </content>
  </create>


adaptive rate limiting

  <create>
  <file>src/api/adaptive_limiter.py</file>
  <content>
  """Adaptive rate limiter that responds to server signals."""
  from typing import Optional
  import time
  from .rate_limiter import TokenBucket
  from .exceptions import RateLimitError


  class AdaptiveRateLimiter:
      """Rate limiter that adapts based on API responses."""

      def __init__(
          self,
          initial_rate: float = 10.0,
          min_rate: float = 1.0,
          max_rate: float = 100.0
      ):
          self.initial_rate = initial_rate
          self.min_rate = min_rate
          self.max_rate = max_rate
          self.current_rate = initial_rate
          self.bucket = TokenBucket(rate=initial_rate, capacity=10)
          self.last_error_time: Optional[float] = None
          self.consecutive_errors = 0

      def acquire(self) -> bool:
          """Acquire a token."""
          return self.bucket.acquire()

      def wait_for_token(self):
          """Wait until token available."""
          self.bucket.wait_for_token()

      def report_success(self):
          """Report successful request - can increase rate."""
          self.consecutive_errors = 0

          # gradually increase rate back to initial
          if self.current_rate < self.initial_rate:
              self.current_rate = min(self.initial_rate, self.current_rate * 1.1)
              self.bucket = TokenBucket(rate=self.current_rate, capacity=10)

      def report_rate_limit_error(self, error: RateLimitError):
          """Report rate limit error - decrease rate."""
          self.consecutive_errors += 1
          self.last_error_time = time.time()

          # reduce rate based on consecutive errors
          reduction_factor = 0.5 ** self.consecutive_errors
          self.current_rate = max(
              self.min_rate,
              self.current_rate * reduction_factor
          )
          self.bucket = TokenBucket(rate=self.current_rate, capacity=10)

          # respect retry-after if provided
          if error.retry_after:
              wait_time = error.retry_after
          else:
              wait_time = 2.0 ** self.consecutive_errors  # exponential backoff

          time.sleep(wait_time)
  </content>
  </create>


PHASE 6: RETRY STRATEGIES


exponential backoff

  <create>
  <file>src/api/retry.py</file>
  <content>
  """Retry strategies for API calls."""
  from typing import Optional, Callable, Type, Tuple
  import time
  import random


  def calculate_backoff(
      attempt: int,
      base_delay: float = 1.0,
      max_delay: float = 60.0,
      exponential_base: float = 2.0,
      jitter: bool = True
  ) -> float:
      """Calculate exponential backoff delay."""
      delay = min(base_delay * (exponential_base ** attempt), max_delay)

      if jitter:
          # add randomness to prevent thundering herd
          delay = delay * (0.5 + random.random() * 0.5)

      return delay


  class RetryConfig:
      """Configuration for retry behavior."""

      def __init__(
          self,
          max_attempts: int = 3,
          base_delay: float = 1.0,
          max_delay: float = 60.0,
          retryable_status_codes: Tuple[int, ...] = (429, 500, 502, 503, 504),
          retryable_exceptions: Tuple[Type[Exception], ...] = (
              ConnectionError,
              TimeoutError
          )
      ):
          self.max_attempts = max_attempts
          self.base_delay = base_delay
          self.max_delay = max_delay
          self.retryable_status_codes = retryable_status_codes
          self.retryable_exceptions = retryable_exceptions


  def retry_with_backoff(
      func: Callable,
      config: Optional[RetryConfig] = None,
      on_retry: Optional[Callable[[int, Exception], None]] = None
  ):
      """Decorator for retrying function calls with exponential backoff."""

      if config is None:
          config = RetryConfig()

      def wrapper(*args, **kwargs):
          last_exception = None

          for attempt in range(config.max_attempts):
              try:
                  return func(*args, **kwargs)
              except Exception as e:
                  last_exception = e

                  # check if exception is retryable
                  if not isinstance(e, config.retryable_exceptions):
                      raise

                  # check if should retry
                  if attempt < config.max_attempts - 1:
                      delay = calculate_backoff(attempt, config.base_delay, config.max_delay)

                      if on_retry:
                          on_retry(attempt + 1, e)

                      time.sleep(delay)
                  else:
                      raise

          raise last_exception

      return wrapper
  </content>
  </create>


usage example

  from src.api.retry import retry_with_backoff, RetryConfig
  from src.api.exceptions import ServerError

  config = RetryConfig(
      max_attempts=5,
      base_delay=0.5,
      max_delay=30.0,
      retryable_status_codes=(429, 500, 502, 503, 504)
  )

  @retry_with_backoff(config=config)
  def fetch_user_data(user_id: int):
      return client.get(f"/users/{user_id}")


PHASE 7: PAGINATION


cursor-based pagination

  <create>
  <file>src/api/pagination.py</file>
  <content>
  """Pagination handling for API responses."""
  from typing import Iterator, List, Optional, TypeVar, Generic


  T = TypeVar("T")


  class CursorPage(Generic[T]):
      """Single page of cursor-paginated results."""

      def __init__(
          self,
          items: List[T],
          next_cursor: Optional[str] = None,
          has_more: bool = False
      ):
          self.items = items
          self.next_cursor = next_cursor
          self.has_more = has_more


  class CursorPaginator(Generic[T]):
      """Iterator for cursor-based pagination."""

      def __init__(self, fetch_function: callable, page_size: int = 100):
          """
          Args:
              fetch_function: callable that takes (cursor, limit) and returns CursorPage
              page_size: number of items per page
          """
          self.fetch_function = fetch_function
          self.page_size = page_size

      def __iter__(self) -> Iterator[T]:
          """Iterate through all pages."""
          cursor = None
          while True:
              page = self.fetch_function(cursor=cursor, limit=self.page_size)
              yield from page.items

              if not page.has_more or not page.next_cursor:
                  break
              cursor = page.next_cursor

      def get_all(self) -> List[T]:
          """Fetch all items as a list."""
          return list(self.__iter__())


  class OffsetPage(Generic[T]):
      """Single page of offset-based results."""

      def __init__(
          self,
          items: List[T],
          total: int,
          offset: int,
          limit: int
      ):
          self.items = items
          self.total = total
          self.offset = offset
          self.limit = limit

      @property
      def has_more(self) -> bool:
          """Check if more pages available."""
          return self.offset + self.limit < self.total


  class OffsetPaginator(Generic[T]):
      """Iterator for offset-based pagination."""

      def __init__(
          self,
          fetch_function: callable,
          page_size: int = 100,
          starting_offset: int = 0
      ):
          """
          Args:
              fetch_function: callable that takes (offset, limit) and returns OffsetPage
              page_size: number of items per page
              starting_offset: initial offset
          """
          self.fetch_function = fetch_function
          self.page_size = page_size
          self.starting_offset = starting_offset

      def __iter__(self) -> Iterator[T]:
          """Iterate through all pages."""
          offset = self.starting_offset

          while True:
              page = self.fetch_function(offset=offset, limit=self.page_size)
              yield from page.items

              if not page.has_more:
                  break
              offset += self.page_size

      def get_all(self) -> List[T]:
          """Fetch all items as a list."""
          return list(self.__iter__())

      def page_at(self, page_number: int) -> List[T]:
          """Get items at specific page number (1-indexed)."""
          offset = (page_number - 1) * self.page_size
          page = self.fetch_function(offset=offset, limit=self.page_size)
          return page.items
  </content>
  </create>


PHASE 8: RESPONSE VALIDATION


pydantic models for validation

  <create>
  <file>src/api/models.py</file>
  <content>
  """Pydantic models for API request/response validation."""
  from typing import List, Optional, Generic, TypeVar
  from datetime import datetime
  from pydantic import BaseModel, Field, validator
  from enum import Enum


  class UserRole(str, Enum):
      """User role enumeration."""
      ADMIN = "admin"
      USER = "user"
      GUEST = "guest"


  class User(BaseModel):
      """User model."""
      id: int = Field(..., description="Unique user identifier")
      name: str = Field(..., min_length=1, max_length=100)
      email: str = Field(..., regex=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
      role: UserRole = UserRole.USER
      created_at: datetime
      updated_at: Optional[datetime] = None

      @validator("email")
      def email_must_be_lowercase(cls, v):
          """Ensure email is lowercase."""
          return v.lower()


  class CreateUserRequest(BaseModel):
      """Request model for creating user."""
      name: str = Field(..., min_length=1, max_length=100)
      email: str = Field(..., regex=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
      role: UserRole = UserRole.USER
      password: str = Field(..., min_length=8, max_length=100)


  class UpdateUserRequest(BaseModel):
      """Request model for updating user."""
      name: Optional[str] = Field(None, min_length=1, max_length=100)
      email: Optional[str] = Field(None, regex=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
      role: Optional[UserRole] = None


  class UserListResponse(BaseModel):
      """Response model for user list."""
      items: List[User]
      total: int
      page: int
      page_size: int
      has_more: bool


  class ErrorResponse(BaseModel):
      """Error response model."""
      error: str
      message: str
      details: Optional[dict] = None
  </content>
  </create>


response parser

  <create>
  <file>src/api/response_parser.py</file>
  <content>
  """Response parsing and validation."""
  from typing import TypeVar, Type, Optional
  from pydantic import BaseModel, ValidationError
  from .exceptions import APIError


  T = TypeVar("T", bound=BaseModel)


  class ResponseParser:
      """Parse and validate API responses."""

      @staticmethod
      def parse(response_data: dict, model: Type[T]) -> T:
          """Parse response data into pydantic model."""
          try:
              return model(**response_data)
          except ValidationError as e:
              raise APIError(
                  f"Response validation failed: {e}",
                  response_data={"validation_errors": e.errors()}
          )

      @staticmethod
      def parse_optional(response_data: Optional[dict], model: Type[T]) -> Optional[T]:
          """Parse optional response data."""
          if response_data is None:
              return None
          return ResponseParser.parse(response_data, model)

      @staticmethod
      def parse_list(response_data: dict, items_key: str, model: Type[T]) -> list:
          """Parse response containing a list of items."""
          if items_key not in response_data:
              raise APIError(f"Response missing key: {items_key}")

          items = response_data[items_key]
          if not isinstance(items, list):
              raise APIError(f"Expected list for key {items_key}, got {type(items)}")

          result = []
          for item in items:
              try:
                  result.append(model(**item))
              except ValidationError as e:
                  raise APIError(f"Item validation failed: {e}")
          return result
  </content>
  </create>


PHASE 9: CACHING STRATEGIES


simple in-memory cache

  <create>
  <file>src/api/cache.py</file>
  <content>
  """Caching for API responses."""
  from typing import Optional, Dict, Any, Callable
  from datetime import datetime, timedelta
  from functools import wraps
  from hashlib import sha256
  import json


  class CacheEntry:
      """Single cache entry."""

      def __init__(self, value: Any, ttl_seconds: int):
          self.value = value
          self.expires_at = datetime.now() + timedelta(seconds=ttl_seconds)

      @property
      def is_expired(self) -> bool:
          """Check if entry has expired."""
          return datetime.now() >= self.expires_at


  class MemoryCache:
      """Simple in-memory cache with TTL."""

      def __init__(self):
          self._storage: Dict[str, CacheEntry] = {}

      def get(self, key: str) -> Optional[Any]:
          """Get value from cache."""
          entry = self._storage.get(key)
          if entry is None:
              return None
          if entry.is_expired:
              del self._storage[key]
              return None
          return entry.value

      def set(self, key: str, value: Any, ttl_seconds: int = 300):
          """Set value in cache."""
          self._storage[key] = CacheEntry(value, ttl_seconds)

      def invalidate(self, key: str):
          """Invalidate cache entry."""
          self._storage.pop(key, None)

      def clear(self):
          """Clear all cache entries."""
          self._storage.clear()

      def cleanup_expired(self):
          """Remove all expired entries."""
          expired_keys = [
              k for k, v in self._storage.items()
              if v.is_expired
          ]
          for key in expired_keys:
              del self._storage[key]


  def cache_response(
      cache: MemoryCache,
      ttl_seconds: int = 300,
      key_prefix: str = ""
  ):
      """Decorator for caching API responses."""

      def decorator(func: Callable) -> Callable:
          @wraps(func)
          def wrapper(*args, **kwargs):
              # generate cache key
              key_parts = [key_prefix]
              key_parts.extend(str(a) for a in args)
              key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
              cache_key = sha256("|".join(key_parts).encode()).hexdigest()

              # try cache first
              cached = cache.get(cache_key)
              if cached is not None:
                  return cached

              # call function and cache result
              result = func(*args, **kwargs)
              cache.set(cache_key, result, ttl_seconds)
              return result

          return wrapper

      return decorator
  </content>
  </create>


PHASE 10: GRAPHQL INTEGRATION


graphql client

  <create>
  <file>src/api/graphql_client.py</file>
  <content>
  """GraphQL API client."""
  from typing import Any, Dict, Optional, List
  import requests
  from .base_client import BaseAPIClient
  from .exceptions import APIError


  class GraphQLClient(BaseAPIClient):
      """Client for GraphQL APIs."""

      def __init__(self, base_url: str, api_key: Optional[str] = None):
          super().__init__(base_url, api_key)
          # GraphQL typically doesn't use Accept: application/json
          # but some implementations do

      def execute(
          self,
          query: str,
          variables: Optional[Dict[str, Any]] = None,
          operation_name: Optional[str] = None
      ) -> Dict[str, Any]:
          """Execute GraphQL query."""
          payload = {"query": query}

          if variables:
              payload["variables"] = variables
          if operation_name:
              payload["operationName"] = operation_name

          response = self.session.post(
              self._build_url(""),
              json=payload,
              headers=self._get_headers()
          )
          self._handle_errors(response)

          data = response.json()

          # check for GraphQL errors
          if "errors" in data:
              errors = data["errors"]
              messages = [e.get("message", str(e)) for e in errors]
              raise APIError(f"GraphQL errors: {', '.join(messages)}")

          return data.get("data", {})

      def query(
          self,
          query: str,
          variables: Optional[Dict[str, Any]] = None
      ) -> Dict[str, Any]:
          """Execute a GraphQL query."""
          return self.execute(query, variables)

      def mutate(
          self,
          mutation: str,
          variables: Optional[Dict[str, Any]] = None
      ) -> Dict[str, Any]:
          """Execute a GraphQL mutation."""
          return self.execute(mutation, variables)
  </content>
  </create>


graphql query builder

  <create>
  <file>src/api/graphql_builder.py</file>
  <content>
  """GraphQL query builder for type-safe queries."""
  from typing import List, Optional, Dict, Any


  class GraphQLQueryBuilder:
      """Builder for constructing GraphQL queries."""

      def __init__(self, operation_type: str = "query"):
          self.operation_type = operation_type
          self.name: Optional[str] = None
          self.fields: List[str] = []
          self.arguments: Dict[str, str] = {}
          self.fragments: List[str] = []

      def name_op(self, name: str) -> "GraphQLQueryBuilder":
          """Set operation name."""
          self.name = name
          return self

      def field(self, field_path: str) -> "GraphQLQueryBuilder":
          """Add a field to query."""
          self.fields.append(field_path)
          return self

      def fields(self, *field_paths: str) -> "GraphQLQueryBuilder":
          """Add multiple fields."""
          self.fields.extend(field_paths)
          return self

      def arg(self, key: str, value: Any) -> "GraphQLQueryBuilder":
          """Add argument to operation."""
          if isinstance(value, str):
              self.arguments[key] = f'"{value}"'
          elif isinstance(value, bool):
              self.arguments[key] = str(value).lower()
          elif value is None:
              self.arguments[key] = "null"
          else:
              self.arguments[key] = str(value)
          return self

      def args(self, **kwargs: Any) -> "GraphQLQueryBuilder":
          """Add multiple arguments."""
          for key, value in kwargs.items():
              self.arg(key, value)
          return self

      def fragment(self, fragment: str) -> "GraphQLQueryBuilder":
          """Add a fragment."""
          self.fragments.append(fragment)
          return self

      def build(self) -> str:
          """Build the complete GraphQL query."""
          # operation declaration
          if self.name:
              args_str = ", ".join(f"${k}: {self._infer_type(v)}" for k, v in self.arguments.items())
              operation = f"{self.operation_type} {self.name}"
              if args_str:
                  operation += f"({args_str})"
          else:
              operation = self.operation_type

          # field arguments
          field_args = ""
          if self.arguments:
              field_args = "(" + ", ".join(f"{k}: ${k}" for k in self.arguments.keys()) + ")"

          # selection set
          selection = "\n    ".join(self.fields)

          # combine
          query = f"{operation} {{{field_args}\n    {selection}\n}}"

          # add fragments
          if self.fragments:
              query += "\n\n" + "\n".join(self.fragments)

          return query

      def _infer_type(self, value: str) -> str:
          """Infer GraphQL type from formatted value."""
          if value.startswith('"'):
              return "String"
          if value == "true" or value == "false":
              return "Boolean"
          if value == "null":
              return "ID"
          if "." in value:
              return "Float"
          return "Int"


  def query(name: str) -> GraphQLQueryBuilder:
      """Start building a GraphQL query."""
      return GraphQLQueryBuilder("query").name_op(name)


  def mutation(name: str) -> GraphQLQueryBuilder:
      """Start building a GraphQL mutation."""
      return GraphQLQueryBuilder("mutation").name_op(name)
  </content>
  </create>


PHASE 11: API TESTING


testing with mock responses

  <create>
  <file>tests/test_api_client.py</file>
  <content>
  """Tests for API client."""
  import pytest
  from unittest.mock import Mock, patch
  from src.api.base_client import BaseAPIClient
  from src.api.exceptions import NotFoundError, RateLimitError


  @pytest.fixture
  def mock_response():
      """Create mock response."""
      mock = Mock()
      mock.ok = True
      mock.status_code = 200
      mock.json.return_value = {"id": 1, "name": "Test"}
      return mock


  @pytest.fixture
  def client():
      """Create test client."""
      return BaseAPIClient(
          base_url="https://api.test.com",
          api_key="test_key"
      )


  def test_get_request_builds_correct_url(client, mock_response):
      """Test that GET builds correct URL."""
      with patch.object(client.session, "get", return_value=mock_response) as mock_get:
          client.get("/users/123")

          mock_get.assert_called_once()
          called_url = mock_get.call_args[0][0]
          assert called_url == "https://api.test.com/users/123"


  def test_get_request_includes_auth_headers(client, mock_response):
      """Test that GET includes auth headers."""
      with patch.object(client.session, "get", return_value=mock_response) as mock_get:
          client.get("/users")

          headers = mock_get.call_args[1]["headers"]
          assert "Authorization" in headers
          assert headers["Authorization"] == "Bearer test_key"


  def test_404_raises_not_found(client):
      """Test that 404 raises NotFoundError."""
      mock_resp = Mock()
      mock_resp.ok = False
      mock_resp.status_code = 404
      mock_resp.json.return_value = {"message": "Not found"}

      with patch.object(client.session, "get", return_value=mock_resp):
          with pytest.raises(NotFoundError):
              client.get("/users/999")


  def test_429_raises_rate_limit_error(client):
      """Test that 429 raises RateLimitError."""
      mock_resp = Mock()
      mock_resp.ok = False
      mock_resp.status_code = 429
      mock_resp.headers = {"Retry-After": "60"}
      mock_resp.json.return_value = {"message": "Rate limit exceeded"}

      with patch.object(client.session, "get", return_value=mock_resp):
          with pytest.raises(RateLimitError) as exc_info:
              client.get("/users")

          assert exc_info.value.retry_after == 60
  </content>
  </create>


PHASE 12: LOGGING AND MONITORING


api client logging

  <create>
  <file>src/api/logging.py</file>
  <content>
  """Logging configuration for API clients."""
  import logging
  import time
  from typing import Optional, Dict, Any
  from requests.models import Response, PreparedRequest


  class APILogger:
      """Structured logging for API calls."""

      def __init__(self, name: str = "api"):
          self.logger = logging.getLogger(name)

      def log_request(
          self,
          method: str,
          url: str,
          headers: Optional[Dict[str, str]] = None,
          body: Optional[Any] = None
      ):
          """Log outgoing request."""
          self.logger.debug(
              "API Request",
              extra={
                  "event": "api_request",
                  "method": method,
                  "url": self._sanitize_url(url),
                  "has_body": body is not None
              }
          )

      def log_response(
          self,
          response: Response,
          duration_ms: float
      ):
          """Log received response."""
          self.logger.info(
              "API Response",
              extra={
                  "event": "api_response",
                  "status_code": response.status_code,
                  "duration_ms": round(duration_ms, 2),
                  "url": self._sanitize_url(str(response.url))
              }
          )

      def log_error(
          self,
          error: Exception,
          duration_ms: Optional[float] = None
      ):
          """Log API error."""
          self.logger.error(
              "API Error",
              extra={
                  "event": "api_error",
                  "error_type": type(error).__name__,
                  "error_message": str(error),
                  "duration_ms": round(duration_ms, 2) if duration_ms else None
              },
              exc_info=error
          )

      def _sanitize_url(self, url: str) -> str:
          """Remove sensitive parameters from URL."""
          # remove API keys, tokens, passwords from URL
          import re
          sanitized = re.sub(r'([?&](api_key|token|password)=)[^&]*', r'\1***', url)
          return sanitized


  class LoggedRequestMixin:
      """Mixin for adding logging to API clients."""

      def __init__(self, *args, **kwargs):
          super().__init__(*args, **kwargs)
          self.logger = APILogger(f"api.{self.__class__.__name__}")

      def _logged_request(self, method: str, *args, **kwargs):
          """Make request with logging."""
          import time
          start = time.time()

          try:
              # log request
              self.logger.log_request(method, *args, **kwargs)

              # make request
              response = super()._logged_request(method, *args, **kwargs)

              # log response
              duration_ms = (time.time() - start) * 1000
              self.logger.log_response(response, duration_ms)

              return response

          except Exception as e:
              duration_ms = (time.time() - start) * 1000
              self.logger.log_error(e, duration_ms)
              raise
  </content>
  </create>


PHASE 13: API DOCUMENTATION GENERATION


openapi spec generator

  <create>
  <file>src/api/openapi.py</file>
  <content>
  """Generate OpenAPI documentation for API clients."""
  from typing import Dict, Any, List, Optional


  class OpenAPIGenerator:
      """Generate OpenAPI specification from API client."""

      def __init__(self, title: str, version: str = "1.0.0"):
          self.spec = {
              "openapi": "3.0.0",
              "info": {
                  "title": title,
                  "version": version
              },
              "servers": [],
              "paths": {},
              "components": {
                  "schemas": {},
                  "securitySchemes": {}
              }
          }

      def add_server(self, url: str, description: Optional[str] = None):
          """Add server URL."""
          server = {"url": url}
          if description:
              server["description"] = description
          self.spec["servers"].append(server)
          return self

      def add_path(
          self,
          path: str,
          method: str,
          summary: Optional[str] = None,
          description: Optional[str] = None,
          parameters: Optional[List[Dict]] = None,
          request_body: Optional[Dict] = None,
          responses: Optional[Dict[int, Dict]] = None,
          tags: Optional[List[str]] = None
      ):
          """Add path to specification."""
          if path not in self.spec["paths"]:
              self.spec["paths"][path] = {}

          operation: Dict[str, Any] = {}
          if summary:
              operation["summary"] = summary
          if description:
              operation["description"] = description
          if parameters:
              operation["parameters"] = parameters
          if request_body:
              operation["requestBody"] = request_body
          if responses:
              operation["responses"] = responses
          if tags:
              operation["tags"] = tags

          self.spec["paths"][path][method.lower()] = operation
          return self

      def add_schema(self, name: str, schema: Dict[str, Any]):
          """Add schema to components."""
          self.spec["components"]["schemas"][name] = schema
          return self

      def add_security_scheme(
          self,
          name: str,
          scheme_type: str,
          scheme: Optional[str] = None,
          bearer_format: Optional[str] = None
      ):
          """Add security scheme."""
          security_scheme: Dict[str, Any] = {"type": scheme_type}
          if scheme:
              security_scheme["scheme"] = scheme
          if bearer_format:
              security_scheme["bearerFormat"] = bearer_format

          self.spec["components"]["securitySchemes"][name] = security_scheme
          return self

      def generate(self) -> Dict[str, Any]:
          """Generate complete OpenAPI spec."""
          return self.spec
  </content>
  </create>


PHASE 14: WEBHOOK HANDLING


webhook signature verification

  <create>
  <file>src/api/webhooks.py</file>
  <content>
  """Webhook signature verification and handling."""
  from typing import Optional, Callable
  from hashlib import hmac, sha256, sha512
  import json


  class WebhookVerifier:
      """Verify webhook signatures."""

      def __init__(self, secret: str, header_name: str = "X-Signature"):
          self.secret = secret
          self.header_name = header_name

      def verify(self, payload: bytes, signature: str) -> bool:
          """Verify webhook signature."""
          expected = self._compute_signature(payload)
          return hmac.compare_digest(expected, signature)

      def _compute_signature(self, payload: bytes) -> str:
          """Compute HMAC signature."""
          mac = hmac.new(
              self.secret.encode(),
              payload,
              sha256
          )
          return f"sha256={mac.hexdigest()}"


  class WebhookHandler:
      """Handle incoming webhooks."""

      def __init__(self, verifier: WebhookVerifier):
          self.verifier = verifier
          self.handlers: Dict[str, Callable] = {}

      def on(self, event_type: str) -> Callable:
          """Decorator to register handler for event type."""
          def decorator(func: Callable):
              self.handlers[event_type] = func
              return func
          return decorator

      def handle(self, payload: bytes, signature: str) -> Optional[Any]:
          """Handle incoming webhook."""
          if not self.verifier.verify(payload, signature):
              raise ValueError("Invalid webhook signature")

          data = json.loads(payload)
          event_type = data.get("type") or data.get("event")

          if event_type in self.handlers:
              return self.handlers[event_type](data)

          return None
  </content>
  </create>


PHASE 15: API INTEGRATION RULES


while this skill is active, these rules are MANDATORY:

  [1] ALWAYS implement rate limiting
      never assume the API can handle unlimited requests
      implement client-side limits even if server has limits

  [2] NEVER hardcode API credentials
      use environment variables or secure vaults
      add .env to .gitignore immediately

  [3] ALWAYS validate responses
      use pydantic models for type safety
      never trust API documentation alone

  [4] IMPLEMENT retry logic with exponential backoff
      transient failures are common
      use jitter to prevent thundering herd

  [5] LOG all API calls
      log request, response, duration
      sanitize sensitive data in logs

  [6] HANDLE errors specifically
      catch specific exceptions, not generic Exception
      map API errors to domain errors

  [7] USE async clients for high-volume operations
      httpx > requests for concurrent requests
      respect connection limits

  [8] CACHE when appropriate
      cache GET requests that rarely change
      respect cache-control headers

  [9] TIMEOUT every request
      never wait forever
      set reasonable defaults (30s for sync, 60s for async)

  [10] WRITE tests for API integration
      mock responses in unit tests
      consider VCR for recording real responses


FINAL REMINDERS


api integration is about reliability

the best API integration is one that doesnt break.
handle edge cases. handle failures. handle rate limits.


documentation is your friend

read the docs. bookmark the reference.
save the openapi spec if available.
understand the errors before they happen.


observability is non-negotiable

log everything. measure everything.
you cant fix what you cant see.


when the api fails

your application should degrade gracefully.
show cached data. show a friendly error.
never crash the whole app because one api failed.


start simple, add complexity gradually

basic client first. then auth. then retries. then caching.
each layer builds on the previous.

now go integrate some apis.
