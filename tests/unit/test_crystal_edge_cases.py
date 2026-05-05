"""Edge case tests for crystal memory XML tags.

AREA 1 - Tag regex extraction (patterns from plugin.py)
AREA 2 - CrystalStore handler edge cases
AREA 3 - Context limit truncation
"""

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

from plugins.hub.crystal_store import CrystalStore, normalize_crystal_id

# ---------------------------------------------------------------------------
# Regex patterns - must match plugin.py exactly
# ---------------------------------------------------------------------------
CRYSTAL_SEARCH_RE = re.compile(
    r'<crystal_search\s+query="([^"]*)"(?:\s+limit="(\d+)")?\s*/>'
)
CRYSTAL_READ_RE = re.compile(r'<crystal_read\s+id="([^"]+)"\s*/>')
CRYSTAL_LIST_RE = re.compile(
    r'<crystal_list(?:\s+limit="(\d+)")?(?:\s+offset="(\d+)")?\s*/>'
)
CRYSTAL_EDIT_RE = re.compile(
    r'<crystal_edit\s+id="([^"]+)"'
    r'(?:\s+summary="([^"]*)")?'
    r'(?:\s+keywords="([^"]*)")?'
    r"\s*>(.*?)</crystal_edit>",
    re.DOTALL | re.IGNORECASE,
)
CRYSTAL_DELETE_RE = re.compile(
    r'<crystal_delete\s+id="([^"]+)"(?:\s+reason="([^"]*)")?\s*/>'
)


# ---------------------------------------------------------------------------
# Extraction helpers - mirror plugin.py closures exactly
# ---------------------------------------------------------------------------
def extract_crystal_search(tag: str) -> Optional[Dict[str, Any]]:
    m = CRYSTAL_SEARCH_RE.search(tag)
    if not m:
        return None
    return {
        "query": m.group(1),
        "limit": int(m.group(2)) if m.group(2) else 5,
    }


def extract_crystal_read(tag: str) -> Optional[Dict[str, Any]]:
    m = CRYSTAL_READ_RE.search(tag)
    if not m:
        return None
    return {"entry_id": m.group(1)}


def extract_crystal_list(tag: str) -> Optional[Dict[str, Any]]:
    m = CRYSTAL_LIST_RE.search(tag)
    if not m:
        return None
    return {
        "limit": int(m.group(1)) if m.group(1) else 20,
        "offset": int(m.group(2)) if m.group(2) else 0,
    }


def extract_crystal_edit(tag: str) -> Optional[Dict[str, Any]]:
    m = CRYSTAL_EDIT_RE.search(tag)
    if not m:
        return None
    keywords_raw = m.group(3)
    if keywords_raw is None:
        keywords = None
    elif keywords_raw == "":
        keywords = []
    else:
        keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
    return {
        "entry_id": m.group(1),
        "content": m.group(4).strip(),
        "summary": m.group(2),
        "keywords": keywords,
    }


def extract_crystal_delete(tag: str) -> Optional[Dict[str, Any]]:
    m = CRYSTAL_DELETE_RE.search(tag)
    if not m:
        return None
    return {
        "entry_id": m.group(1),
        "reason": m.group(2) or "",
    }


# =====================================================================
# AREA 1 - Tag regex extraction
# =====================================================================
class TestCrystalSearchRegex(unittest.TestCase):
    """Test crystal_search tag extraction."""

    def test_empty_query_default_limit(self):
        result = extract_crystal_search('<crystal_search query="" />')
        self.assertIsNotNone(result)
        self.assertEqual(result["query"], "")
        self.assertEqual(result["limit"], 5)

    def test_query_with_custom_limit(self):
        result = extract_crystal_search('<crystal_search query="test" limit="3" />')
        self.assertEqual(result["query"], "test")
        self.assertEqual(result["limit"], 3)

    def test_missing_limit_defaults_to_5(self):
        result = extract_crystal_search('<crystal_search query="something"/>')
        self.assertEqual(result["limit"], 5)

    def test_no_match(self):
        result = extract_crystal_search("<not_a_crystal_tag />")
        self.assertIsNone(result)


