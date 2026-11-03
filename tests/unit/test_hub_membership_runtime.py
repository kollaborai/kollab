"""Membership registry per (workspace, network) — issue #158 slice 3.

Contract under test:
- Memberships discovers membership dirs under
  ~/.kollab/workspaces/<digest>/networks/<slug>/ (v2 layout from slice 1).
- With one membership, primary() is that membership and state_dir matches
  the slice-1 resolver — N=1 keeps today's single-membership behavior.
- The device record's primary network wins when it names one of this
  workspace's memberships; otherwise deterministic first slug.
- register() enforces the 8-membership cap and is idempotent per origin.
- resolve() maps None to primary and rejects unknown slugs.
"""

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from plugins.hub.device_record import NETWORKS_DIR_NAME, origin_slug
from plugins.hub.membership_runtime import (
    MAX_MEMBERSHIPS_PER_WORKSPACE,
    Membership,
    MembershipContext,
    Memberships,
)
from plugins.hub.relay_state import RelayError


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _membership_dir(workspace: Path, home: Path, slug: str) -> Path:
    digest = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
    path = home / ".kollab" / "workspaces" / digest / NETWORKS_DIR_NAME / slug
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _seed_state(directory: Path, origin: str):
    payload = {
        "origin": origin,
        "enabled": True,
        "workspace_id": "a" * 32,
        "device_name": "laptop-kollab",
    }
    state = directory / "state.json"
    state.write_text(json.dumps(payload))
    os.chmod(state, 0o600)


