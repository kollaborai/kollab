"""Tests for web tools (web-fetch and web-search).

Tests cover:
1. Tool definition registration and metadata
2. web-fetch execution: basic fetch, truncation, invalid URLs, timeouts
3. web-search execution: basic search, result count limit

Mocks aiohttp to avoid real network calls.
"""

import asyncio
import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

# Ensure we can import kollabor_agent
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kollabor_agent.tool_registry import ToolRegistry


def _run(coro):
    """Run a coroutine with a fresh event loop.

    Using asyncio.run() instead of get_event_loop().run_until_complete()
    avoids conflicts with pytest-asyncio's event loop policy.
    """
    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestWebToolDefinitions(unittest.TestCase):
    """Verify web tool definitions are registered correctly."""

    def setUp(self):
        """Reset registry and load definitions."""
        ToolRegistry.reset()
        self.registry = ToolRegistry.get_global()

    def test_web_fetch_registered(self):
        """web-fetch tool should be in the registry."""
        tool = self.registry.get("web-fetch")
        self.assertIsNotNone(tool, "web-fetch not registered")
        self.assertEqual(tool.category, "web")
        self.assertEqual(tool.risk_level, "medium")

    def test_web_search_registered(self):
        """web-search tool should be in the registry."""
        tool = self.registry.get("web-search")
        self.assertIsNotNone(tool, "web-search not registered")
        self.assertEqual(tool.category, "web")
        self.assertEqual(tool.risk_level, "medium")

    def test_web_fetch_parameters(self):
        """web-fetch should have url (required) and max_chars (optional)."""
        tool = self.registry.get("web-fetch")
        self.assertIsNotNone(tool)

        param_names = {p.name for p in tool.parameters}
        self.assertIn("url", param_names)
        self.assertIn("max_chars", param_names)

        url_param = next(p for p in tool.parameters if p.name == "url")
        self.assertTrue(url_param.required)
        self.assertEqual(url_param.type, "string")

        max_chars_param = next(p for p in tool.parameters if p.name == "max_chars")
        self.assertFalse(max_chars_param.required)
        self.assertEqual(max_chars_param.type, "integer")
        self.assertEqual(max_chars_param.default, 5000)

    def test_web_search_parameters(self):
        """web-search should have query (required) and max_results (optional)."""
        tool = self.registry.get("web-search")
        self.assertIsNotNone(tool)

        param_names = {p.name for p in tool.parameters}
        self.assertIn("query", param_names)
        self.assertIn("max_results", param_names)

        query_param = next(p for p in tool.parameters if p.name == "query")
        self.assertTrue(query_param.required)
        self.assertEqual(query_param.type, "string")

        max_results_param = next(
            p for p in tool.parameters if p.name == "max_results"
        )
        self.assertFalse(max_results_param.required)
        self.assertEqual(max_results_param.type, "integer")
        self.assertEqual(max_results_param.default, 5)

    def test_web_fetch_xml_tag(self):
        """web-fetch should use nested XML form with web-fetch tag."""
        tool = self.registry.get("web-fetch")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.xml_tag, "web-fetch")
        self.assertEqual(tool.xml_form, "nested")

    def test_web_search_xml_tag(self):
        """web-search should use nested XML form with web-search tag."""
        tool = self.registry.get("web-search")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.xml_tag, "web-search")
        self.assertEqual(tool.xml_form, "nested")

    def test_web_fetch_native_name(self):
        """web-fetch native name should be web_fetch (underscore)."""
        tool = self.registry.get("web-fetch")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.native_name, "web_fetch")

    def test_web_search_native_name(self):
        """web-search native name should be web_search (underscore)."""
        tool = self.registry.get("web-search")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.native_name, "web_search")

    def test_web_fetch_has_examples(self):
        """web-fetch should have at least one usage example."""
        tool = self.registry.get("web-fetch")
        self.assertIsNotNone(tool)
        self.assertGreater(len(tool.examples), 0)

    def test_web_search_has_examples(self):
        """web-search should have at least one usage example."""
        tool = self.registry.get("web-search")
        self.assertIsNotNone(tool)
        self.assertGreater(len(tool.examples), 0)


def _make_mock_aiohttp_response(status=200, text="", url="https://example.com"):
    """Create a mock for an aiohttp response context manager.

    Can be used as the return value of session.get() or session.post()
    in an `async with` block.
    """
    resp = AsyncMock()
    resp.status = status
    resp.reason = "OK" if status < 400 else "Error"
    resp.text = AsyncMock(return_value=text)
    resp.headers = {}
    resp.url = url
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)
    return resp