class TestCrystalReadRegex(unittest.TestCase):
    """Test crystal_read tag extraction."""

    def test_standard_id(self):
        result = extract_crystal_read('<crystal_read id="crys-003" />')
        self.assertIsNotNone(result)
        self.assertEqual(result["entry_id"], "crys-003")

    def test_raw_numeric_id(self):
        result = extract_crystal_read('<crystal_read id="3" />')
        self.assertIsNotNone(result)
        self.assertEqual(result["entry_id"], "3")

    def test_no_match(self):
        result = extract_crystal_read("<crystal_read />")
        self.assertIsNone(result)


class TestCrystalListRegex(unittest.TestCase):
    """Test crystal_list tag extraction."""

    def test_no_attrs_defaults(self):
        result = extract_crystal_list("<crystal_list />")
        self.assertIsNotNone(result)
        self.assertEqual(result["limit"], 20)
        self.assertEqual(result["offset"], 0)

    def test_custom_limit_and_offset(self):
        result = extract_crystal_list('<crystal_list limit="10" offset="5" />')
        self.assertEqual(result["limit"], 10)
        self.assertEqual(result["offset"], 5)

    def test_limit_only(self):
        result = extract_crystal_list('<crystal_list limit="50" />')
        self.assertEqual(result["limit"], 50)
        self.assertEqual(result["offset"], 0)

    def test_no_match(self):
        result = extract_crystal_list("<wrong_tag />")
        self.assertIsNone(result)


class TestCrystalEditRegex(unittest.TestCase):
    """Test crystal_edit tag extraction."""

    def test_no_summary_no_keywords(self):
        result = extract_crystal_edit(
            '<crystal_edit id="crys-001">body</crystal_edit>'
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["entry_id"], "crys-001")
        self.assertEqual(result["content"], "body")
        self.assertIsNone(result["summary"])
        self.assertIsNone(result["keywords"])

    def test_empty_keywords_attr(self):
        result = extract_crystal_edit(
            '<crystal_edit id="crys-001" keywords="">body</crystal_edit>'
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["keywords"], [])

    def test_populated_keywords(self):
        result = extract_crystal_edit(
            '<crystal_edit id="crys-001" keywords="a,b,c">body</crystal_edit>'
        )
        self.assertEqual(result["keywords"], ["a", "b", "c"])

    def test_summary_without_keywords(self):
        result = extract_crystal_edit(
            '<crystal_edit id="crys-001" summary="new sum">body</crystal_edit>'
        )
        self.assertEqual(result["summary"], "new sum")
        self.assertIsNone(result["keywords"])

    def test_all_attrs_present(self):
        result = extract_crystal_edit(
            '<crystal_edit id="crys-005" summary="updated" keywords="x,y">new body</crystal_edit>'
        )
        self.assertEqual(result["entry_id"], "crys-005")
        self.assertEqual(result["summary"], "updated")
        self.assertEqual(result["keywords"], ["x", "y"])
        self.assertEqual(result["content"], "new body")

    def test_multiline_body_with_dotall(self):
        result = extract_crystal_edit(
            '<crystal_edit id="crys-001">line one\nline two\nline three</crystal_edit>'
        )
        self.assertIn("line one", result["content"])
        self.assertIn("line three", result["content"])

    def test_case_insensitive_tag(self):
        result = extract_crystal_edit(
            '<CRYSTAL_EDIT id="crys-001">body</CRYSTAL_EDIT>'
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["entry_id"], "crys-001")

    def test_whitespace_in_keywords_stripped(self):
        result = extract_crystal_edit(
            '<crystal_edit id="crys-001" keywords=" alpha , beta , gamma ">body</crystal_edit>'
        )
        self.assertEqual(result["keywords"], ["alpha", "beta", "gamma"])

    def test_no_match(self):
        result = extract_crystal_edit("<crystal_edit/>")
        self.assertIsNone(result)