def test_empty_workspace_has_no_memberships(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    memberships = Memberships(workspace)
    assert memberships.list() == []
    with pytest.raises(RelayError):
        memberships.primary()


def test_single_membership_is_primary_and_matches_slice1_resolver(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    slug = origin_slug("https://relay.example.com")
    directory = _membership_dir(workspace, home, slug)
    _seed_state(directory, "https://relay.example.com")

    memberships = Memberships(workspace)
    listed = memberships.list()
    assert [m.slug for m in listed] == [slug]
    primary = memberships.primary()
    assert primary.slug == slug
    assert primary.state_dir == directory

    from plugins.hub.relay_state import default_workspace_state_dir

    assert primary.state_dir == default_workspace_state_dir(workspace)


def test_device_primary_wins_when_network_is_a_membership(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    slug_a = origin_slug("https://a.example.com")
    slug_b = origin_slug("https://b.example.com")
    _seed_state(_membership_dir(workspace, home, slug_a), "https://a.example.com")
    _seed_state(_membership_dir(workspace, home, slug_b), "https://b.example.com")

    # device record names b's network primary
    from plugins.hub.device_record import DeviceRecord

    record = DeviceRecord()
    record.add_network("https://b.example.com", "b-net")

    memberships = Memberships(workspace)
    assert memberships.primary().slug == slug_b
    # device primary not among this workspace's memberships -> the
    # deterministic first slug wins (fresh registry re-reads the record)
    class ForeignPrimary:
        primary = origin_slug("https://c.example.com")

    fresh = Memberships(workspace)
    fresh._device_record = ForeignPrimary()  # type: ignore[assignment]
    assert fresh.primary().slug == min(slug_a, slug_b)


def test_register_enforces_cap_and_is_idempotent(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    memberships = Memberships(workspace)
    for i in range(MAX_MEMBERSHIPS_PER_WORKSPACE):
        slug = origin_slug(f"https://net-{i}.example.com")
        _seed_state(_membership_dir(workspace, home, slug), f"https://net-{i}.example.com")
    with pytest.raises(RelayError):
        memberships.register("https://over.example.com")
    # same origin twice -> same membership, no error
    again = Memberships(workspace)
    first = again.register("https://net-0.example.com")
    assert first.slug == origin_slug("https://net-0.example.com")


def test_resolve_maps_none_to_primary_and_rejects_unknown(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    slug = origin_slug("https://relay.example.com")
    _seed_state(_membership_dir(workspace, home, slug), "https://relay.example.com")
    memberships = Memberships(workspace)
    assert memberships.resolve(None).slug == slug
    assert memberships.resolve("").slug == slug
    with pytest.raises(RelayError):
        memberships.resolve("no-such-slug")


def test_unjoined_placeholder_is_not_a_membership(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    _membership_dir(workspace, home, "unjoined")
    memberships = Memberships(workspace)
    assert memberships.list() == []


# -- relay.* network-arg dispatch (slice 3, part 2) -------------------------


class _FakeClient:
    def __init__(self, label: str):
        self.label = label

    def status(self):
        return {"state": "online", "label": self.label}


class _FakeCommands:
    def __init__(self, label: str):
        self.client = _FakeClient(label)


def _bridge_for_dispatch(tmp_path, home, memberships_by_slug):
    """Minimal RelayAgentBridge stand-in exercising _membership_commands."""
    import plugins.hub.relay_agent as relay_agent

    workspace = tmp_path / "project"
    workspace.mkdir(exist_ok=True)
    bridge = relay_agent.RelayAgentBridge.__new__(relay_agent.RelayAgentBridge)
    bridge.workspace = workspace.resolve()
    bridge.owner = relay_agent.WorkspaceRelayOwner(workspace)
    bridge.memberships = Memberships(workspace)
    bridge._membership_runtimes = {}
    # primary runtime is the bridge's own commands facade
    primary = bridge.memberships.primary()
    bridge.commands = _FakeCommands(f"primary:{primary.slug}")
    for slug, label in memberships_by_slug.items():
        if slug != primary.slug:
            bridge._membership_runtimes[slug] = _FakeCommands(label)
    return bridge


def test_membership_commands_resolves_none_to_primary_runtime(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    slug = origin_slug("https://relay.example.com")
    _seed_state(_membership_dir(workspace, home, slug), "https://relay.example.com")
    bridge = _bridge_for_dispatch(tmp_path, home, {})
    import asyncio

    commands = asyncio.run(bridge._membership_commands(None))
    assert commands.client.status()["label"] == f"primary:{slug}"


def test_membership_commands_secondary_builds_and_caches(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    slug_a = origin_slug("https://a.example.com")
    slug_b = origin_slug("https://b.example.com")
    _seed_state(_membership_dir(workspace, home, slug_a), "https://a.example.com")
    _seed_state(_membership_dir(workspace, home, slug_b), "https://b.example.com")
    # the NON-primary slug is the secondary under test
    primary_slug = Memberships(workspace).primary().slug
    secondary = slug_a if primary_slug == slug_b else slug_b
    # seed the cache like an earlier call did
    bridge = _bridge_for_dispatch(tmp_path, home, {secondary: f"secondary:{secondary}"})
    import asyncio

    commands = asyncio.run(bridge._membership_commands(secondary))
    assert commands.client.status()["label"] == f"secondary:{secondary}"
    cached = asyncio.run(bridge._membership_commands(secondary))
    assert cached is commands  # cache hit, no rebuild
    with pytest.raises(RelayError):
        asyncio.run(bridge._membership_commands("unknown-slug"))


# -- slice 3 fan-out: MembershipContext lifecycle + slug-addressed inbound --


class _RecordingCommands:
    def __init__(self):
        self.handlers = []
        self.closed = False

        class _Client:
            def __init__(inner):
                inner.state_dir = Path("/unused")
                inner.handler = None

            def set_request_handler(inner, handler):
                inner.handler = handler

        self.client = _Client()

    async def resume(self):
        self.resumed = True

    async def close(self):
        self.closed = True


class _RecordingTransport:
    def __init__(self):
        self.identity_response_calls = []

    def identity_response(self, peer, payload):
        self.identity_response_calls.append((peer, payload))
        return {"ok": True}


def test_membership_context_registers_inbound_and_closes_cleanly():
    import asyncio

    commands = _RecordingCommands()
    transport = _RecordingTransport()
    inbound_calls = []

    async def inbound(peer, method, payload):
        inbound_calls.append((peer, method, payload))
        return {"echo": True}

    membership = Membership(slug="slug-x", state_dir=Path("/tmp/x"))
    context = MembershipContext(membership, commands, transport, inbound=inbound)

    async def scenario():
        await context.start()
        handler = commands.client.handler
        result = await handler("peerkey", "directory", {})
        await context.close()
        return result

    result = asyncio.run(scenario())
    assert result == {"echo": True} and inbound_calls == [("peerkey", "directory", {})]
    assert commands.closed is True
    assert commands.client.handler is None  # adapter detached


def test_receive_membership_validates_against_its_own_state(tmp_path, home):
    """A secondary's inbound must consult ITS approvals, not the primary's."""
    import asyncio

    workspace = tmp_path / "project"
    workspace.mkdir()
    slug_a = origin_slug("https://a.example.com")
    slug_b = origin_slug("https://b.example.com")
    dir_a = _membership_dir(workspace, home, slug_a)
    dir_b = _membership_dir(workspace, home, slug_b)
    _seed_state(dir_a, "https://a.example.com")
    _seed_state(dir_b, "https://b.example.com")

    import plugins.hub.relay_agent as relay_agent

    bridge = relay_agent.RelayAgentBridge.__new__(relay_agent.RelayAgentBridge)
    bridge.workspace = workspace.resolve()
    bridge.memberships = Memberships(workspace)
    bridge._membership_contexts = {}
    bridge.commands = None  # no primary: proves secondary path is self-contained

    class _State:
        class state:
            links = set()
            approvals = {"peer-of-b": True}

        workspace_id = "b" * 32

    class _Client:
        state_dir = dir_b
        state = _State.state  # approvals contain ONLY b's peer

    class _CommandsB:
        client = _Client()

    context = MembershipContext(
        Membership(slug=slug_b, state_dir=dir_b),
        _CommandsB(),
        _RecordingTransport(),
        inbound=None,
    )
    bridge._membership_contexts[slug_b] = context

    async def scenario(peer):
        return await bridge._receive_membership(slug_b, peer, "directory", {})

    # peer known to b's state: passes the approval gate and proceeds to the
    # secure-session requirement (enforced further down _receive) — proving
    # validation used THIS membership's state, not the (absent) primary's.
    with pytest.raises(RelayError, match="authenticated secure session"):
        asyncio.run(scenario("peer-of-b"))

    # peer unknown to b's state: refused at the membership's own approval gate
    with pytest.raises(RelayError, match="peer is not approved"):
        asyncio.run(scenario("stranger"))


def test_secondary_membership_polls_its_own_enrollment_issuer(home, tmp_path):
    """A secondary command facade must expose its membership's pending joins."""
    from types import SimpleNamespace

    import plugins.hub.relay_agent as relay_agent
    from plugins.hub.relay_commands import RelayCommands

    workspace = tmp_path / "project"
    workspace.mkdir()
    primary_slug = origin_slug("https://a.example.com")
    secondary_slug = origin_slug("https://b.example.com")
    _seed_state(_membership_dir(workspace, home, primary_slug), "https://a.example.com")
    secondary_dir = _membership_dir(workspace, home, secondary_slug)
    _seed_state(secondary_dir, "https://b.example.com")
    membership = Memberships(workspace).get(secondary_slug)
    assert membership is not None

    parent = SimpleNamespace(
        workspace=workspace,
        plugin=SimpleNamespace(),
        identity=SimpleNamespace(agent_id="secondary-agent"),
        _require_human_network_context=lambda _message: None,
    )
    commands = SimpleNamespace()
    bridge = relay_agent._MembershipEnrollmentBridge(parent, membership, commands)
    pending = [SimpleNamespace(enrollment_id="secondary-request")]
    bridge._enrollment_issuer = SimpleNamespace(pending_requests=lambda: pending)

    facade = RelayCommands.__new__(RelayCommands)
    facade.agent_bridge = bridge
    assert facade._try_pending_rows() == pending


@pytest.mark.asyncio
async def test_membership_context_starts_and_closes_enrollment_issuer(home, tmp_path):
    from types import SimpleNamespace

    workspace = tmp_path / "project"
    workspace.mkdir()
    slug = origin_slug("https://issuer.example.com")
    _seed_state(_membership_dir(workspace, home, slug), "https://issuer.example.com")
    membership = Memberships(workspace).primary()
    commands = _RecordingCommands()
    issuer = SimpleNamespace(start_recovery=lambda: setattr(issuer, "started", True))
    issuer.started = False
    issuer.close = AsyncMock()
    context = MembershipContext(
        membership,
        commands,
        _RecordingTransport(),
        inbound=lambda *_args: None,
        enrollment_issuer=issuer,
    )

    await context.start()
    assert issuer.started is True
    await context.close()
    issuer.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_membership_context_start_failure_closes_partial_resources():
    import asyncio

    resume_started = asyncio.Event()

    class _Client:
        def __init__(self):
            self.handler = None

        def set_request_handler(self, handler):
            self.handler = handler

    class _Commands:
        def __init__(self):
            self.client = _Client()
            self.close_calls = 0
            self.resume_task = None

        async def resume(self):
            self.resume_task = asyncio.current_task()
            resume_started.set()
            await asyncio.Event().wait()

        async def close(self):
            self.close_calls += 1

    class _Issuer:
        def __init__(self):
            self.close_calls = 0

        def start_recovery(self):
            raise RuntimeError("issuer startup failed")

        async def close(self):
            self.close_calls += 1

    commands = _Commands()
    issuer = _Issuer()
    context = MembershipContext(
        Membership(slug="secondary", state_dir=Path("/tmp/secondary")),
        commands,
        _RecordingTransport(),
        inbound=lambda *_args: None,
        enrollment_issuer=issuer,
    )

    with pytest.raises(RuntimeError, match="issuer startup failed"):
        await context.start()

    assert resume_started.is_set()
    assert commands.resume_task is not None
    assert commands.resume_task.done() and commands.resume_task.cancelled()
    assert commands.client.handler is None
    assert issuer.close_calls == 1
    assert commands.close_calls == 1

    # Repeated close and a stale start cannot recreate or double-close resources.
    await context.close()
    await context.start()
    assert commands.client.handler is None
    assert issuer.close_calls == 1
    assert commands.close_calls == 1


@pytest.mark.asyncio
async def test_bridge_close_joins_inflight_secondary_membership_start(tmp_path, monkeypatch):
    """Shutdown joins startup cancellation before closing secondary resources."""
    import asyncio
    from types import SimpleNamespace

    import plugins.hub.enrollment_client as enrollment_client
    import plugins.hub.relay_agent as relay_agent

    handler_installed = asyncio.Event()
    resume_started = asyncio.Event()
    startup_started = asyncio.Event()
    release_startup = asyncio.Event()
    startup_cancelled = asyncio.Event()
    release_startup_cleanup = asyncio.Event()
    context_close_entered = asyncio.Event()
    context_close_start_states = []
    release_resume = asyncio.Event()
    close_entered = asyncio.Event()
    release_close = asyncio.Event()
    close_entered_during_startup_cleanup = []
    commands_created = []
    issuers_created = []
    startup_tasks = []
    original_create_task = asyncio.create_task

    def capture_create_task(coro, *, name=None, **kwargs):
        task = original_create_task(coro, name=name, **kwargs)
        if name and name.startswith("kollab-membership-start-"):
            startup_tasks.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", capture_create_task)

    class _Client:
        def __init__(self):
            self._store = SimpleNamespace(key="a" * 32)
            self.request_handler = None

        def set_request_handler(self, handler):
            self.request_handler = handler
            if handler is not None:
                handler_installed.set()

    class _Commands:
        def __init__(self, *_args, **_kwargs):
            self.client = _Client()
            self.agent_bridge = None
            self.close_calls = 0
            commands_created.append(self)

        async def resume(self):
            resume_started.set()
            await release_resume.wait()

        async def close(self):
            self.close_calls += 1
            close_entered.set()
            await release_close.wait()

    class _EnrollmentBridge:
        def __init__(self, *_args, **_kwargs):
            self._enrollment_issuer = None

    class _Issuer:
        def __init__(self, _bridge):
            self.start_calls = 0
            self.close_calls = 0
            self.recovery_task = None
            issuers_created.append(self)

        def start_recovery(self):
            self.start_calls += 1

            async def recover():
                await asyncio.Event().wait()

            self.recovery_task = asyncio.create_task(recover())

        async def close(self):
            self.close_calls += 1
            if self.recovery_task is not None:
                self.recovery_task.cancel()
                await asyncio.gather(self.recovery_task, return_exceptions=True)

    monkeypatch.setattr(relay_agent, "RelayCommands", _Commands)
    monkeypatch.setattr(relay_agent, "_MembershipEnrollmentBridge", _EnrollmentBridge)
    monkeypatch.setattr(relay_agent, "SecureConversationTransport", lambda *_args: object())
    monkeypatch.setattr(enrollment_client, "EnrollmentIssuer", _Issuer)

    original_start = relay_agent.MembershipContext.start

    async def gated_start(context):
        await original_start(context)
        startup_started.set()
        try:
            await release_startup.wait()
        except asyncio.CancelledError:
            startup_cancelled.set()
            await release_startup_cleanup.wait()
            raise

    monkeypatch.setattr(relay_agent.MembershipContext, "start", gated_start)

    original_context_close = relay_agent.MembershipContext.close

    async def observed_context_close(context):
        context_close_start_states.append((startup_tasks[0].done(), startup_tasks[0].cancelled()))
        context_close_entered.set()
        await original_context_close(context)

    monkeypatch.setattr(relay_agent.MembershipContext, "close", observed_context_close)

    primary = SimpleNamespace(slug="primary", state_dir=tmp_path)
    secondary = SimpleNamespace(slug="secondary", state_dir=tmp_path)
    owner_releases = []
    bridge = relay_agent.RelayAgentBridge.__new__(relay_agent.RelayAgentBridge)
    bridge.plugin = SimpleNamespace(config=None)
    bridge.workspace = tmp_path
    bridge.owner = SimpleNamespace(owner=lambda: object(), release=lambda: owner_releases.append(True))
    bridge.memberships = SimpleNamespace(primary=lambda: primary, list=lambda: [primary, secondary])
    bridge._membership_contexts = {}
    bridge._membership_start_tasks = set()
    bridge._closed = False
    bridge.active = None
    bridge._loop_task = None
    bridge._resume_task = None
    bridge._directory_task = None
    bridge._arrivals_task = None
    bridge._enrollment_issuer = None
    bridge.peer_mesh = None
    bridge.config_sync = None
    bridge.membership_sync = None
    bridge.halt_sync = None
    bridge.network_attach = None
    bridge.secure_transport = None
    bridge.commands = None

    bridge._start_secondary_memberships()
    assert len(startup_tasks) == 1
    context = bridge._membership_contexts[secondary.slug]
    commands = commands_created[0]
    issuer = issuers_created[0]

    caller_task = asyncio.current_task()

    async def release_cancelled_startup_cleanup():
        await startup_cancelled.wait()
        # Give close its next turn while startup cleanup remains gated.
        await asyncio.sleep(0)
        close_entered_during_startup_cleanup.append(context_close_entered.is_set())
        release_startup_cleanup.set()

    async def cancel_close_caller_during_context_cleanup():
        await close_entered.wait()
        caller_task.cancel()

    release_startup_task = asyncio.create_task(release_cancelled_startup_cleanup())
    cancel_task = asyncio.create_task(cancel_close_caller_during_context_cleanup())
    try:
        await asyncio.wait_for(
            asyncio.gather(handler_installed.wait(), startup_started.wait(), resume_started.wait()),
            timeout=5,
        )
        assert handler_installed.is_set()
        assert resume_started.is_set()
        assert startup_tasks[0] in bridge._membership_start_tasks
        assert not startup_tasks[0].done()
        assert issuer.start_calls == 1
        assert issuer.recovery_task is not None
        assert not issuer.recovery_task.done()

        # The real start has installed its handler and resume/recovery tasks. Keep
        # its bridge-owned startup task pending until bridge.close cancels it.
        with pytest.raises(asyncio.CancelledError):
            await bridge.close()
        await close_entered.wait()

        # The test controller releases cancellation cleanup, but does not await
        # the startup task. The context-close observation proves bridge close
        # waited for that owned task before entering secondary resource teardown.
        assert close_entered_during_startup_cleanup == [False]
        assert context_close_start_states == [(True, True)]
        assert startup_tasks[0].done() and startup_tasks[0].cancelled()
        assert not bridge._membership_start_tasks
        assert not bridge._close_task.done()
        assert commands.client.request_handler is None
        assert context.resume_task is None or context.resume_task.done()
        assert issuer.recovery_task is None or issuer.recovery_task.done()

        release_close.set()
        await bridge._close_task

        assert context_close_entered.is_set()
        assert commands.client.request_handler is None
        assert handler_installed.is_set()
        assert resume_started.is_set()
        assert issuer.recovery_task is None or issuer.recovery_task.done()
        assert issuer.start_calls == 1
        assert len(startup_tasks) == 1
        assert not bridge._membership_start_tasks
        bridge._start_secondary_memberships()
        assert len(startup_tasks) == 1
        assert bridge._membership_contexts == {}
        assert commands.close_calls == 1
        assert issuer.close_calls == 1
        assert owner_releases == [True]
    finally:
        release_close.set()
        release_resume.set()
        release_startup.set()
        release_startup_cleanup.set()
        for task in startup_tasks:
            if not task.done():
                task.cancel()
        if context.resume_task is not None and not context.resume_task.done():
            context.resume_task.cancel()
        for task in (cancel_task, release_startup_task):
            if not task.done():
                task.cancel()
        pending = list(startup_tasks)
        pending.extend((cancel_task, release_startup_task))
        if bridge._close_task is not None:
            pending.append(bridge._close_task)
        if context.resume_task is not None:
            pending.append(context.resume_task)
        if issuer.recovery_task is not None:
            pending.append(issuer.recovery_task)
        await asyncio.gather(*pending, return_exceptions=True)
        if bridge._close_task is None:
            await context.close()