@contextmanager
def _patch_aiohttp_session(mock_get=None, mock_post=None):
    """Patch aiohttp.ClientSession to return a mock session.

    The mock session's __aenter__ returns itself, and .get()/.post()
    return the provided mock response context managers.
    """
    mock_session = AsyncMock()
    if mock_get is not None:
        mock_session.get = MagicMock(return_value=mock_get)
    if mock_post is not None:
        mock_session.post = MagicMock(return_value=mock_post)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    with patch("aiohttp.ClientSession", return_value=mock_session):
        yield


class TestWebFetchExecution(unittest.TestCase):
    """Tests for web-fetch tool execution via ToolExecutor."""

    def setUp(self):
        """Set up mock executor."""
        from kollabor_agent.tool_executor import ToolExecutor

        self.mcp_integration = MagicMock()
        self.event_bus = MagicMock()
        self.executor = ToolExecutor(
            mcp_integration=self.mcp_integration,
            event_bus=self.event_bus,
            terminal_timeout=10,
            mcp_timeout=20,
        )

    def test_web_fetch_basic(self):
        """Test basic URL fetch with HTML-to-text conversion."""
        tool_data = {
            "type": "web_fetch",
            "id": "web_fetch_0",
            "url": "https://example.com",
            "raw": '<web-fetch><url>https://example.com</url></web-fetch>',
        }

        mock_resp = _make_mock_aiohttp_response(
            text="<html><body><p>Hello World</p></body></html>"
        )

        with _patch_aiohttp_session(mock_get=mock_resp):
            result = _run(self.executor._execute_web_fetch(tool_data))

            self.assertTrue(result.success, f"Expected success, got error: {result.error}")
            self.assertIn("Hello World", result.output)
            # HTML tags should be stripped
            self.assertNotIn("<html>", result.output)
            self.assertNotIn("<p>", result.output)

    def test_web_fetch_truncation(self):
        """Test that max_chars limit truncates output."""
        long_text = "A" * 50000
        html = f"<html><body><p>{long_text}</p></body></html>"

        tool_data = {
            "type": "web_fetch",
            "id": "web_fetch_0",
            "url": "https://example.com",
            "max_chars": 100,
            "raw": '<web-fetch><url>https://example.com</url><max_chars>100</max_chars></web-fetch>',
        }

        mock_resp = _make_mock_aiohttp_response(text=html)

        with _patch_aiohttp_session(mock_get=mock_resp):
            result = _run(self.executor._execute_web_fetch(tool_data))

            self.assertTrue(result.success)
            # Output includes a URL header line + truncated text + truncation marker
            self.assertLessEqual(
                len(result.output), 200,
                f"Output should be roughly truncated, got {len(result.output)} chars"
            )

    def test_web_fetch_invalid_url(self):
        """Test error handling for invalid URLs."""
        tool_data = {
            "type": "web_fetch",
            "id": "web_fetch_0",
            "url": "not-a-valid-url",
            "raw": '<web-fetch><url>not-a-valid-url</url></web-fetch>',
        }

        # URL validation rejects before any network call
        result = _run(self.executor._execute_web_fetch(tool_data))

        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_web_fetch_timeout(self):
        """Test timeout handling."""
        tool_data = {
            "type": "web_fetch",
            "id": "web_fetch_0",
            "url": "https://slow-server.example.com",
            "raw": '<web-fetch><url>https://slow-server.example.com</url></web-fetch>',
        }

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=asyncio.TimeoutError())
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = _run(self.executor._execute_web_fetch(tool_data))

            self.assertFalse(result.success)
            self.assertIsNotNone(result.error)
            self.assertIn("timeout", result.error.lower())

    def test_web_fetch_missing_url(self):
        """Test error when url parameter is missing."""
        tool_data = {
            "type": "web_fetch",
            "id": "web_fetch_0",
            "raw": '<web-fetch></web-fetch>',
        }

        result = _run(self.executor._execute_web_fetch(tool_data))

        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_web_fetch_http_error(self):
        """Test handling of HTTP error status codes (404, 500)."""
        tool_data = {
            "type": "web_fetch",
            "id": "web_fetch_0",
            "url": "https://example.com/nonexistent",
            "raw": '<web-fetch><url>https://example.com/nonexistent</url></web-fetch>',
        }

        mock_resp = _make_mock_aiohttp_response(status=404, text="Not Found")

        with _patch_aiohttp_session(mock_get=mock_resp):
            result = _run(self.executor._execute_web_fetch(tool_data))

            self.assertFalse(result.success)
            self.assertIsNotNone(result.error)

    def test_web_fetch_html_to_text(self):
        """Test that HTML stripping removes script/style/nav tags."""
        html = (
            "<html><head><script>evil()</script>"
            "<style>body{color:red}</style></head>"
            "<nav>Home About</nav>"
            "<body><p>Main content here</p>"
            "<footer>Copyright</footer></body></html>"
        )

        text = self.executor._html_to_text(html)

        self.assertIn("Main content here", text)
        self.assertNotIn("evil()", text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("Copyright", text)


class TestWebSearchExecution(unittest.TestCase):
    """Tests for web-search tool execution via ToolExecutor."""

    def setUp(self):
        """Set up mock executor."""
        from kollabor_agent.tool_executor import ToolExecutor

        self.mcp_integration = MagicMock()
        self.event_bus = MagicMock()
        self.executor = ToolExecutor(
            mcp_integration=self.mcp_integration,
            event_bus=self.event_bus,
            terminal_timeout=10,
            mcp_timeout=20,
        )

    def _make_search_html(self, results):
        """Build mock DuckDuckGo HTML with given results.

        Args:
            results: list of (title, url, snippet) tuples
        """
        items = []
        for title, url, snippet in results:
            items.append(
                f'<div class="result">'
                f'<a class="result__a" href="//duckduckgo.com/l/?uddg={url}">{title}</a>'
                f'<a class="result__snippet">{snippet}</a>'
                f'</div>'
            )
        return f'<html><body>{"".join(items)}</body></html>'

    def test_web_search_basic(self):
        """Test basic search returns parsed results."""
        search_html = self._make_search_html([
            ("Python docs", "https%3A%2F%2Fdocs.python.org", "The official Python documentation"),
            ("Python tutorial", "https%3A%2F%2Ftutorial.python.org", "Learn Python step by step"),
        ])

        tool_data = {
            "type": "web_search",
            "id": "web_search_0",
            "query": "python documentation",
            "raw": '<web-search><query>python documentation</query></web-search>',
        }

        mock_resp = _make_mock_aiohttp_response(text=search_html)

        with _patch_aiohttp_session(mock_post=mock_resp):
            result = _run(self.executor._execute_web_search(tool_data))

            self.assertTrue(result.success, f"Expected success, got error: {result.error}")
            self.assertTrue(
                "Python docs" in result.output or
                "docs.python.org" in result.output or
                "python" in result.output.lower()
            )

    def test_web_search_max_results(self):
        """Test that max_results limits the number of returned results."""
        results_data = [
            (f"Result {i}", f"https%3A%2F%2Fexample.com%2F{i}", f"Snippet {i}")
            for i in range(10)
        ]
        search_html = self._make_search_html(results_data)

        tool_data = {
            "type": "web_search",
            "id": "web_search_0",
            "query": "test query",
            "max_results": 3,
            "raw": '<web-search><query>test query</query><max_results>3</max_results></web-search>',
        }

        mock_resp = _make_mock_aiohttp_response(text=search_html)

        with _patch_aiohttp_session(mock_post=mock_resp):
            result = _run(self.executor._execute_web_search(tool_data))

            self.assertTrue(result.success, f"Expected success, got error: {result.error}")
            result_count = result.output.count("https://example.com/")
            self.assertLessEqual(
                result_count, 3,
                f"Should return at most 3 results, got {result_count}"
            )

    def test_web_search_missing_query(self):
        """Test error when query parameter is missing."""
        tool_data = {
            "type": "web_search",
            "id": "web_search_0",
            "raw": '<web-search></web-search>',
        }

        result = _run(self.executor._execute_web_search(tool_data))

        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_web_search_network_error(self):
        """Test error handling when search endpoint is unreachable."""
        tool_data = {
            "type": "web_search",
            "id": "web_search_0",
            "query": "test query",
            "raw": '<web-search><query>test query</query></web-search>',
        }

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=aiohttp.ClientError("Connection refused"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = _run(self.executor._execute_web_search(tool_data))

            self.assertFalse(result.success)
            self.assertIsNotNone(result.error)


if __name__ == "__main__":
    unittest.main()
