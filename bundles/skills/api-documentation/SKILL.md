---
name: api-documentation
description: "API Documentation skill - write comprehensive API documentation from scratch"
---

api-documentation mode: DOCUMENT EVERY ENDPOINT

when this skill is active, you follow API documentation best practices.
this is a comprehensive guide to writing world-class API documentation.


PHASE 0: PREREQUISITE DISCOVERY

before writing ANY API documentation, discover what exists.


check for existing API specifications

  <terminal>find . -name "openapi.yaml" -o -name "openapi.yml" -o -name "swagger.json" 2>/dev/null</terminal>
  <terminal>find . -name "*.spec.yaml" -o -name "*.spec.yml" 2>/dev/null</terminal>

if OpenAPI spec exists:
  <read><file>openapi.yaml</file></read>
  analyze current completeness
  identify missing sections

if no spec exists:
  create one from scratch
  this becomes the source of truth


check for existing documentation

  <terminal>find . -type d -name "docs" 2>/dev/null</terminal>
  <terminal>ls -la docs/ 2>/dev/null || echo "no docs directory"</terminal>
  <terminal>find docs -name "*api*" -o -name "*endpoint*" 2>/dev/null</terminal>

read existing docs to understand:
  - documentation structure
  - writing style in use
  - formatting conventions
  - what's already documented


check for API source code

  <terminal>find . -name "*routes*.py" -o -name "*api*.py" -o -name "*controller*.py" 2>/dev/null | head -20</terminal>
  <terminal>find . -name "*routes*.js" -o -name "*api*.js" -o -name "*controller*.js" 2>/dev/null | head -20</terminal>
  <terminal>find . -name "*handlers*.go" 2>/dev/null | head -10</terminal>

for each route file found:
  <read><file>path/to/routes.py</file></read>
  extract endpoint definitions
  note request/response patterns
  identify authentication requirements


check for authentication setup

  <terminal>grep -r "jwt\|oauth\|api.*key\|bearer\|auth" --include="*.py" --include="*.js" . 2>/dev/null | head -20</terminal>
  <terminal>find . -name "*auth*.py" -o -name "*auth*.js" 2>/dev/null | head -10</terminal>

understand:
  - authentication method (JWT, OAuth, API Key)
  - where credentials are passed (header, query, body)
  - required scopes or permissions


check for testing files

  <terminal>find . -name "*test*api*.py" -o -name "*test*route*.py" 2>/dev/null | head -10</terminal>
  <terminal>find . -name "*test*api*.js" 2>/dev/null | head -10</terminal>

test files contain:
  - example requests
  - expected responses
  - error scenarios
  - authentication usage

these are GOLD for documentation examples.


PHASE 1: UNDERSTANDING YOUR API

before documenting, understand what the API does.


inventory all endpoints

create a complete endpoint inventory:

  method    path                    description
  -------   -------------------     ---------------------------
  GET       /api/users              list all users
  POST      /api/users              create new user
  GET       /api/users/{id}         get user by ID
  PUT       /api/users/{id}         update user
  DELETE    /api/users/{id}         delete user
  GET       /api/users/{id}/posts   get user's posts

use this command to find routes:
  <terminal>grep -r "@app\|@router\|@bp\.route\|\.get\|\.post\|\.put\|\.delete" --include="*.py" . 2>/dev/null</terminal>
  <terminal>grep -r "router\.\|app\." --include="*.js" . 2>/dev/null | grep -E "get|post|put|delete|patch" | head -30</terminal>


group endpoints by resource

organize into logical resource groups:

  users:
    GET    /api/users
    POST   /api/users
    GET    /api/users/{id}
    PUT    /api/users/{id}
    DELETE /api/users/{id}

  posts:
    GET    /api/posts
    POST   /api/posts
    GET    /api/posts/{id}
    PUT    /api/posts/{id}
    DELETE /api/posts/{id}
    GET    /api/posts/{id}/comments

  comments:
    POST   /api/posts/{id}/comments
    DELETE /api/comments/{id}

documentation should follow this grouping.


identify common patterns

look for patterns across endpoints:

  [ ] pagination (page, limit, offset)
  [ ] filtering (filter[field]=value)
  [ ] sorting (sort=field, order=asc|desc)
  [ ] search (q=query)
  [ ] field selection (fields=id,name)
  [ ] includes (include=related)
  [ ] versioning (/v1/, /v2/)
  [ ] rate limiting headers
  [ ] standard response format

document patterns ONCE, then reference them.


PHASE 2: OPENAPI SPECIFICATION STRUCTURE

the OpenAPI specification is your foundation.


create the base spec

  <create>
  <file>openapi.yaml</file>
  <content>
openapi: 3.0.3
info:
  title: My API
  description: |
    Detailed description of what this API does.

    ## Authentication

    All endpoints require authentication using API keys or JWT tokens.

    ## Rate Limiting

    Rate limits are enforced per API key.
  version: 1.0.0
  contact:
    name: API Support
    email: support@example.com
  license:
    name: MIT
    url: https://opensource.org/licenses/MIT

servers:
  - url: https://api.example.com/v1
    description: Production server
  - url: https://staging-api.example.com/v1
    description: Staging server
  - url: http://localhost:8000/v1
    description: Local development server

tags:
  - name: users
    description: User management operations
  - name: posts
    description: Blog post operations
  - name: auth
    description: Authentication operations

paths:
  # endpoints go here

components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
    apiKeyAuth:
      type: apiKey
      in: header
      name: X-API-Key

  schemas:
    Error:
      type: object
      required:
        - code
        - message
      properties:
        code:
          type: string
          example: "validation_error"
        message:
          type: string
          example: "Validation failed"
        details:
          type: object
          additionalProperties: true
  </content>
  </create>


