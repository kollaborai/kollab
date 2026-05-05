---
name: security-hardening
description: "Security Hardening skill - secure code from the start"
---

security-hardening mode: SECURITY FIRST, ALWAYS

when this skill is active, you follow security best practices.
this is a comprehensive guide to writing secure, production-ready code.


PHASE 0: ENVIRONMENT VERIFICATION

before writing ANY code, verify security tools are available.


check for security linters

  <terminal>python -m bandit --version 2>/dev/null || echo "bandit not installed"</terminal>

if bandit not installed:
  <terminal>pip install bandit</terminal>


check for dependency vulnerability scanner

  <terminal>python -m safety --version 2>/dev/null || echo "safety not installed"</terminal>

if safety not installed:
  <terminal>pip install safety</terminal>


check for secrets detection

  <terminal>git-secrets --version 2>/dev/null || echo "git-secrets not installed"</terminal>

git-secrets installation varies by platform:
  <terminal>brew install git-secrets</terminal>  # macOS
  <terminal>apt install git-secrets</terminal>   # Ubuntu/Debian

initialize git-secrets in repo if not already done:
  <terminal>git secrets --install</terminal>
  <terminal>git secrets --register-aws</terminal>


check for pre-commit hooks

  <terminal>pre-commit --version 2>/dev/null || echo "pre-commit not installed"</terminal>

if pre-commit not installed:
  <terminal>pip install pre-commit</terminal>


check for existing security configuration

  <terminal>ls -la .bandit 2>/dev/null || echo "no bandit config"</terminal>
  <terminal>cat .gitignore 2>/dev/null | grep -E "\.env|secret|key" || echo "no secrets in gitignore"</terminal>


verify project security setup

  <terminal>find . -name "*.py" -type f | head -5 | xargs -I {} bandit {} 2>&1 | head -20</terminal>

  <terminal>safety check --bare 2>&1 | head -20</terminal>


PHASE 1: THE SECURITY MINDSET


think like an attacker

every input is malicious until proven otherwise.
every user request could be an attack.
every external system might be compromised.

questions to ask for every feature:
  [1] what if the input is malicious?
  [2] what if the user is not who they claim?
  [3] what if the database is compromised?
  [4] what if the external API is down or compromised?
  [5] what if secrets are leaked?

secure by default principles:

  deny by default, allow by exception
  fail securely (closed, not open)
  principle of least privilege
  defense in depth (multiple layers)
  minimize attack surface


the owasp top 10 (2021)

  [1] broken access control
  [2] cryptographic failures
  [3] injection
  [4] insecure design
  [5] security misconfiguration
  [6] vulnerable and outdated components
  [7] identification and authentication failures
  [8] software and data integrity failures
  [9] security logging and monitoring failures
  [10] server-side request forgery

memorize these. they cover 90% of security vulnerabilities.


PHASE 2: INPUT VALIDATION FUNDAMENTALS


never trust input

sources of untrusted input:
  [ok] user input forms
  [ok] url parameters and query strings
  [ok] http headers
  [ok] cookies
  [ok] file uploads
  [ok] webhooks
  [ok] api requests from any source
  [ok] database data (may be historic/compromised)


validation layers

layer 1: schema validation
  verify structure, types, required fields

layer 2: business rule validation
  verify value ranges, relationships, constraints

layer 3: sanitization
  remove/escape dangerous content

always validate in this order, never skip layers.


whitelist vs blacklist

  # bad - blacklist (you'll miss something)
  def sanitize_filename(filename):
      dangerous = ["../", "..\\", "/etc/", "C:\\"]
      for d in dangerous:
          filename = filename.replace(d, "")
      return filename

  # good - whitelist (only allow known safe)
  import re

  def sanitize_filename(filename):
      # only allow alphanumeric, dash, underscore, dot
      cleaned = re.sub(r"[^a-zA-Z0-9._-]", "", filename)
      # remove leading dots/dashes to prevent directory traversal
      cleaned = cleaned.lstrip(".-")
      return cleaned


validation examples

  import re
  from typing import Optional
  from dataclasses import dataclass


  @dataclass
  class ValidatedEmail:
      """A validated email address."""
      value: str

      def __post_init__(self):
          if not self._is_valid():
              raise ValueError(f"Invalid email: {self.value}")

      def _is_valid(self) -> bool:
          pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
          return bool(re.match(pattern, self.value))

      def __str__(self) -> str:
          return self.value


  # usage
  try:
      email = ValidatedEmail(user_input)
      # safe to use email.value
  except ValueError as e:
      return {"error": str(e)}, 400


length limits always

  def validate_username(username: str) -> str:
      if not username:
          raise ValueError("Username required")
      if len(username) < 3:
          raise ValueError("Username too short")
      if len(username) > 50:
          raise ValueError("Username too long")
      if not re.match(r"^[a-zA-Z0-9_-]+$", username):
          raise ValueError("Invalid characters")
      return username


