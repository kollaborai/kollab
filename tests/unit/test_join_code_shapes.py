"""The join code is XXXX-XXXX-AAAA-AAAA-AAAA (F4-3): every offer validator must pass it."""

import pathlib

from plugins.altview.connect_altview import _SHORT_CODE_RE
from plugins.altview.connect_style import CODE_RE

LONG = "ABCD-EFGH-JKMN-PQRS-TVWX"


def test_screen_patterns_take_the_anchored_code():
    assert _SHORT_CODE_RE.fullmatch(LONG) and _SHORT_CODE_RE.fullmatch(LONG[:9])
    assert CODE_RE.match(LONG).group(0) == LONG
    assert not _SHORT_CODE_RE.fullmatch(LONG[:14])


def test_no_offer_validator_is_eight_characters_only():
    root = pathlib.Path(__file__).resolve().parents[2]
    bad = r'fullmatch(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}", code)'
    files = (
        "plugins/hub/plugin.py",
        "kollabor/state/handlers.py",
        "kollabor/state/local.py",
        "kollabor/state/remote.py",
    )
    for rel in files:
        assert bad not in (root / rel).read_text(), rel