document a single endpoint fully

  paths:
    /users:
      get:
        summary: List all users
        description: |
          Returns a paginated list of users. By default, returns 20 users per page.

          The response includes user profiles with basic information.
        tags:
          - users
        security:
          - bearerAuth: []
        parameters:
          - name: page
            in: query
            description: Page number for pagination
            required: false
            schema:
              type: integer
              default: 1
              minimum: 1
          - name: limit
            in: query
            description: Number of items per page
            required: false
            schema:
              type: integer
              default: 20
              minimum: 1
              maximum: 100
          - name: sort
            in: query
            description: Sort field and order
            required: false
            schema:
              type: string
              enum: [name_asc, name_desc, created_asc, created_desc]
              default: created_desc
          - name: status
            in: query
            description: Filter by user status
            required: false
            schema:
              type: string
              enum: [active, inactive, suspended]
        responses:
          '200':
            description: Successful response
            content:
              application/json:
                schema:
                  type: object
                  required:
                    - data
                    - pagination
                  properties:
                    data:
                      type: array
                      items:
                        $ref: '#/components/schemas/User'
                    pagination:
                      $ref: '#/components/schemas/Pagination'
                examples:
                  success:
                    summary: Successful response
                    value:
                      data:
                        - id: "1"
                          name: "Alice Johnson"
                          email: "alice@example.com"
                          status: "active"
                          created_at: "2024-01-15T10:30:00Z"
                      pagination:
                        page: 1
                        limit: 20
                        total: 145
                        pages: 8
          '400':
            description: Bad request - invalid parameters
            content:
              application/json:
                schema:
                  $ref: '#/components/schemas/Error'
                example:
                  code: "validation_error"
                  message: "Invalid sort parameter"
                  details:
                    field: "sort"
                    errors: ["Must be one of: name_asc, name_desc"]
          '401':
            description: Unauthorized - missing or invalid token
            content:
              application/json:
                schema:
                  $ref: '#/components/schemas/Error'
                example:
                  code: "unauthorized"
                  message: "Authentication required"
          '429':
            description: Too many requests
            content:
              application/json:
                schema:
                  $ref: '#/components/schemas/Error'
                example:
                  code: "rate_limit_exceeded"
                  message: "Rate limit exceeded. Try again in 1 minute."
            headers:
              X-RateLimit-Limit:
                schema:
                  type: integer
                description: Request limit per time window
              X-RateLimit-Remaining:
                schema:
                  type: integer
                description: Requests remaining in window
              X-RateLimit-Reset:
                schema:
                  type: integer
                description: Unix timestamp when limit resets


PHASE 3: REQUEST DOCUMENTATION

every possible input must be documented.


path parameters

  parameters:
    - name: user_id
      in: path
      description: |
        The unique identifier of the user.

        This can be either:
        - The numeric user ID
        - The string "me" for the authenticated user

        Example: `/users/123` or `/users/me`
      required: true
      schema:
        type: string
        pattern: '^[a-z0-9_-]+$'
      example: "123"

always document:
  [ ] what the parameter represents
  [ ] valid values or patterns
  [ ] if it's optional (should not be for path params)
  [ ] example values


query parameters

  parameters:
    - name: include
      in: query
      description: |
        Related resources to include in the response.

        Multiple values can be comma-separated.

        Available includes:
        - `profile` - user profile information
        - `settings` - user preferences
        - `stats` - user statistics

        Example: `?include=profile,stats`
      required: false
      schema:
        type: string
        example: "profile,stats"

    - name: fields
      in: query
      description: |
        Comma-separated list of fields to return.

        Use this to reduce response size by requesting only needed fields.

        Example: `?fields=id,name,email`
      required: false
      schema:
        type: string
        example: "id,name,email"

    - name: filter
      in: query
      description: |
        Filter results by field values.

        Syntax: `filter[field]=value`

        Multiple filters can be combined.

        Supported operators:
        - `filter[name]=Alice` - exact match
        - `filter[name][contains]=Ali` - contains
        - `filter[name][starts]=Al` - starts with
        - `filter[age][gte]=18` - greater than or equal
        - `filter[age][lte]=65` - less than or equal

        Example: `?filter[status]=active&filter[age][gte]=18`
      required: false
      schema:
        type: object
        additionalProperties: true
      style: deepObject
      explode: true


request headers

document all relevant headers:

  parameters:
    - name: Accept
      in: header
      description: |
        Response content format.

        Supported values:
        - `application/json` (default)
        - `application/vnd.api+json` (JSON:API format)
      required: false
      schema:
        type: string
        enum: [application/json, application/vnd.api+json]
        default: application/json

    - name: Accept-Language
      in: header
      description: |
        Preferred language for response messages.

        Supported: `en`, `es`, `fr`, `de`
      required: false
      schema:
        type: string
        example: "en"

    - name: Idempotency-Key
      in: header
      description: |
        A unique key to ensure idempotent requests.

        Use this for POST/PUT operations that should only execute once.
        The key should be a UUID or unique identifier.

        The key expires after 24 hours.
      required: false
      schema:
        type: string
        format: uuid
        example: "550e8400-e29b-41d4-a716-446655440000"


request body

  requestBodies:
    createUser:
      description: User object to create
      required: true
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/CreateUserRequest'
          examples:
            minimal:
              summary: Minimal user creation
              value:
                email: "user@example.com"
                password: "SecurePassword123!"
            full:
              summary: Complete user creation with all fields
              value:
                email: "alice@example.com"
                password: "SecurePassword123!"
                name: "Alice Johnson"
                phone: "+1-555-0123"
                timezone: "America/New_York"
                locale: "en"
            withProfile:
              summary: User with profile information
              value:
                email: "bob@example.com"
                password: "SecurePassword123!"
                name: "Bob Smith"
                profile:
                  bio: "Software developer"
                  location: "San Francisco, CA"
                  website: "https://bob.example.com"
        application/x-www-form-urlencoded:
          schema:
            type: object
            required:
              - email
              - password
            properties:
              email:
                type: string
                format: email
              password:
                type: string
                minLength: 8
              name:
                type: string
          encoding:
            profile:
              style: form
              explode: false

  components:
    schemas:
      CreateUserRequest:
        type: object
        required:
          - email
          - password
        properties:
          email:
            type: string
            format: email
            description: |
              User's email address. Must be unique across all users.

              A confirmation email will be sent to this address.
            example: "user@example.com"
          password:
            type: string
            minLength: 8
            maxLength: 128
            description: |
              User's password. Must be at least 8 characters.

              Requirements:
              - At least 8 characters
              - At least one uppercase letter
              - At least one number
              - At least one special character
            example: "SecurePass123!"
          name:
            type: string
            maxLength: 100
            description: User's display name
            example: "Alice Johnson"
          phone:
            type: string
            pattern: '^\+?[1-9]\d{1,14}$'
            description: |
              Phone number in E.164 format.

              Include country code prefixed with +.
            example: "+1-555-0123"
          timezone:
            type: string
            description: User's timezone for display purposes
            example: "America/New_York"
          locale:
            type: string
            description: Preferred language/locale
            example: "en"