PHASE 3: PREVENTING INJECTION ATTACKS


sql injection

the classic vulnerability:

  # vulnerable - never do this
  def get_user(user_id):
      query = f"SELECT * FROM users WHERE id = {user_id}"
      return db.execute(query)

  # attack: "1 OR 1=1; DROP TABLE users; --"


prevention: parameterized queries

  # safe - using parameterized queries
  import sqlite3

  def get_user(user_id: int):
      query = "SELECT * FROM users WHERE id = ?"
      cursor = db.execute(query, (user_id,))
      return cursor.fetchone()

  # safe - with ORM
  from sqlalchemy import text

  def get_user(user_id: int):
      return db.session.query(User).filter(User.id == user_id).first()


  # safe - with explicit type conversion
  def get_user(user_id: str) -> Optional[User]:
      try:
          user_id_int = int(user_id)  # force integer
      except ValueError:
          raise ValueError("Invalid user ID")
      return User.query.get(user_id_int)


dynamic queries still need parameters

  # vulnerable even with some parameters
  def search_users(column: str, value: str):
      query = f"SELECT * FROM users WHERE {column} = ?"
      return db.execute(query, (value,))

  # attack: column = "id OR 1=1; DROP TABLE users; --"


  # safe - validate column name against whitelist
  ALLOWED_COLUMNS = {"id", "username", "email", "created_at"}

  def search_users(column: str, value: str):
      if column not in ALLOWED_COLUMNS:
          raise ValueError(f"Invalid column: {column}")
      query = f"SELECT * FROM users WHERE {column} = ?"
      return db.execute(query, (value,))


nosql injection

  # vulnerable - mongodb injection
  def find_user(username, password):
      query = {"username": username, "password": password}
      return db.users.find_one(query)

  # attack: {"$ne": null} for username finds first user


  # safe - use type-safe queries
  from typing import Dict, Any

  def find_user(username: str, password: str) -> Optional[Dict[str, Any]]:
      if not isinstance(username, str) or not isinstance(password, str):
          raise TypeError("Username and password must be strings")
      return db.users.find_one({
          "username": username,
          "password": password  # hash this first!
      })


command injection

  # vulnerable - never pass user input to shell
  import subprocess

  def process_file(filename):
      result = subprocess.run(
          f"process_file {filename}",
          shell=True,  # DANGEROUS
          capture_output=True
      )
      return result.stdout

  # attack: filename = "file.txt; rm -rf /; #"


  # safe - use list argument (no shell)
  def process_file(filename: str):
      # validate filename first
      safe_filename = sanitize_filename(filename)
      result = subprocess.run(
          ["process_file", safe_filename],
          shell=False,  # safe default
          capture_output=True
      )
      return result.stdout


  # safe alternative - use python libraries
  def process_pdf(filename: str):
      safe_filename = sanitize_filename(filename)
      # use PyPDF2 instead of calling external tool
      import PyPDF2
      with open(safe_filename, 'rb') as f:
          reader = PyPDF2.PdfReader(f)
          return reader.pages[0].extract_text()


template injection

  # vulnerable - jinja2 with user input
  from jinja2 import Template

  def render_greeting(template_str, name):
      template = Template(template_str)  # user controls template!
      return template.render(name=name)

  # attack: template_str = "{{config.items()}}"


  # safe - separate template from data
  from jinja2 import Environment, FileSystemLoader

  env = Environment(loader=FileSystemLoader('templates/'))
  template = env.get_template('greeting.html')  # fixed template

  def render_greeting(name):
      return template.render(name=name)


PHASE 4: CROSS-SITE SCRIPTING (XSS) PREVENTION


xss attack types

  [1] stored xss - malicious code saved to database, shown to visitors
  [2] reflected xss - malicious code in url, reflected back in response
  [3] dom-based xss - malicious code executes in browser via javascript


output encoding

  # vulnerable - raw output
  def show_comment(comment):
      return f"<div>{comment}</div>"

  # attack: comment = "<script>alert('XSS')</script>"


  # safe - html escape
  from html import escape

  def show_comment(comment):
      escaped = escape(comment)
      return f"<div>{escaped}</div>"

  # result: &lt;script&gt;alert('XSS')&lt;/script&gt;


  # safe - with template engine (auto-escaped)
  from jinja2 import Template

  template = Template("<div>{{ comment }}</div>", autoescape=True)
  result = template.render(comment=user_input)


context matters

  def render_attribute(value):
      # html body context
      body = f"<div>{escape(value)}</div>"
      # attribute context needs different escaping
      attr = f'<div data-value="{escape(value, quote=True)}">'
      # url context needs url encoding
      import urllib.parse
      url = f"/search?q={urllib.parse.quote(value)}"
      return body


content security policy

