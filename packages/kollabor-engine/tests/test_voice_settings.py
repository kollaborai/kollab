"""Voice settings/prompt routes, worker interpreter resolution, thread validation."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from kollabor_engine.routes import voice as voice_routes
from kollabor_engine.voice_control import VoiceControl
from kollabor_engine.voice_livekit import LiveKitRuntime
from kollabor_engine.voice_runtime_host import RuntimeHostError
from kollabor_engine.voice_settings import DEFAULT_PROMPT, VoiceSettingsService
from kollabor_engine.voice_types import SpeechReadiness
from kollabor_voice.client import VoiceClient
from kollabor_voice.control import VoiceError, install_identity, manifest
from test_voice_livekit_runtime import FakeSession

from kollabor_ai.prompt_overrides import PromptOverrideStore


class FakeConfig:
    def __init__(self):
        self.data = {}

    def get(self, path, default=None):
        return self.data.get(path, default)

    def save_key(self, path, value, save_target=None):
        self.data[path] = value
        return True


class FakeSecrets:
    def __init__(self):
        self.keys = {}

    async def get_key(self, name):
        return self.keys.get(name)

    async def store_key(self, name, value):
        self.keys[name] = value

    async def delete_key(self, name):
        self.keys.pop(name, None)


async def _ready():
    return SpeechReadiness(
        stt_state="ready", stt_engine="parakeet", tts_state="ready", tts_engine="vibevoice"
    )


@pytest.fixture
def rig(tmp_path):
    session = FakeSession()
    session.known_thread_ids = {"t1"}
    config, secrets = FakeConfig(), FakeSecrets()
    settings = VoiceSettingsService(
        config=config,
        secrets=secrets,
        profiles=lambda name: SimpleNamespace(name=name, provider="openai", base_url="https://x.test/v1")
        if name == "voice-profile"
        else None,
        prompts=PromptOverrideStore(tmp_path / "prompts"),
        readiness=_ready,
    )
    runtime = LiveKitRuntime(
        engine_url=lambda: "http://127.0.0.1:1", engine_token=lambda: "tok"
    )
    control = VoiceControl(
        runtime=runtime,
        registry=lambda: {"s1": session},
        platform_supported=lambda: True,
        settings=settings,
    )
    app = FastAPI()
    app.state.voice = control
    app.include_router(voice_routes.router)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    return SimpleNamespace(
        http=http, config=config, secrets=secrets, settings=settings, control=control, runtime=runtime
    )


# --- worker interpreter ---


def _prepare_runtime(root):
    pins = manifest()["runtime"]
    import hashlib

    sig = hashlib.sha256(json.dumps(pins).encode()).hexdigest()[:16]
    runtime = root / "runtimes" / sig
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").symlink_to(sys.executable)
    (runtime / "ready.json").write_text(json.dumps({"requirements": pins}))
    return runtime / "bin" / "python"


def test_worker_uses_prepared_voice_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    monkeypatch.delenv("KOLLAB_VOICE_PYTHON", raising=False)
    python = _prepare_runtime(tmp_path)
    runtime = LiveKitRuntime(engine_url=lambda: "", engine_token=lambda: "")
    cmd, env = runtime._worker_command()
    assert cmd == [str(python), "-m", "kollabor_voice.livekit_worker"]
    assert env["PYTHONPATH"].endswith("kollabor-voice/src")


def test_worker_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    monkeypatch.setenv("KOLLAB_VOICE_PYTHON", "/opt/custom/python")
    cmd, _ = LiveKitRuntime(engine_url=lambda: "", engine_token=lambda: "")._worker_command()
    assert cmd[0] == "/opt/custom/python"


async def _start_voice(rig, monkeypatch, *, ensure):
    """Create a voice session; return the first start_session call's worker command."""
    monkeypatch.setattr(rig.runtime, "_binary", lambda: "/usr/local/bin/livekit-server")
    monkeypatch.setattr(VoiceClient, "ensure", ensure)
    started: asyncio.Future = asyncio.get_running_loop().create_future()

    async def start_session(binding):
        started.set_result(rig.runtime._worker_command()[0])
        raise RuntimeHostError("runtime_unavailable", 503, "no room in tests")

    monkeypatch.setattr(rig.runtime, "start_session", start_session)
    res = await rig.http.post("/sessions/s1/voice", json={})
    assert res.status_code == 202 and res.json()["state"] == "starting"
    return await asyncio.wait_for(started, 5)


