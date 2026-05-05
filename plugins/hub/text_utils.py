"""Text utilities for crystallized memory retrieval.

Stopword removal, keyword extraction, n-gram generation, and
overlap scoring for the vault nudge system. Pure python, no deps.
"""

import re
from typing import List, Set, Tuple

# Common english stopwords -- kept minimal for speed
STOPWORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "as", "is", "was", "are",
    "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "shall",
    "can", "need", "must", "ought", "i", "you", "he", "she", "it",
    "we", "they", "me", "him", "her", "us", "them", "my", "your",
    "his", "its", "our", "their", "mine", "yours", "hers", "ours",
    "theirs", "this", "that", "these", "those", "what", "which",
    "who", "whom", "whose", "when", "where", "why", "how", "all",
    "each", "every", "both", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "than",
    "too", "very", "just", "because", "if", "then", "else", "about",
    "up", "out", "into", "through", "during", "before", "after",
    "above", "below", "between", "under", "again", "further", "once",
    "here", "there", "also", "any", "many", "much", "even", "still",
    "already", "always", "never", "often", "sometimes", "now",
    "however", "although", "though", "while", "since", "until",
    "unless", "whether", "either", "neither", "yet", "like",
    "get", "got", "make", "made", "take", "took", "come", "came",
    "go", "went", "gone", "see", "seen", "know", "knew", "known",
    "think", "thought", "say", "said", "tell", "told", "give",
    "gave", "given", "find", "found", "want", "look", "use", "used",
    "new", "old", "well", "way", "thing", "things", "work", "first",
    "also", "over", "after", "one", "two", "three", "don", "doesn",
    "didn", "won", "isn", "aren", "wasn", "weren", "hasn", "haven",
    "hadn", "wouldn", "couldn", "shouldn", "t", "s", "re", "ve",
    "ll", "d", "m", "n", "o", "e",
}

# Patterns for extracting technical identifiers
_FILE_PATH_RE = re.compile(r"[\w./\\-]+\.(?:py|md|json|js|ts|yaml|yml|toml|cfg|sh)")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_SNAKE_CASE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_CAMEL_CASE_RE = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b")
_UPPER_CONST_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
_WORD_RE = re.compile(r"[a-zA-Z0-9_./\\-]+")


def normalize_token(word: str) -> str:
    """Lowercase and strip surrounding punctuation."""
    word = word.strip(".,;:!?\"'()[]{}#*<>|~`")
    return word.lower()


def stem(word: str) -> str:
    """Light stemmer that removes common english suffixes.

    Strategy: strip longest matching suffix first, but only if
    the remaining stem is at least 3 chars. Handles doubled
    consonants before -ing/-ed (debugging -> debug).
    Preserves file paths and identifiers (anything with / _ or .).
    """
    # Don't stem file paths, identifiers, or very short words
    if "/" in word or "_" in word or "." in word or len(word) < 4:
        return word

    # Try suffixes longest-first
    for suffix in (
        "ation", "ction", "ating",
        "ment", "ness", "ally", "edly",
        "able", "ible",
        "ing", "ied", "ies", "ers", "ful",
        "ed", "er", "ly",
    ):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            candidate = word[: -len(suffix)]
            # De-double trailing consonant: debugging -> debugg -> debug
            if (
                len(candidate) >= 4
                and candidate[-1] == candidate[-2]
                and candidate[-1] not in "aeiou"
            ):
                candidate = candidate[:-1]
            return candidate

    # Plural -s (not -ss, -us, -is)
    if (
        word.endswith("s")
        and not word.endswith(("ss", "us", "is"))
        and len(word) > 3
    ):
        return word[:-1]

    return word


def tokenize(text: str) -> List[str]:
    """Split text into normalized tokens, removing stopwords."""
    raw = _WORD_RE.findall(text)
    tokens = []
    for w in raw:
        norm = normalize_token(w)
        if norm and norm not in STOPWORDS and len(norm) > 1:
            tokens.append(norm)
    return tokens


def extract_keywords(text: str, apply_stem: bool = True) -> List[str]:
    """Extract meaningful keywords from text.

    Pulls out: file paths, backticked terms, snake_case identifiers,
    CamelCase names, UPPER_CONSTANTS, and remaining content words
    after stopword removal. Applies stemming for better matching.
    Returns deduplicated list.
    """
    keywords: List[str] = []
    seen: Set[str] = set()

    def _add(term: str) -> None:
        norm = normalize_token(term)
        if not norm or norm in STOPWORDS or len(norm) < 2:
            return
        # Apply stemming for content words (not paths/identifiers)
        stemmed = stem(norm) if apply_stem else norm
        if stemmed and stemmed not in seen:
            seen.add(stemmed)
            keywords.append(stemmed)

    # File paths (high value)
    for match in _FILE_PATH_RE.finditer(text):
        _add(match.group())
        # Also add the filename without path
        parts = match.group().replace("\\", "/").split("/")
        if len(parts) > 1:
            _add(parts[-1])

    # Backticked terms (explicitly marked as important)
    for match in _BACKTICK_RE.finditer(text):
        term = match.group(1).strip()
        if term:
            _add(term)
            # Split compound backtick terms
            for part in term.replace("/", " ").replace(".", " ").split():
                _add(part)

    # Snake case identifiers
    for match in _SNAKE_CASE_RE.finditer(text):
        _add(match.group())

    # CamelCase names
    for match in _CAMEL_CASE_RE.finditer(text):
        _add(match.group())

    # UPPER_CONSTANTS
    for match in _UPPER_CONST_RE.finditer(text):
        _add(match.group())

    # Remaining content words (after stopword removal)
    for token in tokenize(text):
        _add(token)

    return keywords


def generate_ngrams(tokens: List[str], n: int = 3) -> List[Tuple[str, ...]]:
    """Generate sliding-window n-grams from a token list.

    If len(tokens) < n, returns the tokens as a single tuple rather than
    an empty list — preserves signal for short inputs.
    """
    if not tokens:
        return []
    if len(tokens) < n:
        return [tuple(tokens)]
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def keyword_overlap(keywords_a: List[str], keywords_b: List[str]) -> float:
    """Compute Jaccard similarity between two keyword lists.

    Returns 0.0 to 1.0. Used for dedup detection.
    """
    if not keywords_a or not keywords_b:
        return 0.0
    set_a = set(keywords_a)
    set_b = set(keywords_b)
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def score_relevance(
    query_keywords: List[str],
    entry_keywords: List[str],
) -> float:
    """Score how relevant an entry is to a query.

    Uses weighted matching:
      - exact keyword hits (weight 1.0)
      - bigram phrase matches (weight 0.65) -- catches multi-word concepts
      - substring matches (weight 0.3) -- catches partial terms

    Returns a float score where higher = more relevant.
    """
    if not query_keywords or not entry_keywords:
        return 0.0

    query_set = set(query_keywords)
    entry_set = set(entry_keywords)

    # Exact token matches (weight 1.0 each)
    exact = len(query_set & entry_set)

    # Bigram phrase matches (weight 0.65 each) -- multi-word concepts like
    # "message routing" or "crystal store" score higher than single tokens
    query_bigrams = set(generate_ngrams(query_keywords, 2))
    entry_bigrams = set(generate_ngrams(entry_keywords, 2))
    bigram_score = len(query_bigrams & entry_bigrams) * 0.65

    # Substring matches (weight 0.3 each) -- catch partial terms
    substring_score = 0.0
    for qt in query_set:
        if qt in entry_set:
            continue  # already counted as exact
        for et in entry_set:
            if qt in et or et in qt:
                substring_score += 0.3
                break

    return exact + bigram_score + substring_score