add csp headers:

  from flask import Flask, Response

  app = Flask(__name__)

  @app.after_request
  def add_security_headers(response):
      csp = (
          "default-src 'self'; "
          "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
          "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
          "img-src 'self' data: https:; "
          "font-src 'self' cdn.jsdelivr.net; "
          "connect-src 'self' api.example.com; "
          "frame-ancestors 'none'; "
          "base-uri 'self'; "
          "form-action 'self';"
      )
      response.headers['Content-Security-Policy'] = csp
      response.headers['X-Content-Type-Options'] = 'nosniff'
      response.headers['X-Frame-Options'] = 'DENY'
      response.headers['X-XSS-Protection'] = '1; mode=block'
      return response


httponly cookies

  from flask import make_response

  response = make_response("Login successful")
  response.set_cookie(
      'session_token',
      token,
      httponly=True,      # prevents javascript access
      secure=True,        # only send over https
      samesite='Lax',     # csrf protection
      max_age=3600
  )
  return response


PHASE 5: CROSS-SITE REQUEST FORGERY (CSRF) PREVENTION


how csrf works

  1. user logs into bank.com, gets session cookie
  2. user visits evil.com
  3. evil.com has form/action to bank.com/transfer
  4. browser sends bank.com cookies automatically
  5. transfer executes with user's credentials


synchronizer token pattern

  import secrets
  from flask import session, request

  def generate_csrf_token():
      if 'csrf_token' not in session:
          session['csrf_token'] = secrets.token_hex(32)
      return session['csrf_token']


  def validate_csrf_token():
      token = session.get('csrf_token')
      if not token or token != request.form.get('csrf_token'):
          raise ValueError("Invalid CSRF token")


  # form template
  def render_transfer_form():
      return f"""
      <form method="POST" action="/transfer">
          <input type="hidden" name="csrf_token" value="{generate_csrf_token()}">
          <input name="to_account" placeholder="Recipient">
          <input name="amount" type="number" placeholder="Amount">
          <button type="submit">Transfer</button>
      </form>
      """


  # form handler
  def handle_transfer():
      validate_csrf_token()  # must be first
      to_account = request.form['to_account']
      amount = request.form['amount']
      # ... process transfer ...


double submit cookie pattern

alternative when sessions aren't available:

  import secrets

  def set_csrf_cookie(response):
      token = secrets.token_hex(32)
      response.set_cookie(
          'csrf_token',
          token,
          httponly=True,
          secure=True,
          samesite='Strict'
      )
      return token


  def validate_csrf_double_submit():
      cookie_token = request.cookies.get('csrf_token')
      form_token = request.form.get('csrf_token')
      if not cookie_token or cookie_token != form_token:
          raise ValueError("CSRF validation failed")


sameSite cookies

modern browsers support sameSite attribute:

  # strict - best security
  response.set_cookie('session', token, samesite='Strict')

  # lax - allows top-level navigations
  response.set_cookie('session', token, samesite='Lax')

  # none - for cross-origin requests (requires secure)
  response.set_cookie('session', token, samesite='None', secure=True)


PHASE 6: AUTHENTICATION AND AUTHORIZATION


secure password handling

import bcrypt
import secrets
from typing import Optional


def hash_password(plain_password: str) -> str:
    """Hash a password using bcrypt."""
    if len(plain_password) > 72:
        raise ValueError("Password too long for bcrypt")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain_password.encode('utf-8'), salt).decode('utf-8')


def verify_password(plain_password: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed.encode('utf-8')
        )
    except Exception:
        return False


password requirements

import re

def validate_password_strength(password: str) -> list[str]:
    """Validate password strength, returning list of errors."""
    errors = []

    if len(password) < 12:
        errors.append("Password must be at least 12 characters")

    if len(password) > 128:
        errors.append("Password too long (max 128 characters)")

    if not re.search(r'[a-z]', password):
        errors.append("Password must contain lowercase letters")

    if not re.search(r'[A-Z]', password):
        errors.append("Password must contain uppercase letters")

    if not re.search(r'[0-9]', password):
        errors.append("Password must contain digits")

    if not re.search(r'[^a-zA-Z0-9]', password):
        errors.append("Password must contain special characters")

    # check for common passwords
    common = ["password", "123456", "qwerty", "admin", "welcome"]
    lower = password.lower()
    for common_pwd in common:
        if common_pwd in lower:
            errors.append(f"Password contains common word: {common_pwd}")

    return errors


secure session management

import secrets
from datetime import datetime, timedelta
from typing import Optional

SESSION_DURATION = timedelta(hours=1)


def create_session(user_id: int) -> str:
    """Create a new session token."""
    token = secrets.token_urlsafe(32)
    # store in database with expiration
    db.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, datetime.now() + SESSION_DURATION)
    )
    return token


def validate_session(token: str) -> Optional[int]:
    """Validate session token and return user_id."""
    session = db.execute(
        "SELECT user_id, expires_at FROM sessions WHERE token = ?",
        (token,)
    ).fetchone()

    if not session:
        return None

    if datetime.now() > session['expires_at']:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return None

    # rotate session periodically
    if random.random() < 0.1:  # 10% chance to rotate
        new_token = create_session(session['user_id'])
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return new_token

    return session['user_id']


