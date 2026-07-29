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
    the remaining stem is at least 4 chars. Handles doubled
    consonants before -ing/-ed (debugging -> debug). Restores
    silent 'e' for -ing/-ed stems (arriving -> arrive).
    Preserves file paths, identifiers, pure numbers, and short tokens.

    Common words that would produce garbage stems are preserved
    (during, rather, etc.).
    """
    # Don't stem file paths, identifiers, or very short words
    if "/" in word or "_" in word or "." in word or len(word) < 4:
        return word

    # Skip pure numbers
    if word.isdigit():
        return word

    # Common words that should never be stemmed (would produce garbage)
    _PRESERVE = frozenset({
        "during", "rather", "other", "after", "where", "there", "their",
        "which", "would", "could", "should", "every", "still", "being",
        "thing", "things", "going", "since", "until", "though",
        "while", "these", "those", "whose",
    })
    if word in _PRESERVE:
        return word

    # Try suffixes longest-first with optional replacement text
    for suffix, replacement in (
        ("ation", ""),
        ("ction", "ct"),
        ("ating", "ate"),
        ("ment", ""),
        ("ness", ""),
        ("ally", ""),
        ("edly", ""),
        ("able", ""),
        ("ible", ""),
        ("ing", ""),
        ("ied", "y"),
        ("ies", "y"),
        ("ers", ""),
        ("ful", ""),
        ("ed", ""),
        ("er", ""),
        ("ly", ""),
    ):
        if word.endswith(suffix) and len(word) - len(suffix) + len(replacement) >= 3:
            candidate = word[: -len(suffix)] + replacement
            # De-double trailing consonant: debugging -> debugg -> debug
            # Only for actual doubled consonants from suffix addition.
            # "ss" pairs (process) and "ll" pairs (install) are inherent.
            _dedoubled = False
            if (
                len(candidate) >= 4
                and candidate[-1] == candidate[-2]
                and candidate[-1] not in "aeiou"
                and candidate[-1] not in "sl"
            ):
                candidate = candidate[:-1]
                _dedoubled = True
            # Restore silent 'e' for -ing/-ed stems (skip if de-doubled):
            # arriving -> arriv -> arrive, truncated -> truncat -> truncate
            if (
                suffix in ("ing", "ed")
                and not replacement
                and not _dedoubled
                and len(candidate) >= 4
            ):
                _VOWELS = "aeiou"
                if (
                    candidate[-1] not in _VOWELS
                    and candidate[-2] in _VOWELS
                    and candidate[-3] not in _VOWELS
                    and candidate[-1] not in "wxy"
                ):
                    candidate = candidate + "e"
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
        # Skip pure numbers and short numeric-ish tokens (noise)
        if norm.isdigit() or (len(norm) <= 2 and not norm.isalpha()):
            return
        # Skip "e.g", "i.e", etc.
        if "." in norm and len(norm) <= 3:
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


def summary_similarity(summary_a: str, summary_b: str) -> float:
    """Compute word-level overlap between two summary strings.

    Tokenizes and stems both summaries (removing stopwords), then
    computes a blended score:
      - overlap coefficient (intersection / min(|A|, |B|))
      - weighted by a size confidence factor

    Short summaries (fewer than 4 content tokens) get penalized
    because a single shared word can produce a high overlap score
    by chance. This prevents false merges like "First insight about
    hub" vs "Second insight about config" (sharing only "insight").

    Returns 0.0 to 1.0.
    """
    tokens_a = set(stem(t) for t in tokenize(summary_a))
    tokens_b = set(stem(t) for t in tokenize(summary_b))
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    smaller = min(len(tokens_a), len(tokens_b))
    overlap = len(intersection) / smaller if smaller else 0.0

    # Size confidence: penalize very short summaries where 1 shared
    # word can produce a high score by chance.
    # 4+ tokens = full confidence, 3 = 0.75, 2 = 0.50, 1 = 0.25
    size_confidence = min(smaller / 4.0, 1.0)

    return overlap * size_confidence


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
