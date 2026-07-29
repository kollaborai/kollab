web tools (internet access):

web-fetch — retrieve a URL and return clean text:
  <web-fetch><url>https://example.com</url></web-fetch>
  <web-fetch><url>https://docs.python.org/3/</url><max_chars>5000</max_chars></web-fetch>

  parameters:
    url        (required)  the URL to fetch (http or https)
    max_chars  (optional)  max characters of text to return (default: 10000)

  behavior:
    - strips HTML tags, script/style/nav/footer removed
    - decodes HTML entities
    - main-content extraction (skips boilerplate)
    - truncates to max_chars with a marker if exceeded
    - 30-second timeout
    - follows redirects

  error modes:
    - invalid URL (missing scheme or host)
    - HTTP error (404, 500, etc.)
    - network error (connection refused, DNS failure)
    - timeout after 30 seconds

web-search — search the web via DuckDuckGo (no API key needed):
  <web-search><query>python async best practices</query></web-search>
  <web-search><query>rust vs go performance</query><max_results>3</max_results></web-search>

  parameters:
    query        (required)  search query string
    max_results  (optional)  max results to return (default: 5)

  behavior:
    - uses DuckDuckGo HTML endpoint (free, no key)
    - returns title, URL, and snippet for each result
    - 30-second timeout

  error modes:
    - no query provided
    - search endpoint unreachable
    - timeout after 30 seconds

typical workflow:
  1. search for info:  <web-search><query>how to do X</query></web-search>
  2. fetch a result:   <web-fetch><url>https://found-page.com</url></web-fetch>
  3. use the content in your response or analysis

notes:
  - prefer web-search when you don't know the exact URL
  - use web-fetch when you have a specific page to read
  - max_chars keeps output manageable; increase for long docs
  - results are text-only (no images, no interactive content)