def revoke_session(token: str) -> None:
    """Revoke a session token."""
    db.execute("DELETE FROM sessions WHERE token = ?", (token,))


def revoke_all_user_sessions(user_id: int) -> None:
    """Revoke all sessions for a user."""
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


authorization checks

from functools import wraps
from flask import g, jsonify

def require_role(*roles):
    """Decorator to require specific user roles."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not hasattr(g, 'user') or g.user is None:
                return jsonify({"error": "Authentication required"}), 401

            if g.user['role'] not in roles:
                return jsonify({"error": "Insufficient permissions"}), 403

            return f(*args, **kwargs)
        return wrapped
    return decorator


def require_ownership(resource_type: str):
    """Decorator to require user owns the resource."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            resource_id = kwargs.get('id')
            user_id = g.user['id']

            # check ownership in database
            owner_id = db.execute(
                f"SELECT user_id FROM {resource_type} WHERE id = ?",
                (resource_id,)
            ).fetchone()

            if not owner_id or owner_id['user_id'] != user_id:
                return jsonify({"error": "Access denied"}), 403

            return f(*args, **kwargs)
        return wrapped
    return decorator


# usage
@app.route('/admin/users')
@require_role('admin')
def admin_users():
    return jsonify({"users": list_users()})


@app.route('/api/posts/<int:id>', methods=['DELETE'])
@require_ownership('posts')
def delete_post(id):
    return delete_post(id)


rate limiting login

from functools import lru_cache
import time

MAX_ATTEMPTS = 5
LOCKOUT_DURATION = 900  # 15 minutes

@lru_cache(maxsize=10000)
def get_login_attempts(identifier: str) -> tuple[int, float]:
    """Get (attempt_count, last_attempt_time) for identifier."""
    return (0, 0)


def record_failed_login(identifier: str) -> bool:
    """Record failed login, return True if locked out."""
    attempts, last_time = get_login_attempts(identifier)
    now = time.time()

    # reset if lockout period passed
    if now - last_time > LOCKOUT_DURATION:
        attempts = 0

    attempts += 1
    # cache with expiration would be better
    return attempts >= MAX_ATTEMPTS


def is_locked_out(identifier: str) -> bool:
    """Check if identifier is currently locked out."""
    attempts, last_time = get_login_attempts(identifier)
    if attempts >= MAX_ATTEMPTS:
        if time.time() - last_time < LOCKOUT_DURATION:
            return True
    return False


PHASE 7: SECRETS MANAGEMENT


never hardcode secrets

  [x] api keys in source code
  [x] database passwords in source code
  [x] jwt secrets in source code
  [x] private keys in repository
  [x] credentials in config files

  [ok] environment variables
  [ok] secret management services
  [ok] encrypted config files
  [ok] runtime secret injection


environment variables

import os
from typing import Optional

def get_required_env(key: str) -> str:
    """Get required environment variable or raise error."""
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Required environment variable {key} not set")
    return value


def get_optional_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get optional environment variable with default."""
    return os.getenv(key, default)


# usage
database_url = get_required_env('DATABASE_URL')
api_key = get_optional_env('API_KEY', 'default-key')


secret validation

def validate_database_url(url: str) -> str:
    """Validate database URL format."""
    if not url.startswith(('postgresql://', 'postgres://', 'mysql://')):
        raise ValueError("Invalid database URL scheme")

    # check for credentials in url
    # warn if not using ssl
    if '?' not in url or 'sslmode' not in url:
        raise ValueError("Database connection must use SSL")

    return url


secrets file with git-secrets

# .gitignore should include:
  .env
  .env.local
  .env.*.local
  *.key
  *.pem
  secrets.json
  .secrets/

# git-secrets patterns to add:
  git secrets --add 'password\s*=\s*["\']?[^"\']+$'
  git secrets --add 'api_key\s*=\s*["\']?[^"\']+$'
  git secrets --add 'secret\s*=\s*["\']?[^"\']+$'
  git secrets --add 'AKIA[0-9A-Z]{16}'  # AWS access keys


rotate secrets

from datetime import datetime, timedelta
import secrets

class SecretRotator:
    """Manage secret rotation."""

    def __init__(self, max_age_days: int = 90):
        self.max_age = timedelta(days=max_age_days)

    def should_rotate(self, created_at: datetime) -> bool:
        """Check if secret needs rotation."""
        return datetime.now() - created_at > self.max_age

    def generate_new_secret(self) -> str:
        """Generate a new secret."""
        return secrets.token_urlsafe(32)

    def rotate_api_key(self, old_key: str) -> str:
        """Rotate an API key."""
        new_key = self.generate_new_secret()
        # store both temporarily for graceful transition
        db.execute(
            "INSERT INTO api_keys (key, old_key, expires_at) VALUES (?, ?, ?)",
            (new_key, old_key, datetime.now() + timedelta(hours=24))
        )
        return new_key


PHASE 8: CRYPTOGRAPHY BEST PRACTICES


use established libraries

  [ok] cryptography.io - general cryptography
  [ok] PyNaCl - modern crypto (libsodium bindings)
  [ok] bcrypt - password hashing
  [x] hashlib.md5 - broken for security
  [x] hashlib.sha1 - broken for security
  [x] custom crypto algorithms - never roll your own


secure hashing

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2

def derive_key(password: bytes, salt: bytes) -> bytes:
    """Derive a key from password using PBKDF2."""
    kdf = PBKDF2(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,  # owasp recommendation
    )
    return kdf.derive(password)


secure random

import secrets

# for tokens, session ids, api keys
token = secrets.token_hex(32)      # 64 hex characters
token = secrets.token_urlsafe(32)  # url-safe base64
token = secrets.token_bytes(32)    # raw bytes

# never use:
  random.random()      # predictable
  random.randint()     # predictable
  os.urandom()         # okay, but secrets is better


encryption at rest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def encrypt_data(data: bytes, key: bytes) -> bytes:
    """Encrypt data using AES-GCM (authenticated encryption)."""
    aesgcm = AESGCM(key)
    nonce = secrets.token_bytes(12)  # 96-bit nonce for GCM
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return nonce + ciphertext  # prepend nonce for storage


def decrypt_data(ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt data using AES-GCM."""
    aesgcm = AESGCM(key)
    nonce = ciphertext[:12]  # extract nonce
    return aesgcm.decrypt(nonce, ciphertext[12:], None)


