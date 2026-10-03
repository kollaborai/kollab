"""Join codes never reach the engine log or the app log filter."""

import logging

CODE = "ABCD-EFGH"


def test_engine_log_handler_redacts_join_codes(tmp_path):
    from kollabor_engine.__main__ import _build_log_handler

    log = tmp_path / "engine.log"
    handler = _build_log_handler(log)
    logger = logging.getLogger("test_engine_log_handler_redacts")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.info("POST body code=%s then %s", CODE, CODE.lower())
    finally:
        logger.removeHandler(handler)
        handler.close()
    text = log.read_text()
    assert CODE not in text and CODE.lower() not in text and "[join code redacted]" in text


def test_app_logging_setup_still_exports_the_shared_redaction():
    from kollabor.logging import setup
    from kollabor_config import log_redaction

    assert setup.redact_join_codes is log_redaction.redact_join_codes
    assert setup.JoinCodeRedactionFilter is log_redaction.JoinCodeRedactionFilter
