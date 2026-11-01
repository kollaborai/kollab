# Testing the voice package

Where voice tests run, why the voice runtime venv has no pytest, and how to
run a test under the real runtime interpreter anyway.

## Voice tests run from the engine venv

The repo venv has the full dev toolchain. From the repo root:

```sh
.venv/bin/python -m pytest packages/kollabor-voice/tests -q
```

Tests that exercise the phone-audio bridge and other runtime-facing code
spawn the voice runtime python as a child process; they never need pytest
inside that child (see `packages/kollabor-engine/tests/test_phone_audio_bridge.py`).

## The runtime venv deliberately ships without pytest

`kollabor_voice.bootstrap` builds runtime venvs under
`~/.kollab/voice/runtimes/<hash>/` containing only the pinned runtime
dependencies (faster_whisper, kokoro_onnx, sounddevice, numpy, livekit).
pytest is not installed, and should not be: users run these on real
machines, and shipping a test stack to every voice install is dead weight
(decision on issue #499).

## Escape hatch: pytest against the runtime interpreter

To catch import-order or dependency-version issues under the actual runtime
python, run the tests with a throwaway pytest install on PYTHONPATH. The
runtime venv itself is never modified:

```sh
scripts/test_voice_runtime.sh
```

or manually:

```sh
uv pip install --target /tmp/voice-pytest-escape pytest pytest-asyncio
PYTHONPATH=/tmp/voice-pytest-escape:packages/kollabor-voice/src \
    ~/.kollab/voice/runtimes/<hash>/bin/python -m pytest \
    packages/kollabor-voice/tests -q
```

Notes:

- `uv run --with pytest -- <runtime python>` does NOT work: uv's ephemeral
  package overlay applies to uv-managed interpreters, not an external
  python. Use the `--target` directory above.
- pytest-asyncio is required alongside pytest; the async tests fail with
  "async def functions are not natively supported" without it.
- The runtime pins its own interpreter version (currently 3.12), which may
  differ from the repo floor; the escape hatch is also the cheap way to
  smoke-test against that version.