# usage
key = secrets.token_bytes(32)  # 256-bit key
encrypted = encrypt_data(sensitive_data.encode(), key)
decrypted = decrypt_data(encrypted, key)


PHASE 9: DEPENDENCY SECURITY


audit dependencies regularly

  <terminal>safety check --full-report</terminal>

  <terminal>pip install pip-audit</terminal>
  <terminal>pip-audit</terminal>


  <terminal>pip install bandit</terminal>
  <terminal>bandit -r . -f json -o security-report.json</terminal>


pin dependencies

requirements.txt with hashes:

  # generate with:
  <terminal>pip freeze > requirements.txt</terminal>
  <terminal>pip hash <requirements.txt > requirements-hashes.txt</terminal>

  # or use pip-tools:
  <terminal>pip install pip-tools</terminal>
  <terminal>pip-compile requirements.in --generate-hashes</terminal>


lock files

use pyproject.toml with poetry or pipenv:

  [tool.poetry.dependencies]
  python = "^3.11"
  fastapi = "^2.0.0"
  pydantic = "^2.0.0"

  # lock file pins exact versions:
  # poetry.lock


vulnerability scanning in ci

  # .github/workflows/security.yml
  name: Security Scan

  on: [push, pull_request]

  jobs:
    security:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3

        - name: Run Safety Check
          run: |
            pip install safety
            safety check --continue-on-error

        - name: Run Bandit
          run: |
            pip install bandit
            bandit -r . -f json -o bandit-report.json

        - name: Upload Reports
          uses: actions/upload-artifact@v3
          with:
            name: security-reports
            path: |
              bandit-report.json


abandonware detection

check for unmaintained packages:

  <terminal>pip install depcheck</terminal>
  <terminal>depcheck</terminal>

manually verify:
  - last release date on pypi
  - open issues/prs on github
  - security advisories


PHASE 10: SECURE CONFIGURATION


secure defaults

import os

class Config:
    """Secure configuration defaults."""

    # security
    SECRET_KEY = os.getenv('SECRET_KEY') or secrets.token_hex(32)
    DEBUG = False  # always false in production
    TESTING = False

    # sessions
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=1)

    # ssl/tls
    FORCE_HTTPS = True
    HSTS_ENABLED = True
    HSTS_MAX_AGE = 31536000  # 1 year

    # limits
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    MAX_REQUEST_SIZE = 1024 * 1024  # 1MB

    # rate limiting
    RATE_LIMIT_ENABLED = True
    RATE_LIMIT_PER_MINUTE = 60


environment-specific configs

class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    TESTING = False

    # allow http in dev
    FORCE_HTTPS = False
    SESSION_COOKIE_SECURE = False


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    DEBUG = True

    # use in-memory database for tests
    DATABASE_URL = 'sqlite:///:memory:'


class ProductionConfig(Config):
    """Production configuration - most secure."""
    DEBUG = False
    TESTING = False

    # enforce security
    FORCE_HTTPS = True
    HSTS_ENABLED = True

    # production-specific
    SENTRY_DSN = os.getenv('SENTRY_DSN')


config_by_env = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
}