class TestCrystalDeleteRegex(unittest.TestCase):
    """Test crystal_delete tag extraction."""

    def test_no_reason(self):
        result = extract_crystal_delete('<crystal_delete id="crys-003" />')
        self.assertIsNotNone(result)
        self.assertEqual(result["entry_id"], "crys-003")
        self.assertEqual(result["reason"], "")

    def test_with_reason(self):
        result = extract_crystal_delete(
            '<crystal_delete id="crys-003" reason="outdated" />'
        )
        self.assertEqual(result["entry_id"], "crys-003")
        self.assertEqual(result["reason"], "outdated")

    def test_empty_reason_attr(self):
        result = extract_crystal_delete(
            '<crystal_delete id="crys-003" reason="" />'
        )
        self.assertEqual(result["reason"], "")

    def test_no_match(self):
        result = extract_crystal_delete("<wrong />")
        self.assertIsNone(result)


# =====================================================================
# AREA 2 - CrystalStore handler edge cases
# =====================================================================
class TestNormalizeCrystalIdEdgeCases(unittest.TestCase):
    """Edge cases for normalize_crystal_id beyond basic tests."""

    def test_zero_pads_to_three(self):
        self.assertEqual(normalize_crystal_id("0"), "crys-000")

    def test_leading_zeros(self):
        self.assertEqual(normalize_crystal_id("001"), "crys-001")

    def test_large_number(self):
        self.assertEqual(normalize_crystal_id("999"), "crys-999")

    def test_crys_dash_passthrough(self):
        # "crys-" is not all digits and already has prefix, passes through
        self.assertEqual(normalize_crystal_id("crys-"), "crys-")

    def test_whitespace_with_number(self):
        self.assertEqual(normalize_crystal_id(" 42 "), "crys-042")


class TestDeleteEdgeCases(unittest.TestCase):
    """Edge cases for delete_entry on CrystalStore."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_delete_from_empty_store(self):
        result = self.store.delete_entry("crys-001")
        self.assertIsNone(result)

    def test_delete_same_entry_twice(self):
        e = self.store.add_entry("test entry", manual_keywords=["test"])
        first = self.store.delete_entry(e.id)
        self.assertIsNotNone(first)
        second = self.store.delete_entry(e.id)
        self.assertIsNone(second)

    def test_delete_preserves_order_of_remaining(self):
        e1 = self.store.add_entry("first", manual_keywords=["a"])
        e2 = self.store.add_entry("second", manual_keywords=["b"])
        e3 = self.store.add_entry("third", manual_keywords=["c"])
        e4 = self.store.add_entry("fourth", manual_keywords=["d"])

        self.store.delete_entry(e2.id)
        remaining = self.store.get_all()
        self.assertEqual(len(remaining), 3)
        self.assertEqual(remaining[0].id, e1.id)
        self.assertEqual(remaining[1].id, e3.id)
        self.assertEqual(remaining[2].id, e4.id)


class TestUpdateEdgeCases(unittest.TestCase):
    """Edge cases for update_entry on CrystalStore."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_update_empty_keywords_clears(self):
        entry = self.store.add_entry(
            "some content", manual_keywords=["alpha", "beta"]
        )
        # add_entry also auto-extracts keywords, so just verify it has some
        self.assertGreaterEqual(len(entry.keywords), 2)

        updated = self.store.update_entry(
            entry.id, "new body", keywords=[]
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.keywords, [])

    def test_update_very_long_body(self):
        entry = self.store.add_entry("short", manual_keywords=["short"])
        long_body = "x" * 6000
        updated = self.store.update_entry(entry.id, long_body)
        self.assertIsNotNone(updated)
        self.assertEqual(len(updated.body), 6000)

    def test_update_preserves_date_when_not_changed(self):
        entry = self.store.add_entry(
            "original", manual_keywords=["original"], date="2026-01-15"
        )
        original_date = entry.date
        updated = self.store.update_entry(entry.id, "updated body")
        self.assertEqual(updated.date, original_date)

    def test_update_persists_across_fresh_store(self):
        entry = self.store.add_entry("original", manual_keywords=["persist"])
        self.store.update_entry(
            entry.id, "persisted body", keywords=["new_kw"]
        )

        fresh = CrystalStore(self.tmpdir)
        loaded = fresh.get_by_id(entry.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.body, "persisted body")
        self.assertEqual(loaded.keywords, ["new_kw"])


