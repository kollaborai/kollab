"""Regression coverage for the ACP lifecycle test's process scan authorization."""

from tests.process_scan_guard import HOST_PROCESS_SCAN_TESTS


def test_deferred_acp_lifecycle_is_registered_for_sanctioned_process_scans():
    assert "test_real_terminal_deferred_acp_hub_lifecycle" in HOST_PROCESS_SCAN_TESTS[
        "integration/test_acp_application_deferred_e2e.py"
    ]