def get_config() -> Config:
    """Get config based on environment."""
    env = os.getenv('FLASK_ENV', 'production')
    return config_by_env.get(env, ProductionConfig)()


disable debug in production

critical check:

def ensure_production_safety():
    """Fail fast if debug mode enabled in production."""
    env = os.getenv('FLASK_ENV', 'production')
    debug = os.getenv('DEBUG', 'false').lower() == 'true'

    if env == 'production' and debug:
        raise RuntimeError(
            "DEBUG mode enabled in production. "
            "This exposes sensitive information and is a security risk."
        )


PHASE 11: LOGGING AND MONITORING


security event logging

import logging
from datetime import datetime

security_logger = logging.getLogger('security')

class SecurityEvent:
    """Log security-relevant events."""

    EVENTS = {
        'auth_success',
        'auth_failure',
        'permission_denied',
        'rate_limit_exceeded',
        'suspicious_input',
        'csrf_failure',
        'injection_attempt',
    }

    @classmethod
    def log(cls, event_type: str, user_id: Optional[int],
            details: dict, ip: str, user_agent: str):
        """Log a security event."""
        if event_type not in cls.EVENTS:
            raise ValueError(f"Unknown event type: {event_type}")

        security_logger.warning({
            'event': event_type,
            'timestamp': datetime.now().isoformat(),
            'user_id': user_id,
            'ip': ip,
            'user_agent': user_agent,
            'details': details,
        })


# usage
SecurityEvent.log(
    'auth_failure',
    user_id=None,
    details={'username': input_username},
    ip=request.remote_addr,
    user_agent=request.headers.get('User-Agent', '')
)


sanitize logs

def sanitize_log_data(data: dict) -> dict:
    """Remove sensitive data before logging."""
    sensitive_keys = {
        'password', 'passwd', 'pwd',
        'token', 'secret', 'api_key',
        'ssn', 'credit_card', 'cc',
        'session', 'cookie',
    }

    cleaned = {}
    for key, value in data.items():
        if any(sensitive in key.lower() for sensitive in sensitive_keys):
            cleaned[key] = '[REDACTED]'
        elif isinstance(value, dict):
            cleaned[key] = sanitize_log_data(value)
        else:
            cleaned[key] = value
    return cleaned


log injection prevention

import re

def sanitize_log_input(text: str) -> str:
    """Prevent log injection attacks."""
    # remove crlf characters
    text = re.sub(r'[\r\n]', '', text)
    # limit length
    text = text[:1000]
    return text


PHASE 12: API SECURITY


api key management

from typing import Optional
import secrets

class APIKeyManager:
    """Manage API keys for external access."""

    def create_key(self, user_id: int, name: str) -> str:
        """Create a new API key."""
        prefix = f"kk_{user_id}_"
        key_secret = secrets.token_urlsafe(32)
        full_key = f"{prefix}{key_secret}"

        # store hash, not the key itself
        key_hash = hash_api_key(full_key)
        db.execute(
            "INSERT INTO api_keys (user_id, name, key_hash, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, name, key_hash, datetime.now())
        )
        return full_key

    def validate_key(self, key: str) -> Optional[dict]:
        """Validate an API key and return user info."""
        key_hash = hash_api_key(key)
        result = db.execute(
            "SELECT user_id, name, scopes FROM api_keys WHERE key_hash = ? AND active = 1",
            (key_hash,)
        ).fetchone()
        return result


def hash_api_key(key: str) -> str:
    """Hash an API key for storage."""
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()


rate limiting

from functools import wraps
import time
from collections import defaultdict

class RateLimiter:
    """Simple in-memory rate limiter."""

    def __init__(self, requests_per_minute: int = 60):
        self.rpm = requests_per_minute
        self.requests = defaultdict(list)

    def is_allowed(self, identifier: str) -> bool:
        """Check if request is allowed."""
        now = time.time()
        minute_ago = now - 60

        # clean old requests
        self.requests[identifier] = [
            t for t in self.requests[identifier] if t > minute_ago
        ]

        if len(self.requests[identifier]) >= self.rpm:
            return False

        self.requests[identifier].append(now)
        return True


# decorator
rate_limiter = RateLimiter(requests_per_minute=60)

