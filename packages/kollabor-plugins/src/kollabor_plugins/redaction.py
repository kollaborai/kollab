"""Shared redaction rules for plugin metadata and diagnostics."""

import re
from collections.abc import Iterable

_SECRET_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9])(?P<key>api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
    r"password|credential|authorization|bearer)(?![A-Za-z0-9])\s*[:=]\s*(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_AUTH_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+[^\s,;]+")
_SECRET_VALUE = re.compile(
    r"(?i)\b(?:secret[-_]token[-_][A-Za-z0-9_-]{6,}|sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]*"
)
_URL = re.compile(r"(?:https?|ssh)://[^\s'\"<>]+", re.IGNORECASE)
_PATH = re.compile(
    r"(?P<quote>['\"])(?:~|/(?!/)|[A-Za-z]:\\)[^'\"]*(?P=quote)"
    r"|(?<![A-Za-z0-9._~:-])(?:~|/(?!/)|[A-Za-z]:\\)[^\s'\"]*"
)
_SAFE_TEXT_VALUES = {"budget", "length", "required"}


def contains_sensitive_text(text: str, *, allow_safe_values: bool = False) -> bool:
    """Return whether text contains a credential or a credential-shaped value."""
    if _SECRET_VALUE.search(text) or _AUTH_SCHEME.search(text):
        return True
    for match in _SECRET_ASSIGNMENT.finditer(text):
        value = match.group("value").rstrip(".!?").lower()
        if allow_safe_values and value in _SAFE_TEXT_VALUES:
            continue
        return True
    return False


def redact_sensitive_text(
    text: str,
    secret_values: Iterable[str] = (),
    *,
    replacement: str = "[redacted]",
    url_replacement: str | None = None,
    redact_paths: bool = True,
) -> str:
    """Remove declared credentials, common token forms, URLs, and absolute paths."""
    for value in sorted((value for value in secret_values if value), key=len, reverse=True):
        text = text.replace(value, replacement)
    text = _URL.sub(url_replacement or replacement, text)
    text = _AUTH_SCHEME.sub(lambda match: f"{match.group(1)} {replacement}", text)
    text = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}: {replacement}", text
    )
    text = _SECRET_VALUE.sub(replacement, text)
    return _PATH.sub("[path]", text) if redact_paths else text