async def test_first_voice_start_installs_voice_and_shows_each_step(rig, tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    monkeypatch.delenv("KOLLAB_VOICE_PYTHON", raising=False)
    seen, installed = [], []

    async def ensure(client, progress):
        progress({"state": "installing", "detail": "Installing voice support"})
        seen.append(next(iter(rig.control.host._sessions.values())).detail)
        installed.append(_prepare_runtime(tmp_path))  # what the service's setup leaves
        return {"state": "off"}

    cmd = await _start_voice(rig, monkeypatch, ensure=ensure)
    assert seen == ["Installing voice support"]
    assert cmd == [str(installed[0]), "-m", "kollabor_voice.livekit_worker"]


async def test_failed_voice_setup_says_why(rig, tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    monkeypatch.delenv("KOLLAB_VOICE_PYTHON", raising=False)
    monkeypatch.setattr(rig.runtime, "_binary", lambda: "/usr/local/bin/livekit-server")

    async def ensure(client, progress):
        raise VoiceError("setup_failed", "Voice setup failed: uv is not installed.")

    monkeypatch.setattr(VoiceClient, "ensure", ensure)
    res = await rig.http.post("/sessions/s1/voice", json={})
    vid = res.json()["voice_session_id"]
    for _ in range(100):
        view = await rig.control.status(vid)
        if view.state == "error":
            break
        await asyncio.sleep(0.02)
    assert (view.state, view.detail) == ("error", "Voice setup failed: uv is not installed.")


async def test_missing_livekit_server_fails_before_any_setup(rig, tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    monkeypatch.delenv("KOLLAB_VOICE_PYTHON", raising=False)
    monkeypatch.setattr(rig.runtime, "_binary", lambda: None)
    called = []
    monkeypatch.setattr(VoiceClient, "ensure", lambda *a: called.append(a))
    with pytest.raises(RuntimeHostError) as err:
        await rig.runtime.ensure_runtime(lambda detail: None)
    assert "brew install livekit" in err.value.detail and not called


class _Service:
    def __init__(self, status):
        self.status = status

    async def call(self, op):
        assert op == "status"
        return self.status


async def test_an_older_kollab_voice_service_is_replaced():
    service = await asyncio.create_subprocess_exec("sleep", "60")
    await LiveKitRuntime._retire_stale_service(
        _Service({"owner": None, "pid": service.pid}), runtime_ready=False
    )
    assert await asyncio.wait_for(service.wait(), 5) == -signal.SIGTERM


async def test_an_older_kollab_voice_service_in_use_keeps_running():
    service = await asyncio.create_subprocess_exec("sleep", "60")
    try:
        with pytest.raises(RuntimeHostError) as err:
            await LiveKitRuntime._retire_stale_service(
                _Service({"owner": "cli:abc", "pid": service.pid}), runtime_ready=False
            )
        assert err.value.code == "voice_service_busy" and service.returncode is None
    finally:
        service.kill()
        await service.wait()


OTHER_CODE = {"path": "/older/kollab/kollabor_voice", "code": "0123456789abcdef"}


@pytest.mark.parametrize("install", [OTHER_CODE, None], ids=["other code", "says no install"])
async def test_an_idle_voice_service_running_other_code_is_replaced(install):
    """This Kollab's runtime, but the code of the Kollab that started it (0.18.4 had to be stopped by hand)."""
    service = await asyncio.create_subprocess_exec("sleep", "60")
    status = {"owner": None, "pid": service.pid}
    if install:
        status["install"] = install
    await LiveKitRuntime._retire_stale_service(_Service(status), runtime_ready=True)
    assert await asyncio.wait_for(service.wait(), 5) == -signal.SIGTERM


@pytest.mark.parametrize(
    "owner, install",
    [(None, "ours"), ("livekit:voice-in-another-chat", OTHER_CODE)],
    ids=["this code", "in use"],
)
async def test_a_voice_service_with_this_code_or_in_use_keeps_running(owner, install):
    service = await asyncio.create_subprocess_exec("sleep", "60")
    try:
        status = {
            "owner": owner,
            "pid": service.pid,
            "install": install_identity() if install == "ours" else install,
        }
        await LiveKitRuntime._retire_stale_service(_Service(status), runtime_ready=True)
        await asyncio.sleep(0.2)
        assert service.returncode is None
    finally:
        service.kill()
        await service.wait()


async def test_a_voice_start_replaces_a_service_with_other_code_before_ensure(
    rig, tmp_path, monkeypatch
):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    monkeypatch.delenv("KOLLAB_VOICE_PYTHON", raising=False)
    monkeypatch.setattr(rig.runtime, "_binary", lambda: "/usr/local/bin/livekit-server")
    _prepare_runtime(tmp_path)
    service = await asyncio.create_subprocess_exec("sleep", "60")
    status = {"owner": None, "pid": service.pid, "install": OTHER_CODE}

    async def call(client, op):
        return status

    ensured = []

    async def ensure(client, progress):
        try:
            os.kill(service.pid, 0)
            ensured.append("still running")
        except ProcessLookupError:
            ensured.append("gone")  # before ensure() starts this Kollab's
        return {"state": "off"}

    monkeypatch.setattr(VoiceClient, "call", call)
    monkeypatch.setattr(VoiceClient, "ensure", ensure)
    await rig.runtime.ensure_runtime(lambda detail: None)
    assert ensured == ["gone"]
    assert await asyncio.wait_for(service.wait(), 5) == -signal.SIGTERM


# --- thread validation ---


async def test_unknown_thread_is_rejected(rig, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_PYTHON", sys.executable)  # skip runtime check
    res = await rig.http.post("/sessions/s1/voice", json={"selected_thread_id": "nope"})
    assert res.status_code == 409
    assert res.json()["code"] == "target_mismatch"


async def test_known_thread_is_bound(rig, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_PYTHON", sys.executable)
    monkeypatch.setattr(rig.runtime, "start_session", _never_start)
    res = await rig.http.post("/sessions/s1/voice", json={"selected_thread_id": "t1"})
    assert res.status_code == 202
    assert res.json()["thread_id"] == "t1"


async def _never_start(binding):
    import asyncio

    await asyncio.sleep(60)


# --- settings ---


async def test_settings_roundtrip_hides_key(rig):
    got = (await rig.http.get("/voice/settings")).json()
    assert got["reasoning"] == "off" and got["api_key_configured"] is False
    assert got["readiness"]["stt_state"] == "ready"

    res = await rig.http.put(
        "/voice/settings",
        json={
            "base_url": "https://api.example.com/v1",
            "model": "m1",
            "reasoning": "low",
            "api_key_action": "set",
            "api_key_value": "sk-secret-123",
        },
    )
    assert res.status_code == 200
    assert "sk-secret-123" not in res.text
    body = res.json()
    assert (body["model"], body["reasoning"], body["api_key_configured"]) == ("m1", "low", True)
    assert body["llm_is_remote"] is True
    assert rig.secrets.keys == {"voice-livekit": "sk-secret-123"}
    assert "sk-secret-123" not in json.dumps(rig.config.data)

    # keep leaves the key; omitted fields are unchanged
    res = await rig.http.put("/voice/settings", json={"model": "m2"})
    assert res.json()["model"] == "m2" and res.json()["api_key_configured"] is True
    res = await rig.http.put("/voice/settings", json={"api_key_action": "clear"})
    assert res.json()["api_key_configured"] is False and not rig.secrets.keys


async def test_settings_validation_keeps_last_good_and_never_echoes_key(rig):
    await rig.http.put("/voice/settings", json={"model": "good"})
    for bad in (
        {"reasoning": "high"},
        {"base_url": "ftp://x"},
        {"provider_profile_id": "missing"},
        {"api_key_action": "set", "api_key_value": ""},
        {"api_key_action": "keep", "api_key_value": "sk-leak-me"},
        {"api_key_action": "clear", "api_key_value": "sk-leak-me"},
        {"owner_id": "x"},
    ):
        res = await rig.http.put("/voice/settings", json={"model": "bad", **bad})
        assert res.status_code == 400, bad
        assert res.json()["code"] == "invalid_request"
        assert "sk-leak-me" not in res.text
    assert (await rig.http.get("/voice/settings")).json()["model"] == "good"
    assert not rig.secrets.keys


async def test_voice_profile_scopes_to_voice_turns_only(rig):
    await rig.http.put("/voice/settings", json={"provider_profile_id": "voice-profile"})
    target = await rig.control.host._session_resolver.resolve("owner", "s1", None)  # noqa: SLF001
    assert target.voice_provider_profile_id == "voice-profile"
    assert rig.control.router is not None
    # the live chat session keeps its own profile
    assert rig.control._registry()["s1"].profile.name == "p1"  # noqa: SLF001


# --- prompt overlay ---


async def test_prompt_overlay_is_per_agent_and_leaves_chat_prompt_alone(rig, tmp_path):
    got = (await rig.http.get("/voice/settings/prompt?session_id=s1")).json()
    assert got == {"text": DEFAULT_PROMPT, "personality": "max_gen_z"}
    before = rig.settings.prompt_revision("lapis")

    res = await rig.http.put(
        "/voice/settings/prompt?session_id=s1",
        json={"text": "Be brief.", "personality": "professional"},
    )
    assert res.status_code == 200
    assert res.json() == {"text": "Be brief.", "personality": "professional"}
    assert rig.settings.prompt_revision("lapis") != before
    assert "Be brief." in rig.settings.overlay("lapis")
    # another agent and the default scope are untouched
    assert rig.settings.get_prompt("peridot")["text"] == DEFAULT_PROMPT
    # only the voice override tree was written
    written = {p.relative_to(tmp_path / "prompts").parts[0] for p in (tmp_path / "prompts").rglob("*.md")}
    assert written == {"voice"}

    # empty text restores the default
    await rig.http.put(
        "/voice/settings/prompt?session_id=s1", json={"text": "", "personality": "neutral"}
    )
    assert rig.settings.get_prompt("lapis")["text"] == DEFAULT_PROMPT


async def test_an_agent_without_its_own_prompt_uses_the_settings_prompt(rig):
    # Settings -> Voice -> Voice Prompt edits the default scope (#295).
    res = await rig.http.put(
        "/voice/settings/prompt", json={"text": "Say less.", "personality": "neutral"}
    )
    assert res.status_code == 200
    assert rig.settings.get_prompt("peridot")["text"] == "Say less."
    assert rig.settings.overlay("peridot").startswith("Say less.\n")
    # an agent's own prompt still wins; clearing it falls back to Settings
    await rig.http.put(
        "/voice/settings/prompt?session_id=s1",
        json={"text": "Be brief.", "personality": "neutral"},
    )
    assert rig.settings.get_prompt("lapis")["text"] == "Be brief."
    await rig.http.put(
        "/voice/settings/prompt?session_id=s1", json={"text": "", "personality": "neutral"}
    )
    assert rig.settings.get_prompt("lapis")["text"] == "Say less."


async def test_a_refused_personality_save_puts_the_old_prompt_back(rig, monkeypatch):
    await rig.http.put("/voice/settings/prompt", json={"text": "Old.", "personality": "neutral"})
    monkeypatch.setattr(rig.config, "save_key", lambda *_a, **_k: False)
    res = await rig.http.put("/voice/settings/prompt", json={"text": "New.", "personality": "professional"})
    assert res.status_code == 503
    assert rig.settings.get_prompt(None)["text"] == "Old."


async def test_prompt_validation_and_scope_errors(rig):
    big = {"text": "x" * (32 * 1024 + 1), "personality": "neutral"}
    assert (await rig.http.put("/voice/settings/prompt", json=big)).status_code == 400
    bad = {"text": "ok", "personality": "pirate"}
    assert (await rig.http.put("/voice/settings/prompt", json=bad)).status_code == 400
    assert (await rig.http.get("/voice/settings/prompt?session_id=zzz")).status_code == 404
    res = await rig.http.get("/voice/settings/prompt?session_id=s1&thread_id=nope")
    assert res.status_code == 409 and res.json()["code"] == "target_mismatch"
    ok = await rig.http.get("/voice/settings/prompt?session_id=s1&thread_id=t1")
    assert ok.status_code == 200



async def test_a_worker_that_cannot_start_gives_its_reason():
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "print('WORKER_ERROR Choose Parakeet v3 in Settings > Voice.', flush=True)",
        stdout=asyncio.subprocess.PIPE,
    )
    with pytest.raises(RuntimeHostError) as err:
        await LiveKitRuntime._read_ready(worker)
    await worker.wait()
    assert err.value.detail == "Choose Parakeet v3 in Settings > Voice."

async def test_a_worker_out_of_memory_reports_a_resource_shortage():
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "print('WORKER_SHORTAGE MemoryError: VibeVoice', flush=True)",
        stdout=asyncio.subprocess.PIPE,
    )
    with pytest.raises(RuntimeHostError) as err:
        await LiveKitRuntime._read_ready(worker)
    await worker.wait()
    assert (err.value.code, err.value.detail, err.value.retryable) == (
        "resource_shortage",
        "MemoryError: VibeVoice",
        True,
    )
