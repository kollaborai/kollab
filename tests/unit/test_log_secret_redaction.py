"""Provider keys must never reach a log file, whichever logger wrote them (#835, #843).

The redaction filter used to run only where code called it by hand, so a
provider's own logger (the Gemini provider logs its request URL, which carries
``?key=``) wrote the key to kollab.log. Every kollab log handler must redact
secrets from the message, its arguments and exception text.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from kollabor.logging.setup import LoggingSetup
from kollabor_engine.__main__ import _build_log_handler, _RedactingStream

# A full-length Google API key shape: "AIza" plus 35 URL-safe characters. Not a real key.
_KEY = "AIzaSyD" + "Q7x9_kL2-mN4pR6tV8wY0zB3cE5gH1jK"
_URL = f"https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key={_KEY}"
_GEMINI_LOGGER = "kollabor_ai.providers.gemini_provider"


def _emit_gemini_records(logger: logging.Logger) -> None:
    logger.warning("Gemini request failed: %s", f"400 Bad Request for url {_URL}")
    logger.error(f"Gemini rejected key {_KEY}")
    try:
        raise RuntimeError(f"invalid API key {_KEY} for {_URL}")
    except RuntimeError:
        logger.exception("Gemini call failed")
    logger.warning("retry count=%d", 5)


def _app_log_text(log_dir: Path) -> str:
    """Run Gemini-provider records through LoggingSetup's real handler; return the file."""
    assert len(_KEY) == 39
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    log_file = log_dir / "kollab.log"
    try:
        LoggingSetup().setup_from_config({"logging": {"file": str(log_file), "level": "DEBUG"}})
        _emit_gemini_records(logging.getLogger(_GEMINI_LOGGER))
        for handler in root.handlers[:]:
            handler.flush()
        return log_file.read_text(encoding="utf-8")
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)


def test_app_log_file_never_holds_a_google_key(tmp_path: Path) -> None:
    text = _app_log_text(tmp_path)
    assert "Gemini request failed" in text
    assert "Gemini call failed" in text
    assert "RuntimeError" in text
    assert _KEY not in text


def test_app_log_redaction_keeps_ordinary_formatting(tmp_path: Path) -> None:
    text = _app_log_text(tmp_path)
    assert "retry count=5" in text


def test_engine_log_handler_never_holds_a_google_key(tmp_path: Path) -> None:
    log_file = tmp_path / "engine.log"
    handler = _build_log_handler(log_file)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("redaction.engine.probe")
    saved_level, saved_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        _emit_gemini_records(logger)
        handler.flush()
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate
    text = log_file.read_text(encoding="utf-8")
    assert "Gemini call failed" in text
    assert "retry count=5" in text
    assert _KEY not in text


def test_engine_stderr_stream_never_holds_a_google_key() -> None:
    sink = io.StringIO()
    _RedactingStream(sink).write(f"Traceback (most recent call last):\nRuntimeError: invalid API key {_KEY}\n")
    assert "RuntimeError: invalid API key" in sink.getvalue()
    assert _KEY not in sink.getvalue()
