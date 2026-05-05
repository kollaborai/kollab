"""Tests for plugins.hub.text_utils -- keyword extraction and matching."""

import unittest

from plugins.hub.text_utils import (
    STOPWORDS,
    extract_keywords,
    generate_ngrams,
    keyword_overlap,
    normalize_token,
    score_relevance,
    stem,
    tokenize,
)


class TestNormalizeToken(unittest.TestCase):
    def test_lowercase(self):
        self.assertEqual(normalize_token("Hello"), "hello")

    def test_strip_punctuation(self):
        self.assertEqual(normalize_token("**bold**"), "bold")
        self.assertEqual(normalize_token("(parens)"), "parens")
        self.assertEqual(normalize_token("trail."), "trail")

    def test_empty(self):
        self.assertEqual(normalize_token(""), "")


class TestStem(unittest.TestCase):
    def test_ing_suffix(self):
        self.assertEqual(stem("debugging"), "debug")
        self.assertEqual(stem("checking"), "check")
        self.assertEqual(stem("broadcasting"), "broadcast")

    def test_ed_suffix(self):
        self.assertEqual(stem("crashed"), "crash")
        self.assertEqual(stem("fixed"), "fix")

    def test_preserves_short_words(self):
        self.assertEqual(stem("run"), "run")
        self.assertEqual(stem("bug"), "bug")

    def test_preserves_paths(self):
        self.assertEqual(stem("vault.py"), "vault.py")
        self.assertEqual(stem("some_func"), "some_func")
        self.assertEqual(stem("path/to/file"), "path/to/file")

    def test_double_consonant_dedup(self):
        # debugging -> debugg -> debug (double g removed)
        self.assertEqual(stem("debugging"), "debug")
        self.assertEqual(stem("stopping"), "stop")
        self.assertEqual(stem("running"), "run")

    def test_plural(self):
        self.assertEqual(stem("agents"), "agent")
        # Don't strip -ss
        self.assertEqual(stem("process"), "process")

    def test_ation_suffix(self):
        self.assertEqual(stem("documentation"), "document")


class TestTokenize(unittest.TestCase):
    def test_removes_stopwords(self):
        tokens = tokenize("the agent is in the hub")
        self.assertNotIn("the", tokens)
        self.assertNotIn("is", tokens)
        self.assertNotIn("in", tokens)
        self.assertIn("agent", tokens)
        self.assertIn("hub", tokens)

    def test_removes_short_tokens(self):
        tokens = tokenize("a b cd efg")
        self.assertNotIn("a", tokens)
        self.assertNotIn("b", tokens)

    def test_normalizes(self):
        tokens = tokenize("Hello WORLD")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)


class TestExtractKeywords(unittest.TestCase):
    def test_file_paths(self):
        kw = extract_keywords("check plugins/hub/vault.py for bugs")
        self.assertIn("plugins/hub/vault.py", kw)

    def test_backticked_terms(self):
        kw = extract_keywords("use `get_rebirth_context` method")
        self.assertIn("get_rebirth_context", kw)

    def test_snake_case(self):
        kw = extract_keywords("the crystal_store handles storage")
        self.assertIn("crystal_store", kw)

    def test_deduplicates(self):
        kw = extract_keywords("hub hub hub routing routing")
        hub_count = kw.count("hub")
        self.assertEqual(hub_count, 1)

    def test_non_empty_for_technical_text(self):
        kw = extract_keywords(
            "The prompt_renderer.py has a bug in TRENDER_HUB_IDENTITY_PATTERN"
        )
        self.assertTrue(len(kw) > 3)


class TestGenerateNgrams(unittest.TestCase):
    def test_basic(self):
        tokens = ["a", "b", "c", "d"]
        ngrams = generate_ngrams(tokens, 3)
        self.assertEqual(ngrams, [("a", "b", "c"), ("b", "c", "d")])

    def test_short_input(self):
        # Fewer tokens than n
        ngrams = generate_ngrams(["a", "b"], 3)
        self.assertEqual(ngrams, [("a", "b")])

    def test_empty(self):
        self.assertEqual(generate_ngrams([], 3), [])

    def test_exact_n(self):
        ngrams = generate_ngrams(["a", "b", "c"], 3)
        self.assertEqual(ngrams, [("a", "b", "c")])


class TestKeywordOverlap(unittest.TestCase):
    def test_identical(self):
        kw = ["hub", "routing", "message"]
        self.assertAlmostEqual(keyword_overlap(kw, kw), 1.0)

    def test_disjoint(self):
        self.assertAlmostEqual(
            keyword_overlap(["a", "b"], ["c", "d"]), 0.0
        )

    def test_partial(self):
        overlap = keyword_overlap(["hub", "routing"], ["hub", "socket"])
        self.assertGreater(overlap, 0.0)
        self.assertLess(overlap, 1.0)

    def test_empty(self):
        self.assertAlmostEqual(keyword_overlap([], ["a"]), 0.0)
        self.assertAlmostEqual(keyword_overlap(["a"], []), 0.0)


class TestScoreRelevance(unittest.TestCase):
    def test_exact_matches(self):
        # 2 exact token hits (1.0 each) + 1 bigram hit ("hub","routing") (0.65) = 2.65
        score = score_relevance(["hub", "routing"], ["hub", "routing", "socket"])
        self.assertAlmostEqual(score, 2.65)

    def test_no_match(self):
        score = score_relevance(["alpha"], ["beta", "gamma"])
        self.assertEqual(score, 0.0)

    def test_substring_partial(self):
        # "debug" is substring of "debugging" -> 0.3 score
        score = score_relevance(["debug"], ["debugging"])
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)

    def test_empty_inputs(self):
        self.assertEqual(score_relevance([], ["a"]), 0.0)
        self.assertEqual(score_relevance(["a"], []), 0.0)


class TestStopwords(unittest.TestCase):
    def test_common_words_present(self):
        for word in ["the", "is", "a", "an", "and", "or", "but", "in"]:
            self.assertIn(word, STOPWORDS)

    def test_technical_words_absent(self):
        for word in ["hub", "vault", "agent", "socket", "daemon"]:
            self.assertNotIn(word, STOPWORDS)


if __name__ == "__main__":
    unittest.main()