class TestFindByKeywordsEdgeCases(unittest.TestCase):
    """Edge cases for find_by_keywords on CrystalStore."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)
        self.store.add_entry(
            "Insight about hub routing", manual_keywords=["hub", "routing"]
        )
        self.store.add_entry(
            "Insight about config loading", manual_keywords=["config", "loading"]
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_keywords_returns_empty(self):
        results = self.store.find_by_keywords([])
        self.assertEqual(results, [])

    def test_single_keyword_match(self):
        results = self.store.find_by_keywords(["routing"])
        self.assertEqual(len(results), 1)
        self.assertIn("routing", results[0].keywords)

    def test_limit_zero_returns_empty(self):
        results = self.store.find_by_keywords(["hub"], top_k=0)
        self.assertEqual(results, [])


# =====================================================================
# AREA 3 - Context limit truncation
# =====================================================================
MAX_OUTPUT_CHARS = 3000
TRUNCATION_SUFFIX_TEMPLATE = "[truncated, {total} chars total]"


def simulate_list_output(entries, max_chars=MAX_OUTPUT_CHARS):
    """Simulate building the list output with truncation."""
    lines = []
    for entry in entries:
        lines.append(f"  {entry.summary_line()}")
    output = "\n".join(lines)
    if len(output) > max_chars:
        truncated = output[:max_chars]
        suffix = TRUNCATION_SUFFIX_TEMPLATE.format(total=len(output))
        return truncated + "\n" + suffix
    return output


def simulate_read_output(entry, max_chars=MAX_OUTPUT_CHARS):
    """Simulate building the read output with truncation."""
    output = entry.to_block()
    if len(output) > max_chars:
        truncated = output[:max_chars]
        suffix = TRUNCATION_SUFFIX_TEMPLATE.format(total=len(output))
        return truncated + "\n" + suffix
    return output


class TestListOutputTruncation(unittest.TestCase):
    """Test that list output truncates when entries exceed limit."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)
        # Create 50+ entries
        for i in range(55):
            self.store.add_entry(
                f"Insight number {i} about topic_{i} with enough text to be substantive "
                f"and include keywords like keyword_{i} and detail_{i}",
                manual_keywords=[f"topic_{i}", f"keyword_{i}"],
            )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_list_output_truncated_at_limit(self):
        entries = self.store.get_all()
        output = simulate_list_output(entries, max_chars=MAX_OUTPUT_CHARS)
        self.assertGreater(len(output), MAX_OUTPUT_CHARS)
        self.assertIn("[truncated,", output)
        self.assertIn("chars total]", output)

    def test_list_output_under_limit_not_truncated(self):
        entries = self.store.get_all()[:3]
        output = simulate_list_output(entries, max_chars=MAX_OUTPUT_CHARS)
        self.assertNotIn("[truncated,", output)


class TestReadOutputTruncation(unittest.TestCase):
    """Test that read output truncates for very large entries."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_read_large_entry_truncated(self):
        long_body = "x" * 5000
        entry = self.store.add_entry(
            long_body, manual_keywords=["long", "test"]
        )
        output = simulate_read_output(entry, max_chars=MAX_OUTPUT_CHARS)
        self.assertIn("[truncated,", output)
        self.assertIn("chars total]", output)
        # Verify the total char count is reported (to_block adds metadata overhead)
        total_str = output.split("chars total]")[0].split("[truncated,")[1].strip()
        self.assertGreater(int(total_str), 5000)

    def test_read_small_entry_not_truncated(self):
        entry = self.store.add_entry(
            "Short body", manual_keywords=["short"]
        )
        output = simulate_read_output(entry, max_chars=MAX_OUTPUT_CHARS)
        self.assertNotIn("[truncated,", output)

    def test_truncation_suffix_contains_total_chars(self):
        long_body = "A" * 4500
        entry = self.store.add_entry(
            long_body, manual_keywords=["big"]
        )
        output = simulate_read_output(entry, max_chars=MAX_OUTPUT_CHARS)
        # The suffix should contain the full output length
        block_len = len(entry.to_block())
        self.assertIn(f"{block_len} chars total]", output)


if __name__ == "__main__":
    unittest.main()
