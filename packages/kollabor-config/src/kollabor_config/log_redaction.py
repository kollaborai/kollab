"""Join-code redaction for log handlers, shared by the app and the engine.

Enrollment codes are one-device credentials. A person can still paste one into
a command or a form, so no log record may carry it. This covers the short
XXXX-XXXX code as it is shown, in either case; the no-dash form never matches
(false positives).
"""

from __future__ import annotations

import logging
import re

ENROLLMENT_CODE_RE = re.compile(
    r"\b[0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{4}-[0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{4}\b"
)


def redact_join_codes(text: str) -> str:
    """Replace every join code in *text*, the same way log records are."""
    return ENROLLMENT_CODE_RE.sub("[join code redacted]", text)


class JoinCodeRedactionFilter(logging.Filter):
    """Rewrite any log record that carries a join code, for every formatter.

    One filter attached to each handler, so compact, standard and custom format
    strings all emit ``[join code redacted]`` instead of the code.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True  # a malformed record is the formatter's problem, not ours
        redacted = redact_join_codes(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True