def rate_limit(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        identifier = request.remote_addr
        if not rate_limiter.is_allowed(identifier):
            return jsonify({"error": "Rate limit exceeded"}), 429
        return f(*args, **kwargs)
    return wrapped


api versioning

@app.route('/api/v1/users')
def list_users_v1():
    # deprecated but maintained
    return jsonify({"users": get_users()})


@app.route('/api/v2/users')
def list_users_v2():
    # current version with security improvements
    return jsonify({"users": get_users_v2()})


PHASE 13: FILE UPLOAD SECURITY


validate file uploads

import imghdr
import os
from pathlib import Path

ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.pdf'}
ALLOWED_MIMES = {
    'image/jpeg', 'image/png', 'image/gif', 'application/pdf'
}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def validate_upload(file) -> tuple[bool, str]:
    """Validate an uploaded file."""
    # check file size
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)

    if size > MAX_FILE_SIZE:
        return False, f"File too large (max {MAX_FILE_SIZE} bytes)"
    if size == 0:
        return False, "Empty file"

    # check extension
    filename = Path(file.filename)
    if filename.suffix.lower() not in ALLOWED_EXTENSIONS:
        return False, f"Invalid file type. Allowed: {ALLOWED_EXTENSIONS}"

    # check mime type
    mime = file.content_type
    if mime not in ALLOWED_MIMES:
        return False, f"Invalid content type: {mime}"

    # for images, verify actual content
    if mime.startswith('image/'):
        header = file.read(32)
        file.seek(0)
        if not imghdr.what(None, header):
            return False, "Invalid image file"

    return True, "Valid"


sanitize filenames

import re
from datetime import datetime

def sanitize_upload_filename(filename: str) -> str:
    """Generate safe filename for upload."""
    # extract extension
    parts = filename.rsplit('.', 1)
    if len(parts) != 2:
        raise ValueError("File must have extension")
    name, ext = parts

    # sanitize name
    name = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
    name = name[:50]  # limit length

    # add timestamp for uniqueness
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    return f"{timestamp}_{name}.{ext.lower()}"


store outside webroot

def save_upload(file) -> str:
    """Save uploaded file securely."""
    is_valid, msg = validate_upload(file)
    if not is_valid:
        raise ValueError(msg)

    safe_name = sanitize_upload_filename(file.filename)

    # store outside web root
    upload_dir = Path('/var/app/uploads')
    upload_dir.mkdir(mode=0o750, exist_ok=True)

    file_path = upload_dir / safe_name
    file.save(str(file_path))

    # set restrictive permissions
    os.chmod(file_path, 0o640)

    # return identifier, not path
    return safe_name


serve via application

@app.route('/uploads/<filename>')
def serve_upload(filename):
    """Serve uploaded files through application (not direct access)."""
    # verify user has access to this file
    # check permissions
    # log access
    # then serve

    file_path = Path('/var/app/uploads') / filename
    if not file_path.exists():
        return "Not found", 404

    return send_file(file_path)


PHASE 14: DATABASE SECURITY


database connection security

from urllib.parse import parse_qs

def validate_db_url(url: str) -> str:
    """Ensure database URL is secure."""
    if not url.startswith(('postgresql://', 'mysql://')):
        raise ValueError("Only postgresql and mysql are supported")

    # require ssl
    if 'sslmode' not in url:
        raise ValueError("Database connection must use SSL")

    parsed = parse_qs(url)
    # verify no plaintext credentials in logs
    return url


least privilege database user

application should use limited database user:

  CREATE USER kollabor_app WITH PASSWORD 'secure_password';

  GRANT CONNECT ON DATABASE kollabor_db TO kollabor_app;

  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO kollabor_app;
  GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO kollabor_app;

  -- no create, drop, alter permissions


query result limits

def paginated_query(query: str, page: int, per_page: int = 50):
    """Execute query with pagination limits."""
    if per_page > 100:
        per_page = 100  # max limit
    if page < 1:
        page = 1

    offset = (page - 1) * per_page
    limited_query = f"{query} LIMIT {per_page} OFFSET {offset}"

    return db.execute(limited_query).fetchall()


PHASE 15: SECURITY TESTING


security unit tests

import pytest

def test_sql_injection_prevented():
    """Test that SQL injection is prevented."""
    malicious_id = "1 OR 1=1; DROP TABLE users; --"

    # should raise validation error
    with pytest.raises(ValueError):
        get_user(malicious_id)

    # or should only find user 1
    result = get_user_safe(malicious_id)
    assert result is None or result['id'] == 1


def test_xss_prevention():
    """Test that XSS is prevented."""
    xss_payload = "<script>alert('XSS')</script>"

    rendered = render_comment(xss_payload)

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_csrf_protection():
    """Test CSRF token validation."""
    client = TestClient(app)

    # request without token should fail
    response = client.post('/transfer', json={
        'to': 'victim',
        'amount': 100
    })
    assert response.status_code == 403

    # request with invalid token should fail
    response = client.post('/transfer', json={
        'csrf_token': 'invalid',
        'to': 'victim',
        'amount': 100
    })
    assert response.status_code == 403


def test_password_validation():
    """Test password strength requirements."""
    weak_passwords = [
        'password',
        'Password1',
        'short',
        'noooooooonumbers',
        '123456789012',
    ]

    for pwd in weak_passwords:
        errors = validate_password_strength(pwd)
        assert len(errors) > 0, f"Should reject: {pwd}"


security integration tests

