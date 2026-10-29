"""Fast model routing (#260): selection and fallback."""

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "packages/kollabor-voice/src")
)

from kollabor_voice.responder import RESPONDER_KEY, responder_model

from kollabor_ai.fast_model import SHARED_KEY, resolve_fast_model, with_model


class Cfg:
    def __init__(self, **d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


class Conf:
    def __init__(self, model):
        self.model = model

    def model_copy(self, update):
        return Conf(update["model"])


class Prov:
    def __init__(self):
        self.model = "big"
        self.config = Conf("big")


def test_unset_falls_back_to_none_and_same_provider():
    assert resolve_fast_model(Cfg(), "plugins.hub.dreaming_model") is None
    assert resolve_fast_model(None, "x") is None
    p = Prov()
    assert with_model(p, None) is p
    assert with_model(p, "big") is p


def test_specific_beats_shared_beats_none():
    cfg = Cfg(**{SHARED_KEY: "haiku", "plugins.hub.dreaming_model": " mini "})
    assert resolve_fast_model(cfg, "plugins.hub.dreaming_model") == "mini"
    assert resolve_fast_model(cfg, "other") == "haiku"
    assert responder_model(Cfg(**{RESPONDER_KEY: "flash"})) == "flash"


def test_responder_is_a_loadout_name_own_key_only():
    # The voice quick model names a loadout (provider + model in one), so it
    # never falls back to the shared fast-model id: that key is a bare model
    # id for compaction and dreaming, not a loadout name.
    assert responder_model(Cfg(**{SHARED_KEY: "haiku"})) is None
    assert responder_model(Cfg(**{RESPONDER_KEY: " fast-or "})) == "fast-or"
    assert responder_model(Cfg(**{RESPONDER_KEY: "  "})) is None
    assert responder_model(None) is None
    assert responder_model(Cfg(**{RESPONDER_KEY: 42})) is None


def test_with_model_copies_not_mutates():
    p = Prov()
    q = with_model(p, "haiku")
    assert q is not p and q.model == "haiku" and q.config.model == "haiku"
    assert p.model == "big" and p.config.model == "big"
