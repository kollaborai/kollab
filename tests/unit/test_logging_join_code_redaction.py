"""Join-code redaction must hold for every logging formatter, not just compact.

docs/specs/agent-network-simple-flow.md section 8: the code never enters a
log. A non-default ``logging.format_type`` must not be the hole it falls
through, a lower-case dashed code is still a code, and the RPC client must
never %r-log a raw reply (an enrollment-offer reply carries the code).
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from kollabor.logging.setup import LoggingSetup
from kollabor_rpc.client import RpcClient

_UPPER_CODE = "7QK4-M2XP"
_LOWER_CODE = "7qk4-m2xp"


def _log_one_line(log_dir: Path, config: dict) -> str:
    """Run one warning record through LoggingSetup's real handler; return the file."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    log_file = log_dir / "kollab.log"
    try:
        LoggingSetup().setup_from_config({"logging": {"file": str(log_file), **config}})
        logging.getLogger("redaction.probe").warning(
            "rpc reply missing request_id: %r",
            {"result": {"code": _UPPER_CODE, "also": _LOWER_CODE}},
        )
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


def _rpc_missing_request_id_warning() -> str:
    """Feed on_reply a reply carrying a code; return what its logger emitted."""
    client = RpcClient.__new__(RpcClient)
    client._pending = {}
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    rpc_logger = logging.getLogger("kollabor_rpc.client")
    saved_level = rpc_logger.level
    rpc_logger.addHandler(handler)
    rpc_logger.setLevel(logging.WARNING)
    try:
        client.on_reply(
            {
                "result": {
                    "status": "offered",
                    "offer_id": "a" * 32,
                    "expires_at": "1790000000",
                    "code": _UPPER_CODE,
                }
            }
        )
        handler.flush()
        return stream.getvalue()
    finally:
        rpc_logger.removeHandler(handler)
        handler.close()
        rpc_logger.setLevel(saved_level)


def test_standard_formatter_redacts_upper_and_lower_codes(tmp_path: Path) -> None:
    content = _log_one_line(tmp_path, {"format_type": "standard"})
    assert _UPPER_CODE not in content
    assert _LOWER_CODE not in content
    assert "[join code redacted]" in content


def test_custom_format_string_redacts_codes(tmp_path: Path) -> None:
    content = _log_one_line(
        tmp_path, {"format_type": "custom", "format": "%(message)s"}
    )
    assert _UPPER_CODE not in content
    assert _LOWER_CODE not in content
    assert "[join code redacted]" in content


def test_compact_formatter_still_redacts_codes(tmp_path: Path) -> None:
    content = _log_one_line(tmp_path, {"format_type": "compact"})
    assert _UPPER_CODE not in content
    assert _LOWER_CODE not in content
    assert "[join code redacted]" in content


def test_no_dash_form_is_not_redacted(tmp_path: Path) -> None:
    """Without the dash it is indistinguishable from ordinary text; leave it."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    log_file = tmp_path / "kollab.log"
    try:
        LoggingSetup().setup_from_config(
            {"logging": {"file": str(log_file), "format_type": "standard"}}
        )
        logging.getLogger("redaction.probe").warning("harmless token 7QK4M2XP here")
        for handler in root.handlers[:]:
            handler.flush()
        content = log_file.read_text(encoding="utf-8")
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
    assert "7QK4M2XP" in content
    assert "[join code redacted]" not in content


def test_rpc_reply_warning_never_carries_the_code() -> None:
    emitted = _rpc_missing_request_id_warning()
    assert "missing request_id" in emitted
    assert _UPPER_CODE not in emitted
    assert _LOWER_CODE not in emitted
    assert "offered" not in emitted  # no reply payload values either