def test_authentication_flow():
    """Test secure authentication flow."""
    client = TestClient(app)

    # register
    response = client.post('/register', json={
        'username': 'testuser',
        'password': 'SecurePass123!',
        'email': 'test@example.com'
    })
    assert response.status_code == 201

    # login with correct password
    response = client.post('/login', json={
        'username': 'testuser',
        'password': 'SecurePass123!'
    })
    assert response.status_code == 200
    assert 'token' in response.json()

    # login with wrong password fails
    response = client.post('/login', json={
        'username': 'testuser',
        'password': 'WrongPass123!'
    })
    assert response.status_code == 401


penetration testing

run security scans:

  <terminal>bandit -r . -f json -o bandit-report.json</terminal>

  <terminal>safety check --json > safety-report.json</terminal>

  <terminal>pip-audit --format json > pip-audit-report.json</terminal>

manual test checklist:
  [ ] try sql injection in all inputs
  [ ] try xss payloads in text fields
  [ ] test csrf without tokens
  [ ] try authentication bypass
  [ ] test idor (insecure direct object reference)
  [ ] test rate limiting
  [ ] try file upload exploits


PHASE 16: SECURITY RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] VALIDATE ALL INPUT
      never trust data from user, api, or database
      whitelist allowed values, don't blacklist bad ones

  [2] USE PARAMETERIZED QUERIES
      never concatenate strings into sql
      always use ? placeholders or orm

  [3] ESCAPE ALL OUTPUT
      html escape user content before rendering
      use template engines with autoescape

  [4] HASH PASSWORDS PROPERLY
      use bcrypt, argon2, or scrypt
      never store plaintext passwords
      never use md5, sha1, or custom algorithms

  [5] USE CSRF TOKENS
      all state-changing requests need csrf protection
      validate tokens on server side

  [6] ENABLE SECURITY HEADERS
      csp, x-frame-options, x-content-type-options
      httponly and secure cookies

  [7] NEVER EXPOSE SECRETS
      no api keys in source code
      use environment variables or secret managers
      add sensitive patterns to git-secrets

  [8] IMPLEMENT RATE LIMITING
      protect authentication endpoints
      protect api endpoints
      log rate limit violations

  [9] LOG SECURITY EVENTS
      auth failures
      permission denials
      suspicious activity

  [10] KEEP DEPENDENCIES UPDATED
       run security scans regularly
       update vulnerable packages promptly

  [11] DISABLE DEBUG IN PRODUCTION
       debug mode exposes sensitive information
       fail fast if debug enabled in production

  [12] USE HTTPS EVERYWHERE
       redirect http to https
       enable hsts
       secure cookies only

  [13] PRINCIPLE OF LEAST PRIVILEGE
       users get minimum required access
       services get minimum required permissions

  [14] DEFENSE IN DEPTH
       multiple layers of security
       if one layer fails, others protect

  [15] SECURITY BY DEFAULT
       secure options should be default
       users should have to opt-in to less secure


PHASE 17: SECURITY CHECKLIST


before deploying to production:

input validation:
  [ ] all user input validated
  [ ] length limits enforced
  [ ] type checking implemented
  [ ] whitelist patterns used
  [ ] file uploads validated

authentication:
  [ ] passwords hashed with bcrypt/argon2
  [ ] session management secure
  [ ] csrf tokens implemented
  [ ] rate limiting on auth endpoints
  [ ] secure password requirements

authorization:
  [ ] access control on all endpoints
  [ ] ownership checks for resources
  [ ] role-based permissions
  [ ] admin actions protected

data protection:
  [ ] secrets in environment variables
  [ ] sensitive data encrypted at rest
  [ ] tls for data in transit
  [ ] database connections use ssl
  [ ] api keys stored hashed

output encoding:
  [ ] html escaping enabled
  [ ] template autoescape on
  [ ] json content-type headers
  [ ] csp headers configured

logging:
  [ ] security events logged
  [ ] sensitive data redacted
  [ ] log injection prevented
  [ ] log rotation configured

dependencies:
  [ ] no known vulnerabilities
  [ ] dependencies pinned
  [ ] security scanning in ci
  [ ] update process defined

infrastructure:
  [ ] https enforced
  [ ] hsts enabled
  [ ] security headers configured
  [ ] debug mode disabled
  [ ] backups encrypted

monitoring:
  [ ] failed auth alerts
  [ ] rate limit alerts
  [ ] anomaly detection
  [ ] incident response plan


FINAL REMINDERS


security is a process, not a feature

it starts at design.
it continues through development.
it doesn't end at deployment.


there is no secure software

there is only software that has been:
  - analyzed for vulnerabilities
  - tested against attacks
  - monitored for intrusions
  - updated when issues found


the attacker only needs to be right once

you need to be right every time.
defense in depth is essential.


when in doubt

err on the side of security.
validate one more time.
add one more layer of protection.
log one more event.


your responsibility

every line of code you write could introduce a vulnerability.
every feature you build needs security consideration.
every deployment needs security validation.

users trust you with their data.
their privacy.
their security.

don't betray that trust.


now go write secure code.
