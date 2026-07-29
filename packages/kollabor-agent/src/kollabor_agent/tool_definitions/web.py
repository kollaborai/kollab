"""Web tool definitions.

Tools for fetching URLs and searching the web. Content extraction
uses readability heuristics to return clean text without navigation,
ads, or page chrome — think "print mode" for agents.
"""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry

web_fetch = ToolDefinition(
    name="web-fetch",
    description=(
        "Fetch a URL and return the content as clean text. "
        "Extracts main content using readability heuristics — "
        "returns clean text without navigation, ads, or page chrome."
    ),
    category="web",
    risk_level="medium",
    requires_permission=True,
    xml_tag="web-fetch",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="url",
            type="string",
            description="The URL to fetch (must include http:// or https://)",
            required=True,
        ),
        ToolParameter(
            name="max_chars",
            type="integer",
            description=(
                "Maximum characters of text to return (default: 5000). "
                "Content is truncated to this limit after extraction."
            ),
            required=False,
            default=5000,
        ),
        ToolParameter(
            name="extract_main",
            type="boolean",
            description=(
                "When true (default), extract only the main content block "
                "using readability heuristics — skips nav, footer, scripts, "
                "ads. When false, return all page text after tag stripping."
            ),
            required=False,
            default=True,
        ),
    ],
    examples=[
        "<web-fetch><url>https://example.com</url></web-fetch>",
        '<web-fetch><url>https://docs.python.org/3/library/asyncio.html</url>'
        '<max_chars>5000</max_chars></web-fetch>',
        '<web-fetch><url>https://news.ycombinator.com</url>'
        '<extract_main>false</extract_main></web-fetch>',
    ],
    result_format=(
        "On success: clean text content from the page, truncated to "
        "max_chars. On failure: error message describing what went wrong."
    ),
    error_modes=[
        "Network error: cannot reach the URL",
        "Invalid URL: malformed or missing scheme",
        "Timeout: server did not respond within 30 seconds",
        "HTTP error: server returned 4xx/5xx status code",
    ],
    notes=(
        "Uses a priority chain for content extraction: JSON-LD structured "
        "data first, then meta tags, then semantic HTML5 (<article>/<main>/"
        "<section>), then readability fallback (highest text-to-tag ratio "
        "element), then tag-stripped full page as last resort. This keeps "
        "results compact and relevant — a 100KB page typically yields 2-5KB "
        "of actual content."
    ),
    safety_features=[
        "30-second timeout prevents hanging on slow servers",
        "max_chars cap prevents context window overflow",
        "content extraction strips scripts, styles, and tracking pixels",
    ],
    key_rules=[
        "always include the full URL with http:// or https://",
        "use max_chars to limit output when you only need a snippet",
        "set extract_main=false for pages where you need all text (e.g. search results)",
        "results are text-only — no images, CSS, or JavaScript output",
    ],
)

web_search = ToolDefinition(
    name="web-search",
    description=(
        "Search the web and return results. Returns titles, URLs, and "
        "snippets for the top matches."
    ),
    category="web",
    risk_level="medium",
    requires_permission=True,
    xml_tag="web-search",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Search query string",
            required=True,
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description=(
                "Maximum number of results to return (default: 5). "
                "Each result includes title, URL, and snippet."
            ),
            required=False,
            default=5,
        ),
    ],
    examples=[
        "<web-search><query>python asyncio tutorial</query></web-search>",
        '<web-search><query>rust vs go performance benchmarks</query>'
        '<max_results>3</max_results></web-search>',
    ],
    result_format=(
        "Formatted list of search results, each with title, URL, and "
        "a short snippet. Typically ~200 chars per result."
    ),
    error_modes=[
        "Network error: cannot reach search endpoint",
        "Timeout: search did not complete within 30 seconds",
        "No results: query returned zero matches",
    ],
    notes=(
        "Uses DuckDuckGo's HTML endpoint — no API key required. Results "
        "are compact by design: 5 results is typically under 1KB of text. "
        "Use web-fetch to get the full content of any result that looks "
        "relevant."
    ),
    safety_features=[
        "30-second timeout on search requests",
        "max_results cap prevents excessive output",
        "results are metadata only — no page content fetched",
    ],
    key_rules=[
        "keep queries concise for better results",
        "use max_results to limit output when you only need top hits",
        "follow up with web-fetch on any result URL for full content",
    ],
)


def register_all():
    """Register all web tool definitions."""
    registry = get_registry()
    registry.register(web_fetch)
    registry.register(web_search)


# Auto-register on import
register_all()