PHASE 4: RESPONSE DOCUMENTATION

document all possible responses.


success response structure

  responses:
    '200':
      description: Successful operation
      content:
        application/json:
          schema:
            allOf:
              - $ref: '#/components/schemas/SuccessResponse'
              - type: object
                properties:
                  data:
                    $ref: '#/components/schemas/User'

  components:
    schemas:
      SuccessResponse:
        type: object
        required:
          - data
          - meta
        properties:
          data:
            description: The primary response data
          meta:
            type: object
            description: Metadata about the response
            properties:
              id:
                type: string
                description: Unique request ID for tracing
              timestamp:
                type: string
                format: date-time
                description: When the response was generated


pagination in responses

  components:
    schemas:
      PaginatedResponse:
        type: object
        required:
          - data
          - pagination
        properties:
          data:
            type: array
            description: Array of items
          pagination:
            type: object
            required:
              - page
              - limit
              - total
              - pages
            properties:
              page:
                type: integer
                minimum: 1
                description: Current page number
                example: 1
              limit:
                type: integer
                minimum: 1
                maximum: 100
                description: Items per page
                example: 20
              total:
                type: integer
                minimum: 0
                description: Total number of items
                example: 145
              pages:
                type: integer
                minimum: 1
                description: Total number of pages
                example: 8
              has_prev:
                type: boolean
                description: Whether there is a previous page
              has_next:
                type: boolean
                description: Whether there is a next page


resource schemas

  components:
    schemas:
      User:
        type: object
        required:
          - id
          - email
          - created_at
        properties:
          id:
            type: string
            description: Unique user identifier
            example: "usr_abc123xyz"
          email:
            type: string
            format: email
            description: User's email address
            example: "alice@example.com"
          name:
            type: string
            nullable: true
            description: User's display name
            example: "Alice Johnson"
          avatar_url:
            type: string
            format: uri
            nullable: true
            description: URL to user's avatar image
            example: "https://cdn.example.com/avatars/usr_abc123xyz.jpg"
          status:
            type: string
            enum: [active, inactive, suspended, deleted]
            description: Current user status
            example: "active"
          role:
            type: string
            enum: [user, admin, moderator]
            description: User's role in the system
            example: "user"
          created_at:
            type: string
            format: date-time
            description: When the user account was created
            example: "2024-01-15T10:30:00Z"
          updated_at:
            type: string
            format: date-time
            nullable: true
            description: When the user was last updated
            example: "2024-01-20T14:22:00Z"
          last_login_at:
            type: string
            format: date-time
            nullable: true
            description: Last time user logged in
            example: "2024-01-25T09:15:00Z"


error responses

create standard error response types:

  components:
    schemas:
      Error:
        type: object
        required:
          - error
        properties:
          error:
            type: object
            required:
              - code
              - message
            properties:
              code:
                type: string
                description: Machine-readable error code
                example: "validation_error"
              message:
                type: string
                description: Human-readable error message
                example: "The request failed validation"
              details:
                oneOf:
                  - type: object
                    additionalProperties: true
                    description: Additional error details
                  - type: array
                    items:
                      type: object
                      properties:
                        field:
                          type: string
                          description: Field name with error
                        message:
                          type: string
                          description: Error message for this field
                        code:
                          type: string
                          description: Error code for this field
              stacktrace:
                type: string
                description: Stack trace (development only)
                deprecated: true
              request_id:
                type: string
                description: Request ID for support
                example: "req_abc123xyz"

common error codes to document:

  400 Bad Request
    - validation_error: Invalid input data
    - invalid_json: Malformed JSON
    - missing_required_field: Required field missing
    - invalid_format: Field format invalid

  401 Unauthorized
    - unauthorized: No authentication provided
    - invalid_token: Token is invalid or expired
    - insufficient_permissions: Lacks required scope/role

  403 Forbidden
    - access_denied: Access to resource denied
    - account_suspended: Account is suspended
    - quota_exceeded: API quota exceeded

  404 Not Found
    - not_found: Resource does not exist

  409 Conflict
    - duplicate: Resource already exists
    - conflict: State conflict

  422 Unprocessable Entity
    - semantic_error: Request is valid but cannot be processed

  429 Too Many Requests
    - rate_limit_exceeded: Rate limit exceeded
    - temporarily_unavailable: Service temporarily unavailable

  500 Internal Server Error
    - internal_error: Unexpected server error

  503 Service Unavailable
    - service_unavailable: Service is down for maintenance


PHASE 5: AUTHENTICATION DOCUMENTATION

clearly explain how to authenticate.


authentication overview section

