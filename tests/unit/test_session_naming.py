import re
from unittest.mock import patch

from kollabor_ai.session_naming import generate_session_name, session_display_name


def test_session_display_name_strips_timestamp_from_generated_slug():
    assert session_display_name("2512111430-nexus-flux") == "nexus-flux"


def test_generated_names_differ_within_the_same_minute():
    # Same minute, frozen clock: the word pair alone has 800 combos, and a
    # collision would let save_session truncate another conversation's file.
    with patch("kollabor_ai.session_naming.datetime") as frozen:
        frozen.now.return_value.strftime.return_value = "2512111430"
        names = [generate_session_name() for _ in range(2)]
    assert names[0] != names[1]
    assert all(re.fullmatch(r"2512111430-[a-z]+-[a-z]+-\d[a-z0-9]{3}", name) for name in names)


def test_session_display_name_strips_the_uniqueness_token():
    assert session_display_name("2512111430-nexus-flux-7k2x") == "nexus-flux"


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
