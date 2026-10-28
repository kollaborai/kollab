import pytest
from kollabor_voice import speech_settings


@pytest.fixture(autouse=True)
def _not_apple_silicon(monkeypatch):
    """Every host runs as CI's Linux does: Whisper by default, never a real Parakeet setup.

    A test about Parakeet sets apple_silicon itself.
    """
    monkeypatch.setattr(speech_settings, "apple_silicon", lambda: False)