create a dedicated authentication guide:

  ## Authentication

  All API requests require authentication. We support two methods:

  ### Bearer Token (JWT)

  Most common method. Include your JWT token in the Authorization header.

  #### Getting a Token

  First, authenticate to receive a token:

  ```bash
  curl -X POST https://api.example.com/v1/auth/token \
    -H "Content-Type: application/json" \
    -d '{
      "client_id": "your_client_id",
      "client_secret": "your_client_secret",
      "grant_type": "client_credentials"
    }'
  ```

  Response:
  ```json
  {
    "access_token": "<access-token>",
    "token_type": "Bearer",
    "expires_in": 3600,
    "refresh_token": "<refresh-token>",
    "scope": "read write"
  }
  ```

  #### Using the Token

  Include the token in subsequent requests:

  ```bash
  curl https://api.example.com/v1/users \
    -H "Authorization: Bearer <access-token>"
  ```

  #### Token Expiration

  Access tokens expire after 1 hour. Use the refresh token to get a new access token:

  ```bash
  curl -X POST https://api.example.com/v1/auth/refresh \
    -H "Content-Type: application/json" \
    -d '{
      "refresh_token": "<refresh-token>"
    }'
  ```

  ### API Key

  For simple integrations, use an API key.

  #### Getting an API Key

  Generate an API key from your dashboard:

  1. Go to Settings > API Keys
  2. Click "Generate API Key"
  3. Copy the key (you won't see it again)

  #### Using the API Key

  Include the key in the X-API-Key header:

  ```bash
  curl https://api.example.com/v1/users \
    -H "X-API-Key: ${API_KEY}"
  ```

  Or as a query parameter (not recommended for production):

  ```bash
  curl "https://api.example.com/v1/users?api_key=${API_KEY}"
  ```


document scopes and permissions

  ### Scopes

  Access tokens can include scopes for granular permissions:

  | Scope | Description | Example Endpoints |
  |-------|-------------|-------------------|
  | `users:read` | Read user information | GET /users, GET /users/{id} |
  | `users:write` | Create and modify users | POST /users, PUT /users/{id} |
  | `users:delete` | Delete users | DELETE /users/{id} |
  | `posts:read` | Read posts | GET /posts |
  | `posts:write` | Create and modify posts | POST /posts |
  | `admin` | Full administrative access | All endpoints |

  #### Requesting Scopes

  Include scopes in your authentication request:

  ```bash
  curl -X POST https://api.example.com/v1/auth/token \
    -H "Content-Type: application/json" \
    -d '{
      "client_id": "your_client_id",
      "client_secret": "your_client_secret",
      "grant_type": "client_credentials",
      "scope": "users:read posts:write"
    }'
  ```


PHASE 6: INTERACTIVE DOCUMENTATION

make your API documentation interactive.


swagger UI setup

add Swagger UI to display interactive docs:

  <create>
  <file>docs/swagger.html</file>
  <content>
<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" type="text/css"
    href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  <style>
    body { margin: 0; padding: 0; }
    #swagger-ui { max-width: 1460px; margin: 0 auto; }
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.onload = function() {
      SwaggerUIBundle({
        url: "/openapi.yaml",
        dom_id: '#swagger-ui',
        deepLinking: true,
        presets: [
          SwaggerUIBundle.presets.apis,
          SwaggerUIBundle.SwaggerUIStandalonePreset
        ],
        plugins: [
          SwaggerUIBundle.plugins.DownloadUrl
        ],
        layout: "BaseLayout",
        defaultModelsExpandDepth: 1,
        defaultModelExpandDepth: 1,
        docExpansion: "list",
        filter: true,
        tryItOutEnabled: true,
        persistAuthorization: true,
        requestInterceptor: (request) => {
          // Add default headers if needed
          return request;
        },
        responseInterceptor: (response) => {
          // Log responses if needed
          return response;
        }
      });
    }
  </script>
</body>
</html>
  </content>
  </create>


redoc setup

for an alternative clean documentation style:

  <create>
  <file>docs/redoc.html</file>
  <content>
<!DOCTYPE html>
<html>
<head>
  <title>API Reference</title>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
  <style>
    body { margin: 0; padding: 0; }
  </style>
  <link rel="stylesheet" type="text/css"
    href="https://cdn.jsdelivr.net/npm/redoc@latest/bundles/redoc.min.css">
</head>
<body>
  <redoc spec-url="/openapi.yaml"></redoc>
  <script src="https://cdn.jsdelivr.net/npm/redoc@latest/bundles/redoc.standalone.js"></script>
</body>
</html>
  </content>
  </create>


PHASE 7: CODE EXAMPLES

provide working examples in multiple languages.


curl examples

  ```bash
  # List all users
  curl "https://api.example.com/v1/users?page=1&limit=20" \
    -H "Authorization: Bearer ${API_TOKEN}" \
    -H "Accept: application/json"

  # Create a new user
  curl -X POST "https://api.example.com/v1/users" \
    -H "Authorization: Bearer ${API_TOKEN}" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{
      "email": "newuser@example.com",
      "password": "SecurePassword123!",
      "name": "New User"
    }'

  # Update a user
  curl -X PUT "https://api.example.com/v1/users/usr_abc123" \
    -H "Authorization: Bearer ${API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "Updated Name"
    }'

  # Delete a user
  curl -X DELETE "https://api.example.com/v1/users/usr_abc123" \
    -H "Authorization: Bearer ${API_TOKEN}"
  ```


python examples

  ```python
  import requests
  import json

  # Configure
  base_url = "https://api.example.com/v1"
  token = "${API_TOKEN}"

  headers = {
      "Authorization": f"Bearer {token}",
      "Content-Type": "application/json",
      "Accept": "application/json"
  }

  # List users
  response = requests.get(
      f"{base_url}/users",
      headers=headers,
      params={"page": 1, "limit": 20}
  )
  response.raise_for_status()
  users = response.json()["data"]
  print(f"Found {len(users)} users")

  # Create user
  new_user = {
      "email": "newuser@example.com",
      "password": "SecurePassword123!",
      "name": "New User"
  }

  response = requests.post(
      f"{base_url}/users",
      headers=headers,
      json=new_user
  )
  response.raise_for_status()
  user = response.json()["data"]
  print(f"Created user: {user['id']}")

  # Upload file
  with open("avatar.jpg", "rb") as f:
      response = requests.put(
          f"{base_url}/users/{user['id']}/avatar",
          headers={"Authorization": f"Bearer {token}"},
          files={"file": f}
      )
  response.raise_for_status()
  ```


javascript examples

  ```javascript
  // Using fetch API
  const baseUrl = "https://api.example.com/v1";
  const token = "${API_TOKEN}";

  const headers = {
    "Authorization": `Bearer ${token}`,
    "Content-Type": "application/json",
    "Accept": "application/json"
  };

  // List users
  async function listUsers(page = 1) {
    const response = await fetch(
      `${baseUrl}/users?page=${page}&limit=20`,
      { headers }
    );

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data = await response.json();
    return data.data;
  }

  // Create user
  async function createUser(userData) {
    const response = await fetch(`${baseUrl}/users`, {
      method: "POST",
      headers: {
        ...headers,
        "Idempotency-Key": crypto.randomUUID()
      },
      body: JSON.stringify(userData)
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error.message);
    }

    const data = await response.json();
    return data.data;
  }

  // Usage
  try {
    const users = await listUsers();
    console.log(`Found ${users.length} users`);

    const newUser = await createUser({
      email: "newuser@example.com",
      password: "SecurePassword123!",
      name: "New User"
    });
    console.log(`Created: ${newUser.id}`);
  } catch (error) {
    console.error("Error:", error.message);
  }
  ```


node.js with axios

  ```javascript
  const axios = require('axios');

  // Configure client
  const api = axios.create({
    baseURL: 'https://api.example.com/v1',
    headers: {
      'Authorization': `Bearer ${process.env.API_TOKEN}`,
      'Content-Type': 'application/json'
    }
  });

  // List users
  async function listUsers() {
    try {
      const response = await api.get('/users', {
        params: { page: 1, limit: 20 }
      });
      return response.data.data;
    } catch (error) {
      if (error.response) {
        throw new Error(error.response.data.error.message);
      }
      throw error;
    }
  }

  // Create user with automatic retry
  async function createUser(userData, maxRetries = 3) {
    for (let attempt = 0; attempt < maxRetries; attempt++) {
      try {
        const response = await api.post('/users', userData, {
          headers: {
            'Idempotency-Key': require('crypto').randomUUID()
          }
        });
        return response.data.data;
      } catch (error) {
        if (error.response?.status === 429 && attempt < maxRetries - 1) {
          // Rate limited - wait and retry
          const retryAfter = error.response.headers['retry-after'];
          await new Promise(r => setTimeout(r, (retryAfter || 1) * 1000));
          continue;
        }
        throw error;
      }
    }
  }
  ```


go examples

  ```go
  package main

  import (
      "bytes"
      "encoding/json"
      "fmt"
      "io"
      "net/http"
  )

  const (
      BaseURL = "https://api.example.com/v1"
      Token   = "${API_TOKEN}"
  )

  type Client struct {
      HTTPClient *http.Client
      BaseURL    string
      Token      string
  }

  func NewClient(token string) *Client {
      return &Client{
          HTTPClient: &http.Client{},
          BaseURL:    BaseURL,
          Token:      token,
      }
  }

  func (c *Client) doRequest(method, path string, body io.Reader) (*http.Response, error) {
      req, err := http.NewRequest(method, c.BaseURL+path, body)
      if err != nil {
          return nil, err
      }

      req.Header.Set("Authorization", "Bearer "+c.Token)
      req.Header.Set("Content-Type", "application/json")
      req.Header.Set("Accept", "application/json")

      return c.HTTPClient.Do(req)
  }

  type User struct {
      ID    string `json:"id"`
      Email string `json:"email"`
      Name  string `json:"name"`
  }

  func (c *Client) ListUsers(page int) ([]User, error) {
      resp, err := c.doRequest("GET", fmt.Sprintf("/users?page=%d", page), nil)
      if err != nil {
          return nil, err
      }
      defer resp.Body.Close()

      var result struct {
          Data []User `json:"data"`
      }

      if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
          return nil, err
      }

      return result.Data, nil
  }

  func (c *Client) CreateUser(user User) (*User, error) {
      body, _ := json.Marshal(user)
      resp, err := c.doRequest("POST", "/users", bytes.NewReader(body))
      if err != nil {
          return nil, err
      }
      defer resp.Body.Close()

      var result struct {
          Data User `json:"data"`
      }

      if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
          return nil, err
      }

      return &result.Data, nil
  }
  ```


PHASE 8: GUIDES AND WALKTHROUGNS

provide narrative documentation beyond reference.


quick start guide

  ## Quick Start

  Get started with the API in 5 minutes.

  ### 1. Get Your Credentials

  Sign up at [https://example.com/signup](https://example.com/signup) and get your API key from the dashboard.

  ### 2. Make Your First Request

  ```bash
  curl "https://api.example.com/v1/users" \
    -H "X-API-Key: ${API_KEY}"
  ```

  Response:
  ```json
  {
    "data": [],
    "pagination": {
      "page": 1,
      "limit": 20,
      "total": 0,
      "pages": 0
    }
  }
  ```

  ### 3. Create Your First Resource

  ```bash
  curl -X POST "https://api.example.com/v1/users" \
    -H "X-API-Key: ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{
      "email": "user@example.com",
      "name": "Hello World"
    }'
  ```

  ### 4. Explore

  Browse the [full API reference](#reference) or check out more [guides](#guides).


common workflows

  ### Pagination

  List endpoints support pagination:

  ```python
  import requests

  def get_all_users():
      page = 1
      all_users = []

      while True:
          response = requests.get(
              "https://api.example.com/v1/users",
              headers={"Authorization": f"Bearer {token}"},
              params={"page": page, "limit": 100}
          ).json()

          all_users.extend(response["data"])

          if not response["pagination"]["has_next"]:
              break

          page += 1

      return all_users
  ```

  ### Filtering and Sorting

  ```bash
  # Filter active users, sorted by name
  curl "https://api.example.com/v1/users?filter[status]=active&sort=name_asc" \
    -H "Authorization: Bearer ${API_TOKEN}"

  # Complex filtering
  curl "https://api.example.com/v1/users?filter[age][gte]=18&filter[status]=active&sort=created_desc" \
    -H "Authorization: Bearer ${API_TOKEN}"
  ```

  ### Error Handling

  ```python
  import requests
  from requests.exceptions import HTTPError

  def api_call(method, endpoint, **kwargs):
      try:
          response = requests.request(
              method,
              f"https://api.example.com/v1{endpoint}",
              headers={"Authorization": f"Bearer {token}"},
              **kwargs
          )
          response.raise_for_status()
          return response.json()

      except HTTPError as e:
          error_data = e.response.json()
          print(f"Error: {error_data['error']['code']}")
          print(f"Message: {error_data['error']['message']}")

          # Handle specific error codes
          if error_data['error']['code'] == 'rate_limit_exceeded':
              # Implement backoff retry
              pass
          elif error_data['error']['code'] == 'unauthorized':
              # Refresh token
              pass

          raise
  ```

  ### Webhooks

  Set up webhooks to receive notifications:

  ```python
  from flask import Flask, request, jsonify

  app = Flask(__name__)

  @app.route('/webhook', methods=['POST'])
  def handle_webhook():
      # Verify signature
      signature = request.headers.get('X-Webhook-Signature')
      if not verify_signature(signature, request.data):
          return jsonify({"error": "invalid signature"}), 401

      # Process event
      event = request.json
      event_type = event['type']

      if event_type == 'user.created':
          handle_user_created(event['data'])
      elif event_type == 'user.deleted':
          handle_user_deleted(event['data'])

      return jsonify({"status": "received"}), 200
  ```


PHASE 9: VERSIONING DOCUMENTATION

clearly document API versioning strategy.


versioning overview

  ## API Versioning

  The API uses URL-based versioning. The current version is `v1`.

  ### Version Format

  ```
  https://api.example.com/v{version}/{resource}
  ```

  Example:
  ```
  https://api.example.com/v1/users
  https://api.example.com/v2/users
  ```

  ### Supported Versions

  | Version | Status | Release Date | Deprecation Date | Sunset Date |
  |---------|--------|--------------|------------------|-------------|
  | v2      | Current | 2024-06-01 | - | - |
  | v1      | Deprecated | 2023-01-01 | 2024-06-01 | 2025-01-01 |

  ### Deprecation Policy

  - API versions are supported for at least 12 months after deprecation
  - Deprecated versions return a `Deprecation` header
  - Sunset dates are announced at least 6 months in advance

  Deprecation header:
  ```
  Deprecation: true
  Sunset: Sat, 01 Jan 2025 00:00:00 GMT
  Link: <https://docs.example.com/api/v2>; rel="successor-version"
  ```


version changes documentation

  ### Migrating from v1 to v2

  Breaking changes in v2:

  [1] Response format change
      v1: `{ "users": [...] }`
      v2: `{ "data": [...] }`

      Migration: Update response parsing:
      ```python
      # v1
      users = response.json()["users"]

      # v2
      users = response.json()["data"]
      ```

  [2] Authentication header changed
      v1: `X-Auth-Token: <token>`
      v2: `Authorization: Bearer <token>`

      Migration: Update authentication headers

  [3] User ID format changed
      v1: Integer IDs (`123`)
      v2: String IDs (`usr_abc123xyz`)

      Migration: Update ID handling code

  [4] Pagination parameter renamed
      v1: `per_page`
      v2: `limit`

      Migration: Update parameter names

  Non-breaking additions in v2:

  - New filtering options
  - Webhook support
  - Batch operations
  - Rate limit headers


PHASE 10: TESTING AND EXAMPLES

provide testable examples and sandbox.


sandbox environment

  ## API Sandbox

  Test the API without affecting real data.

  ### Sandbox URL

  ```
  https://sandbox-api.example.com/v1
  ```

  The sandbox provides:
  - Full API functionality
  - Isolated test data
  - No rate limiting
  - Pre-configured test accounts

  ### Sandbox Credentials

  ```bash
  # Sandbox test account
  curl "https://sandbox-api.example.com/v1/auth/token" \
    -H "Content-Type: application/json" \
    -d '{
      "client_id": "test_client_id",
      "client_secret": "test_client_secret",
      "grant_type": "client_credentials"
    }'
  ```

  Response:
  ```json
  {
    "access_token": "test_token_abc123",
    "token_type": "Bearer",
    "expires_in": 3600
  }
  ```


example data sets

provide realistic example data:

  ### Example User Object

  ```json
  {
    "id": "usr_abc123xyz",
    "email": "alice.johnson@example.com",
    "name": "Alice Johnson",
    "avatar_url": "https://cdn.example.com/avatars/usr_abc123xyz.jpg",
    "status": "active",
    "role": "user",
    "timezone": "America/New_York",
    "locale": "en",
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-20T14:22:00Z",
    "last_login_at": "2024-01-25T09:15:00Z",
    "profile": {
      "bio": "Software developer and coffee enthusiast",
      "location": "San Francisco, CA",
      "website": "https://alice.example.com",
      "twitter": "@alicejohnson"
    },
    "stats": {
      "posts_count": 42,
      "followers_count": 156,
      "following_count": 89
    }
  }
  ```

  ### Example Error Response

  ```json
  {
    "error": {
      "code": "validation_error",
      "message": "Validation failed for one or more fields",
      "details": [
        {
          "field": "email",
          "message": "Email is already registered",
          "code": "duplicate_email"
        },
        {
          "field": "password",
          "message": "Password must be at least 8 characters",
          "code": "password_too_short"
        }
      ],
      "request_id": "req_xyz789abc",
      "timestamp": "2024-01-25T10:30:00Z"
    }
  }
  ```


PHASE 11: RATE LIMITING DOCUMENTATION

explain rate limits clearly.


rate limits overview

  ## Rate Limiting

  To ensure fair usage, the API enforces rate limits.

  ### Rate Limit Tiers

  | Plan | Requests per hour | Requests per day | Burst allowance |
  |------|-------------------|------------------|-----------------|
  | Free | 1,000 | 10,000 | 50 |
  | Basic | 10,000 | 100,000 | 200 |
  | Pro | 100,000 | 1,000,000 | 1,000 |
  | Enterprise | Unlimited | Unlimited | 10,000 |

  ### Rate Limit Headers

  Every response includes rate limit information:

  ```
  X-RateLimit-Limit: 1000
  X-RateLimit-Remaining: 945
  X-RateLimit-Reset: 1706169600
  X-RateLimit-Reset-After: 342
  ```

  Header descriptions:
  - `X-RateLimit-Limit`: Maximum requests per window
  - `X-RateLimit-Remaining`: Requests remaining in window
  - `X-RateLimit-Reset`: Unix timestamp when limit resets
  - `X-RateLimit-Reset-After`: Seconds until reset


handling rate limits

  ### Implementing Retry Logic

  ```python
  import time
  import requests

  def api_request_with_retry(url, max_retries=5):
      retries = 0

      while retries < max_retries:
          response = requests.get(url, headers=headers)

          # Check rate limit status
          remaining = int(response.headers.get('X-RateLimit-Remaining', 1))
          if remaining < 10:
              # Proactively slow down
              time.sleep(1)

          # Handle rate limit
          if response.status_code == 429:
              reset_after = int(response.headers.get('Retry-After', 60))
              print(f"Rate limited. Waiting {reset_after} seconds...")
              time.sleep(reset_after)
              retries += 1
              continue

          return response

      raise Exception("Max retries exceeded due to rate limiting")
  ```

  ### Exponential Backoff

  ```javascript
  async function fetchWithBackoff(url, maxRetries = 5) {
    for (let attempt = 0; attempt < maxRetries; attempt++) {
      const response = await fetch(url, { headers });

      if (response.status !== 429) {
        return response;
      }

      // Calculate backoff with jitter
      const baseDelay = Math.pow(2, attempt) * 1000; // 1s, 2s, 4s, 8s, 16s
      const jitter = Math.random() * 1000;
      const delay = baseDelay + jitter;

      console.log(`Rate limited. Retrying after ${delay}ms...`);
      await new Promise(r => setTimeout(r, delay));
    }

    throw new Error('Max retries exceeded');
  }
  ```


PHASE 12: WEBHOOKS DOCUMENTATION

document webhook system thoroughly.


webhook overview

  ## Webhooks

  Webhooks allow your application to receive real-time notifications when events occur.

  ### Setting Up Webhooks

  1. Provide a publicly accessible HTTPS endpoint
  2. Register the webhook URL via the API
  3. Verify your ownership of the URL
  4. Start receiving events

  ### Creating a Webhook

  ```bash
  curl -X POST "https://api.example.com/v1/webhooks" \
    -H "Authorization: Bearer ${API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{
      "url": "https://your-app.com/webhooks",
      "events": ["user.created", "user.updated", "user.deleted"],
      "secret": "your_webhook_secret"
    }'
  ```

  Response:
  ```json
  {
    "data": {
      "id": "whk_abc123",
      "url": "https://your-app.com/webhooks",
      "events": ["user.created", "user.updated", "user.deleted"],
      "status": "active",
      "created_at": "2024-01-25T10:30:00Z"
    }
  }
  ```


webhook events

  ### Available Events

  | Event | Description | Payload |
  |-------|-------------|---------|
  | `user.created` | New user registered | User object |
  | `user.updated` | User profile changed | User object + changes |
  | `user.deleted` | User account deleted | User ID |
  | `payment.succeeded` | Payment completed | Payment object |
  | `payment.failed` | Payment failed | Payment + error |
  | `subscription.started` | Subscription created | Subscription object |
  | `subscription.ended` | Subscription cancelled | Subscription object |

  ### Event Payload Structure

  ```json
  {
    "id": "evt_abc123xyz",
    "type": "user.created",
    "data": {
      "id": "usr_def456",
      "email": "newuser@example.com",
      "name": "New User",
      "created_at": "2024-01-25T10:30:00Z"
    },
    "timestamp": "2024-01-25T10:30:00Z",
    "delivered_attempts": 1
  }
  ```


webhook verification

  ### Verifying Webhook Signatures

  Each webhook includes a signature header:

  ```
  X-Webhook-Signature: t=1643104800,v1=abc123...
  ```

  Verification logic:

  ```python
  import hmac
  import hashlib
  from flask import Flask, request, jsonify

  app = Flask(__name__)
  WEBHOOK_SECRET = "your_webhook_secret"

  def verify_signature(payload, signature_header):
      # Split signature
      parts = signature_header.split(',')
      signature_dict = {}

      for part in parts:
          key, value = part.split('=')
          signature_dict[key] = value

      # Create expected signature
      expected_payload = f"{signature_dict['t']}.{payload}"
      expected_signature = hmac.new(
          WEBHOOK_SECRET.encode(),
          expected_payload.encode(),
          hashlib.sha256
      ).hexdigest()

      # Compare signatures
      received_signature = signature_dict.get('v1', '')
      return hmac.compare_digest(expected_signature, received_signature)

  @app.route('/webhooks', methods=['POST'])
  def handle_webhook():
      signature = request.headers.get('X-Webhook-Signature')
      payload = request.get_data(as_text=True)

      if not verify_signature(payload, signature):
          return jsonify({"error": "Invalid signature"}), 401

      # Process webhook
      event = request.json
      # ... handle event ...

      return jsonify({"status": "ok"}), 200
  ```


webhook best practices

  [ ] Always verify signatures
  [ ] Return 200 OK quickly, process asynchronously
  [ ] Handle duplicate events (idempotency)
  [ ] Implement retry logic for failures
  [ ] Log all webhook events
  [ ] Use HTTPS only
  [ ] Don't expose sensitive data in webhook URLs

  example webhook handler:

  ```python
  import hashlib
  from typing import Dict
  from datetime import datetime

  class WebhookHandler:
      def __init__(self, secret: str):
          self.secret = secret
          self.processed_events = set()  # In production, use Redis or database

      def verify(self, payload: str, signature: str) -> bool:
          """Verify webhook signature."""
          expected = hmac.new(
              self.secret.encode(),
              payload.encode(),
              hashlib.sha256
          ).hexdigest()
          return hmac.compare_digest(expected, signature)

      def is_duplicate(self, event_id: str) -> bool:
          """Check if event was already processed."""
          return event_id in self.processed_events

      def mark_processed(self, event_id: str):
          """Mark event as processed."""
          self.processed_events.add(event_id)

      def handle(self, event: Dict) -> None:
          """Handle webhook event."""
          event_id = event['id']

          if self.is_duplicate(event_id):
              print(f"Duplicate event {event_id}, skipping")
              return

          # Route to appropriate handler
          handlers = {
              'user.created': self.handle_user_created,
              'user.updated': self.handle_user_updated,
              'user.deleted': self.handle_user_deleted,
          }

          handler = handlers.get(event['type'])
          if handler:
              handler(event['data'])
              self.mark_processed(event_id)

      def handle_user_created(self, data):
          """Handle user created event."""
          print(f"New user: {data['email']}")

      # ... other handlers ...
  ```


PHASE 13: CHANGELOG DOCUMENTATION

maintain a history of API changes.


changelog format

  ## Changelog

  All notable changes to the API are documented in this file.

  The format is based on [Keep a Changelog](https://keepachangelog.com/).

  ### [Unreleased]

  ### [2.1.0] - 2024-02-15

  #### Added
  - New `include` parameter for related resources
  - Webhook support for user events
  - Filter by multiple statuses

  #### Changed
  - Increased rate limits for Pro plan
  - Improved error messages for validation failures

  #### Fixed
  - Fixed pagination issue with filtered results
  - Fixed timezone handling in date fields

  #### Deprecated
  - `per_page` parameter (use `limit` instead)

  #### Removed
  - XML response format
  - v0 endpoints (grace period ended)

  #### Security
  - Added signature verification for webhooks

  ### [2.0.0] - 2024-01-01

  #### Breaking Changes
  - Response format changed to `{ data, meta }` structure
  - Authentication now uses `Authorization: Bearer` header
  - User IDs are now strings instead of integers

  See [migration guide](/docs/migration-v2) for details.

  #### Added
  - Batch operations endpoint
  - Server-sent events for real-time updates
  - Rate limit headers on all responses

  ### [1.5.0] - 2023-11-15

  #### Added
  - Search endpoint
  - Webhook management endpoints

  #### Changed
  - Default page size increased from 10 to 20

  #### Deprecated
  - Legacy authentication method


migration guides

for major versions, provide detailed migration guides:

  ### Migrating to v2.0

  This guide helps you migrate from v1 to v2.

  #### Overview of Changes

  [1] Authentication
     v1: `X-Auth-Token` header
     v2: `Authorization: Bearer` header

  [2] Response Structure
     v1: `{ resource_name: [...] }`
     v2: `{ data: [...], meta: {...} }`

  [3] ID Format
     v1: Integer IDs
     v2: String IDs with prefixes

  [4] Pagination
     v1: `per_page` parameter
     v2: `limit` parameter

  #### Code Changes

  Before (v1):
  ```python
  response = requests.get('https://api.example.com/v1/users', headers={
      'X-Auth-Token': token
  })
  users = response.json()['users']
  ```

  After (v2):
  ```python
  response = requests.get('https://api.example.com/v2/users', headers={
      'Authorization': f'Bearer {token}'
  })
  users = response.json()['data']
  ```

  #### Testing Your Migration

  Use the v2 sandbox environment:
  ```
  https://sandbox-api.example.com/v2
  ```

  Follow the [testing checklist](#testing-checklist).


PHASE 14: API DOCUMENTATION CHECKLIST


content completeness

  [ ] overview section
      [ ] what the API does
      [ ] who it's for
      [ ] key features

  [ ] authentication section
      [ ] all auth methods documented
      [ ] how to get credentials
      [ ] token refresh process
      [ ] scopes and permissions

  [ ] all endpoints documented
      [ ] HTTP method and path
      [ ] description
      [ ] all parameters documented
      [ ] all headers documented
      [ ] request body schema
      [ ] all response codes
      [ ] response schemas
      [ ] examples for each

  [ ] common patterns documented
      [ ] pagination
      [ ] filtering
      [ ] sorting
      [ ] error handling
      [ ] rate limiting

  [ ] code examples
      [ ] curl
      [ ] javascript
      [ ] python
      [ ] at least one backend language

  [ ] guides
      [ ] quick start
      [ ] common workflows
      [ ] integration examples
      [ ] troubleshooting


quality checks

  [ ] all examples are tested
      [ ] curl commands run successfully
      [ ] code examples execute
      [ ] example responses are accurate

  [ ] OpenAPI spec is valid
      <terminal>npm install -g @apidevtools/swagger-cli</terminal>
      <terminal>swagger-cli validate openapi.yaml</terminal>

  [ ] links work
      [ ] internal links
      [ ] external links
      [ ] code links

  [ ] consistent terminology
      [ ] always use same terms for concepts
      [ ] avoid synonyms
      [ ] define jargon

  [ ] clear and concise
      [ ] no unnecessary verbosity
      [ ] no ambiguity
      [ ] active voice

  [ ] accessible
      [ ] readable without styling
      [ ] alt text for images
      [ ] color contrast


PHASE 15: API DOCUMENTATION RULES (STRICT MODE)


while this skill is active, these rules are MANDATORY:

  [1] DOCUMENT EVERY ENDPOINT
      no endpoint should be undocumented
      if an endpoint exists publicly, it must be documented

  [2] NEVER SKIP ERROR RESPONSES
      document all possible error codes for each endpoint
      include example error responses

  [3] ALWAYS PROVIDE EXAMPLES
      at minimum: curl example
      better: curl + one language example
      best: curl + 2+ language examples

  [4] VALIDATE OPENAPI SPECS
      before committing, validate:
      <terminal>swagger-cli validate openapi.yaml</terminal>

  [5] TEST YOUR EXAMPLES
      every code example must be tested
      if you can't run it, don't include it

  [6] DOCUMENT PARAMETERS FULLY
      name, type, required/optional, description, example, constraints
      no parameter should be partially documented

  [7] INCLUDE AUTHENTICATION
      every endpoint must show auth requirements
      if no auth needed, explicitly state it

  [8] MAINTAIN CHANGELOG
      every API change goes in the changelog
      no silent changes

  [9] PROVIDE MIGRATION GUIDES
      for any breaking change, provide migration guide
      deprecation notice at least 6 months before removal

  [10] THINK FROM USER PERSPECTIVE
       would a new developer understand this?
       would a non-technical stakeholder understand?
       if not, rewrite


PHASE 16: DOCUMENTATION MAINTENANCE


keeping docs current

  [ ] review schedule
      weekly: check for new endpoints
      monthly: full documentation review
      quarterly: user feedback review

  [ ] automated checks
      - openapi spec validation in ci
      - example testing in ci
      - link checking

  [ ] feedback loop
      - add feedback widget to docs
      - track documentation issues
      - survey users annually

  [ ] version control
      - tag docs with API version
      - keep old versions accessible
      - maintain docs in same repo as code

  example CI check:

  ```yaml
  # .github/workflows/docs-check.yml
  name: Documentation Checks

  on: [pull_request]

  jobs:
    validate-openapi:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3
        - name: Validate OpenAPI spec
          run: |
            npm install -g @apidevtools/swagger-cli
            swagger-cli validate openapi.yaml

    test-examples:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3
        - name: Test curl examples
          run: |
            # Extract and test curl commands from docs
            ./scripts/test-examples.sh
  ```


FINAL REMINDERS


documentation is a product interface

the API docs are often the first thing developers see.
poor documentation = poor developer experience.
excellent documentation = happy developers, fewer support tickets.


docs are never done

 APIs evolve.
documentation must evolve with them.
make docs part of your development process.


the rule of clarity

if you have to read it twice, rewrite it.
if you have to explain it, it's not clear.
simple, direct language wins.


when in doubt

add an example.
examples bridge the gap between abstract and concrete.
a good example is worth a thousand descriptions.

now go document every endpoint.
