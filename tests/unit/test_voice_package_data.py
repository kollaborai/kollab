"""The kollab wheel ships every file the voice manifest names.

Through 0.16.8 the kollabor_voice package-data listed only manifest.json, so
every packaged build (wheel, binary, Kollab.app) left out the Laya heads and
the classifier failed its integrity check on the first /voice.
"""

import fnmatch
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "packages" / "kollabor-voice" / "src" / "kollabor_voice"


def test_wheel_package_data_covers_the_laya_heads():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    globs = config["tool"]["setuptools"]["package-data"]["kollabor_voice"]
    laya = json.loads((VOICE / "manifest.json").read_text())["classifiers"]["laya"]
    for head in (laya["voice_head"], laya["speech_head"]):
        assert (VOICE / head["name"]).is_file()
        assert any(fnmatch.fnmatch(head["name"], glob) for glob in globs), head
