from plugins.hub import relay_agent as ra


def test_short_code_shape_takes_the_anchor_tail():
    ok = ra._SHORT_CODE_SHAPE.fullmatch
    assert ok("ABCD-2345") and ok("ABCD-2345-6789-ABCD")
    assert not ok("ABCD-2345-6789") and not ok("ABCD-2345-6789-ABC")


def test_network_mismatch_is_a_known_error():
    assert ra._safe_enrollment_result({"error": "network_mismatch"}) == {"error": "network_mismatch"}


def test_approved_passes_only_founder_and_warning():
    got = ra._safe_enrollment_result(
        {"status": "approved", "founder": "ab" * 32, "warning": "w", "key": "k", "proof": {"x": 1}}
    )
    assert got == {"status": "approved", "founder": "ab" * 32, "warning": "w"}
    assert ra._safe_enrollment_result({"status": "rejected", "founder": "x"}) == {"status": "rejected"}
    assert ra._safe_enrollment_result({"status": "approved", "founder": 5}) == {"status": "approved"}
