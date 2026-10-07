import re

from kollabor_ai.session_naming import session_display_name


def test_session_display_name_strips_timestamp_from_generated_slug():
    assert session_display_name("2512111430-nexus-flux") == "nexus-flux"


def test_session_display_name_is_stable_and_friendly_for_uuid_ids():
    session_id = "1c753a7d2def481084e3a64bcb09a7d2"

    name = session_display_name(session_id)

    assert re.fullmatch(r"[a-z]+-[a-z]+", name)
    assert name != session_id
    assert session_display_name(session_id) == name


def test_session_display_name_accepts_prefixed_uuid_ids():
    assert session_display_name("sess_1c753a7d2def481084e3a64bcb09a7d2") == (
        session_display_name("1c753a7d2def481084e3a64bcb09a7d2")
    )
