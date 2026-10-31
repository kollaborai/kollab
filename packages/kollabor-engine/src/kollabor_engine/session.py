"""EngineSession - a thin proxy over one headless kollab daemon.

This used to be a second, smaller implementation of a conversation: its own
services, its own history list, and ``turn_runner.py`` running its own turn
loop. It drifted from the terminal client and shipped without plugins, the XML
tag pipeline, the vault, compaction, or a conversation log.

Now a session owns a ``kollab --detached`` daemon - the same process the
terminal client forks - and this class is the adapter between the engine's HTTP
surface and that daemon:

  * reads and writes go out as ``state.*`` RPC calls
  * ``send_message`` submits a turn and returns immediately
  * the daemon's structured display events are relayed as SSE

Everything the terminal client can do, a web session can now do, because it is
the same process doing it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from kollabor.state.snapshots import ProfileSnapshot
from kollabor_agent.voice_admission import validate_voice_admission_context
from kollabor_ai.model_registry import supports_vision
from kollabor_ai.session_naming import session_display_name
from kollabor_config.config_utils import get_conversations_dir
from kollabor_events.permissions_models import ApprovalMode

from .daemon_pool import DaemonHandle, get_daemon_pool
from .history_provenance import mark_system_initialization
from .history_store import anchor_digest, read_history_from_disk_async
from .history_xml_tools import web_history

logger = logging.getLogger(__name__)

PERMISSION_RESPONSE_RPC_METHOD = "permission.respond"

# The profile a POST /sessions builds from request credentials. No saved
# profile has this name, so the daemon builds it from KOLLAB_APP_INLINE_* vars
# (ProfileManager._try_create_profile_from_env).
INLINE_PROFILE = "app-inline"
# Opening a busy agent must return its local history mirror promptly. A daemon
# RPC can be queued behind a long-running tool, so this is deliberately shorter
# than the normal RPC timeout and falls back to the cached snapshot.
HISTORY_REFRESH_TIMEOUT_SECONDS = 0.75
ACP_STARTUP_TIMEOUT_SECONDS = 45.0


def inline_profile_env(profile: Any) -> Dict[str, str]:
    """The env vars that carry an inline profile to its daemon; {} for a saved one."""
    if getattr(profile, "name", None) != INLINE_PROFILE:
        return {}
    fields = {
        "PROVIDER": profile.provider,
        "MODEL": profile.model,
        "API_KEY": profile.api_key,
        "BASE_URL": profile.base_url,
        "MAX_TOKENS": profile.max_tokens,
        "STREAMING": profile.streaming,
        "SUPPORTS_TOOLS": profile.supports_tools,
    }
    prefix = "KOLLAB_" + INLINE_PROFILE.replace("-", "_").upper() + "_"
    return {
        prefix + key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in fields.items()
        if value not in (None, "")
    }


_APPROVAL_MODE_MAP = {
    "confirm_all": ApprovalMode.CONFIRM_ALL,
    "default": ApprovalMode.DEFAULT,
    "auto_approve_edits": ApprovalMode.AUTO_APPROVE_EDITS,
    "trust_all": ApprovalMode.TRUST_ALL,
}


# HTTP decision+scope -> the ConfirmationResponse name the daemon expects.
_APPROVE_SCOPE_RESPONSES = {
    "once": "APPROVE_ONCE",
    "session": "APPROVE_SESSION",
    "project": "APPROVE_PROJECT",
    "always_edits": "APPROVE_ALWAYS",
    "trust_tool": "APPROVE_TOOL_ALWAYS",
}


def _confirmation_response_name(decision: str, scope: str) -> str:
    """Map an API decision+scope onto a ConfirmationResponse member name.

    Anything unrecognized denies: an unknown scope must never widen access.
    """
    if decision != "approve":
        return "DENY"
    return _APPROVE_SCOPE_RESPONSES.get(scope, "APPROVE_ONCE")


def _permission_input_payload(tool_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the user-visible permission input payload."""
    explicit_input = tool_data.get("input")
    if isinstance(explicit_input, dict) and explicit_input:
        return explicit_input

    arguments = tool_data.get("arguments")
    if isinstance(arguments, dict) and arguments:
        return arguments

    if tool_data.get("type") == "terminal":
        payload: Dict[str, Any] = {}
        for key in (
            "command",
            "cwd",
            "background",
            "timeout",
            "session_name",
            "lines",
        ):
            value = tool_data.get(key)
            if value not in (None, ""):
                payload[key] = value
        return payload

    return {}


class EngineSession:
    """One conversation, backed by a headless kollab daemon."""

    def __init__(
        self,
        session_id: str,
        profile: Any,
        approval_mode: str = "trust_all",
        workspace: Optional[str] = None,
        system_prompt: Optional[str] = None,
        mcp_server_names: Optional[List[str]] = None,
        user_token: Optional[str] = None,
        agent: Optional[str] = None,
        identity: Optional[str] = None,
        solo: bool = False,
        stop_on_owner_exit: bool = False,
        launch_brief: Optional[str] = None,
        webui_owned: bool = False,
    ):
        self.session_id = session_id
        self.launch_brief = launch_brief or ""
        self.solo = solo
        self.stop_on_owner_exit = stop_on_owner_exit
        # Only sessions created through the WebUI are detached and reowned
        # across engine shutdown. CLI-created sessions keep the old stop policy.
        self.webui_owned = bool(webui_owned)

        self.on_persistence_changed: Optional[Callable[["EngineSession"], None]] = None
        self.user_token = user_token
        self.workspace = self._resolve_workspace(workspace)
        self.system_prompt = system_prompt or ""
        # timezone-aware now(): datetime.utcnow() is deprecated in Python 3.12+
        # and removed-planned for future versions (launch runtime is 3.14).
        self.created_at = datetime.now(timezone.utc)
        self.profile = profile
        self._profile_revision = 0
        self.agent = agent
        self.requested_identity = identity
        self.approval_mode = approval_mode
        # Actionable warnings for facts that could not be read or saved.
        # They never carry private transport or filesystem details, and None
        # means no known problem.
        self.approval_mode_warning: Optional[str] = None
        self.persistence_warning: Optional[str] = None
        # Delete stopped the daemon but its durable owner record is still
        # present: the row stays listed and a retried Delete clears it
        # without stopping anything a second time.
        self.deletion_pending = False
        self.deletion_warning: Optional[str] = None
        self.mcp_server_names = mcp_server_names or []
        # Started outside this engine (a terminal, another engine) and attached
        # by adopt(): shutdown detaches. display_name is its own conversation's
        # name, kept current by list_sessions.
        self.external = False
        self.display_name = ""
        # The network name of the computer an agent runs on, when it is not this
        # one (server.open_named_session of an agent@device). Empty here.
        self.device = ""

        self.daemon: Optional[DaemonHandle] = None
        self._borrowed_daemon = False

        # Mirror of the daemon's conversation. The daemon is the source of
        # truth; this is refreshed on demand so synchronous readers (to_dict,
        # the history route) don't have to await mid-render.
        self.history: List[Dict[str, Any]] = []
        # Thread ids a message was sent on; a voice room may bind only one of these.
        self.known_thread_ids: set[str] = set()
        # The daemon's messages as sent, and the digest it gave for them: a
        # refresh asks for only what follows (an agent on another computer
        # would otherwise send its whole history over the network every turn).
        self._raw_history: List[Dict[str, Any]] = []
        self._history_anchor = ""
        self._history_conversation_uid: str | None = None
        self._history_authoritative = False
        self._history_lock = asyncio.Lock()
        # The mirror holds a disk seed the daemon has not replaced yet. A seed
        # is no prefix of the daemon's history (hub messages are logged apart),
        # so a page showing it replaces its thread whole when the daemon answers.
        self._history_seeded = False

        # The daemon's own conversation id (its conversations/<id>.jsonl
        # name). Arrives with the first answered snapshot; before that, disk
        # seeding falls back to the newest log in the workspace's folder.
        self.conversation_id = ""
        # The coalesced background refresh (get_history_nonblocking, daemon
        # events). One task at a time; a request that arrives while one runs
        # makes it go once more, because the one in flight may have asked the
        # daemon before the change behind the request.
        self._history_refresh_task: Optional[asyncio.Task] = None
        self._history_refresh_again = False
        # Whether the refresh ahead announces what it changed (history_updated).
        self._history_refresh_notify = False
        # True while a refresh is in flight, for the UI's syncing badge.
        self.history_syncing = False

        self.total_turns = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        # Why the last turn failed, if it did: the terminal prints it once,
        # but the conversation keeps no trace, so a reloaded page asks here.
        # ``history_length`` places it after that turn's messages.
        self.last_turn_error: Optional[Dict[str, Any]] = None
        self.acp_waiting = False
        self.acp_plan: Dict[str, Any] = {}
        self.acp_usage: Dict[str, Any] = {}

        self._pending_permissions: Dict[str, Dict[str, Any]] = {}
        self._active_turn_task: Optional[asyncio.Task] = None
        self._event_task: Optional[asyncio.Task] = None
        self._facts_task: Optional[asyncio.Task] = None

    @staticmethod
    def _resolve_workspace(workspace: Optional[str]) -> str:
        """Normalize and validate the daemon's working directory.

        With no folder named the daemon runs in this engine's own, so that is
        the folder the session reports: a null left every reader to guess it.
        """
        path = Path(workspace or Path.cwd()).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"Workspace does not exist: {workspace}")
        if not path.is_dir():
            raise ValueError(f"Workspace is not a directory: {workspace}")
        return str(path)

    # === lifecycle ===

    def _notify_persistence_changed(self) -> None:
        """Persist changed public session metadata without interrupting chat."""
        callback = getattr(self, "on_persistence_changed", None)
        if callback is None:
            return
        try:
            callback(self)
        except Exception:
            logger.exception("could not update persisted metadata for session %s", self.session_id)
            # Report the failure, never the underlying path or transport error.
            self.persistence_warning = (
                "This agent's saved details could not be updated. It keeps "
                "running, but an engine restart may restore older details. "
                "The next successful save clears this."
            )
        else:
            self.persistence_warning = None


    async def initialize(
        self,
        on_spawned: Optional[Callable[["EngineSession"], Awaitable[None]]] = None,
    ) -> None:
        """Spawn the daemon, persist its owner record, then initialize it.

        ``on_spawned`` is awaited immediately after attach, before any profile,
        approval, or history RPC can block. WebUI persistence therefore exists
        as soon as the daemon PID and socket are known.
        """
        profile_name = getattr(self.profile, "name", None) or str(self.profile or "")
        is_acp = str(getattr(self.profile, "provider", "")).lower() == "acp"
        if is_acp:
            from kollabor.llm.acp_bridge import ACPProfile

            # Fail with installation guidance before creating a Hub identity.
            ACPProfile.from_dict(profile_name, {
                "command": getattr(self.profile, "command", None) or None,
                "cwd": getattr(self.profile, "cwd", None) or self.workspace,
                "env": getattr(self.profile, "env", None) or {},
                "pass_env": getattr(self.profile, "pass_env", None) or (),
            }).resolve()
        self.daemon = await get_daemon_pool().spawn(
            self.session_id,
            profile=profile_name or None,
            agent=self.agent,
            identity=self.requested_identity,
            workspace=self.workspace,
            system_prompt=self.system_prompt or None,
            launch_brief=self.launch_brief or None,
            user_token=self.user_token,
            solo=self.solo,
            profile_env=inline_profile_env(self.profile),
            stop_on_owner_exit=self.stop_on_owner_exit,
            webui_owned=self.webui_owned,
        )
        if on_spawned is not None:
            await on_spawned(self)
        if isinstance(self.profile, str):
            # Inbound clients pass a portable profile name. The daemon loads
            # it in the requested workspace, whose config can differ from the
            # engine's cwd; read that authoritative provider before admission.
            self.profile = await self.state.get_active_profile()
            is_acp = str(getattr(self.profile, "provider", "")).lower() == "acp"
        if is_acp:
            await self._await_acp_startup()
            # The harness advertises image support only after initialize.
            # Read the daemon's negotiated value, not the profile's model guess.
            try:
                self.profile = await self.state.get_active_profile()
            except Exception as exc:
                logger.warning("session %s: could not read ACP profile (%s)", self.session_id, type(exc).__name__)

        if self.approval_mode:
            try:
                await self.state.set_approval_mode(self.approval_mode)
            except Exception as e:
                logger.warning(
                    "session %s: could not set approval mode %s: %s",
                    self.session_id,
                    self.approval_mode,
                    e,
                )

        # Session-scoped MCP servers: the request asked for these, so the
        # daemon must actually run them. Without this, mcp_server_names was
        # stored and reported but never applied -- every session came up with
        # whatever the daemon's global settings happened to enable.
        if self.mcp_server_names:
            try:
                await self._apply_mcp_servers()
            except Exception as e:
                logger.warning(
                    "session %s: could not apply MCP servers %s: %s",
                    self.session_id,
                    self.mcp_server_names,
                    e,
                )

        self._event_task = asyncio.create_task(
            self._track_events(), name=f"session-track-{self.session_id}"
        )
        await self.refresh_history()

    async def _await_acp_startup(self) -> None:
        """A connected daemon is usable only after its adapter has started."""
        try:
            async with asyncio.timeout(ACP_STARTUP_TIMEOUT_SECONDS):
                while True:
                    if not self.alive:
                        raise RuntimeError("ACP daemon closed before startup completed")
                    info = await self.state.get_system_info()
                    status = getattr(info, "startup_state", "")
                    if status == "failed":
                        raise RuntimeError(info.startup_error or "ACP startup failed")
                    if status == "ready":
                        return
                    await asyncio.sleep(0.05)
        except TimeoutError as exc:
            raise TimeoutError(
                f"ACP adapter did not become ready within {ACP_STARTUP_TIMEOUT_SECONDS:g} seconds"
            ) from exc
        except Exception as exc:
            startup_error = getattr(self.daemon, "startup_error", "")
            if startup_error:
                raise RuntimeError(startup_error) from exc
            raise

    @classmethod
    async def adopt(
        cls,
        found: Dict[str, Any],
        *,
        owned: bool = False,
        webui_owned: bool = False,
        created_at: Optional[datetime] = None,
    ) -> "EngineSession":
        """Attach to a discoverable or manifest-reowned local agent.

        Ordinary discovery remains external and detach-only. A WebUI manifest
        may explicitly transfer process ownership after the prior engine exits;
        profile and approval settings are still read, never mutated.
        """
        session_id = str(found["session_id"])
        # What presence says it runs: the row keeps its model while a busy
        # daemon is slow to answer _read_agent_facts, which then replaces this.
        seed = ProfileSnapshot(
            name=str(found.get("profile") or ""),
            model=str(found.get("model") or ""),
            provider=str(found.get("provider") or ""),
            description=str(found.get("harness") or ""),
        )
        session = cls(
            session_id=session_id,
            profile=seed if seed.name or seed.model else None,
            approval_mode="",
            agent=str(found.get("agent") or "") or None,
            identity=str(found.get("identity") or "") or None,
        )
        session.external = not webui_owned
        session.webui_owned = bool(webui_owned)
        session.display_name = str(found.get("name") or "")
        session.device = str(found.get("device") or "")
        discovered_workspace = str(found.get("workspace") or "") or None
        # Keep the discovered path for remote agents (it is meaningful on their
        # host even when it is not mounted locally); session_log validation
        # below prevents using an unrelated local cache.
        session.workspace = discovered_workspace
        # Hub discovery is the authority for an adopted conversation.  Accept
        # a session_log only when it is an existing file under this workspace's
        # project data directory; never guess by newest mtime.
        session_log = str(found.get("session_log") or "")
        if session_log and not session.device:
            log_path = Path(session_log).expanduser()
            try:
                conversations_dir = Path(session._disk_conversation_dir() or "").resolve()
                if log_path.is_file() and conversations_dir in log_path.resolve().parents:
                    session.conversation_id = log_path.stem
            except OSError:
                pass
        if created_at is not None:
            session.created_at = (
                created_at.replace(tzinfo=timezone.utc)
                if created_at.tzinfo is None
                else created_at.astimezone(timezone.utc)
            )
        elif found.get("created_at"):
            # keep it aware: created_at is timezone-aware since the 3.14
            # launch-runtime change (datetime.now(timezone.utc) above), and a
            # naive/aware mix would break any comparison or subtraction.
            session.created_at = datetime.fromtimestamp(
                float(found["created_at"]), timezone.utc
            )
        if not session.conversation_id:
            # The per-port manifest stores only the opaque log identifier, not
            # history contents. Restrict it to a filename component before it
            # is used to resolve a path below the workspace conversations dir.
            conversation_id = str(found.get("conversation_id") or "")
            if (
                conversation_id
                and Path(conversation_id).name == conversation_id
                and conversation_id not in {".", ".."}
            ):
                session.conversation_id = conversation_id
        try:
            session.daemon = await get_daemon_pool().adopt(
                session_id, found, owned=owned, webui_owned=webui_owned
            )
            # An existing pool-owned handle belongs to its original session;
            # this attached view only owns its own subscriptions/tasks.
            session._borrowed_daemon = session.daemon.launcher is not None
            # Read in the background so opening a busy agent stays prompt.
            session._facts_task = asyncio.create_task(
                session._read_agent_facts(), name=f"session-facts-{session_id}"
            )
            session._event_task = asyncio.create_task(
                session._track_events(), name=f"session-track-{session_id}"
            )
            # Adoption must not wait for a busy or silent daemon.  The first
            # history response is served by the mirror/disk path; reconciliation
            # runs after the HTTP open has returned.
            session._schedule_refresh()
        except BaseException:
            if session.daemon is not None:
                try:
                    await session.shutdown(stop_daemon=not webui_owned)
                except BaseException as cleanup_error:
                    logger.warning("adopted session cleanup failed (%s)", type(cleanup_error).__name__)
            raise
        return session

    async def _read_agent_facts(self) -> None:
        """An adopted agent's profile, approval mode and, on another computer, folder."""
        # Each fact is read independently: one failed read reports a warning
        # for that fact only, and the others still land. The daemon's private
        # error details stay in the log, never in a warning string.
        try:
            revision = self._profile_revision
            profile = await self.state.get_active_profile()
            # A profile-change event can arrive while this initial RPC waits.
            if revision == self._profile_revision:
                self.profile = profile

                self._notify_persistence_changed()
        except Exception as e:
            logger.debug(
                "session %s: could not read its profile: %s", self.session_id, e
            )
        try:
            mode = (await self.state.get_permission_state()).approval_mode
            self.approval_mode = str(mode or "").lower()
            self.approval_mode_warning = None
        except Exception as e:
            self.approval_mode_warning = (
                "This agent's approval mode could not be read, so it shows as "
                "unknown. Open the agent again or send it a message to refresh it."
            )
            logger.debug(
                "session %s: could not read its approval mode: %s", self.session_id, e
            )
        if self.device:
            try:
                # Its folder is on that computer: what its own daemon reports.
                self.workspace = (await self.state.get_system_info()).cwd or None
            except Exception as e:
                logger.debug(
                    "session %s: could not read its folder: %s", self.session_id, e
                )

    async def shutdown(self, *, stop_daemon: bool = True) -> None:
        """Close session tasks and either stop or detach the daemon.

        Delete and `/quit` keep the default stop behavior. The engine lifespan
        passes ``False`` only for explicitly WebUI-owned persistent sessions.
        """
        tasks = (
            self._facts_task,
            self._event_task,
            self._history_refresh_task,
            self._active_turn_task,
        )
        for task in tasks:
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._facts_task = None
        self._event_task = None
        self._history_refresh_task = None
        self._active_turn_task = None
        if not self._borrowed_daemon:
            pool = get_daemon_pool()
            if stop_daemon:
                await pool.stop(self.session_id)
            else:
                await pool.detach(self.session_id)
        self.daemon = None

    # === daemon access ===

    @property
    def state(self) -> Any:
        """The daemon's StateService (the 41 ``state.*`` RPC methods)."""
        if self.daemon is None or self.daemon.state is None:
            raise RuntimeError(f"session {self.session_id} has no live daemon")
        return self.daemon.state

    @property
    def alive(self) -> bool:
        return self.daemon is not None and self.daemon.alive

    def subscribe(self) -> asyncio.Queue:
        if self.daemon is None:
            raise RuntimeError(f"session {self.session_id} has no live daemon")
        return self.daemon.subscribe()

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if self.daemon is not None:
            self.daemon.unsubscribe(queue)

    # === conversation ===

    async def _apply_mcp_servers(self) -> None:
        """Enable the session's requested MCP servers on the live daemon.

        Mirrors the /sessions/{id}/mcp/{name}/connect route: enable each
        server in the daemon's settings, then one hot-reload reconnects the
        whole set. Unknown server names are surfaced in a warning instead of
        failing session creation -- a bad name should degrade the tool set,
        not take the session down.
        """
        for server_name in self.mcp_server_names:
            try:
                await self.state.enable_mcp_server(server_name)
            except ValueError as e:
                logger.warning(
                    "session %s: requested MCP server %r unavailable: %s",
                    self.session_id,
                    server_name,
                    e,
                )
        await self.state.reload_mcp_servers()

    async def refresh_history(self, notify: bool = False) -> List[Dict[str, Any]]:
        """Pull the daemon's conversation into the local mirror.

        Asks for only the messages past the ones already held, with the digest
        the daemon gave for them; a daemon whose history changed (compaction,
        /clear, /resume), or one too old to know, sends all of it.

        Subscribers hear ``history_updated`` when the answer replaces a disk
        seed (whoever asked) or, with ``notify``, when it changed anything.
        """
        async with self._history_lock:
            held = self._raw_history
            before = anchor_digest(held)
            previous_uid = getattr(self, "_history_conversation_uid", None)
            try:
                kwargs = {"since": len(held) or None, "anchor": self._history_anchor or None}
                if previous_uid:
                    kwargs["expected_conversation_uid"] = previous_uid
                snapshot = await asyncio.wait_for(
                    self.state.get_conversation(**kwargs),
                    timeout=HISTORY_REFRESH_TIMEOUT_SECONDS,
                )
                metadata = (
                    snapshot.get("metadata", {}) if isinstance(snapshot, dict)
                    else getattr(snapshot, "metadata", {})
                )
                current_uid = metadata.get("conversation_uid") if isinstance(metadata, dict) else None
                # An older daemon may ignore the expected uid and send a tail
                # from a different conversation. Ask again for a full snapshot.
                since = snapshot.get("since") if isinstance(snapshot, dict) else getattr(snapshot, "since", None)
                if previous_uid and current_uid and previous_uid != current_uid and since is not None:
                    snapshot = await asyncio.wait_for(
                        self.state.get_conversation(), timeout=HISTORY_REFRESH_TIMEOUT_SECONDS,
                    )
                    metadata = (
                        snapshot.get("metadata", {}) if isinstance(snapshot, dict)
                        else getattr(snapshot, "metadata", {})
                    )
                    current_uid = metadata.get("conversation_uid") if isinstance(metadata, dict) else None
            except asyncio.TimeoutError:
                logger.debug(
                    "session %s history refresh timed out; using cached history",
                    self.session_id,
                )
                return self.history
            except Exception as e:
                logger.debug(
                    "session %s history refresh failed: %s", self.session_id, e
                )
                return self.history

            messages = getattr(snapshot, "messages", None)
            if messages is None and isinstance(snapshot, dict):
                messages = snapshot.get("messages", [])

            # StateService returns MessageDto snapshots with timestamps, metadata,
            # and thinking. Keep the complete wire shape in the engine mirror;
            # reducing messages to role/content here hides native tool-call IDs
            # from both the trajectory projector and the restored chat runtime.
            received: List[Dict[str, Any]] = []
            for message in messages or []:
                if isinstance(message, dict):
                    received.append(message)
                    continue
                to_dict = getattr(message, "to_dict", None)
                if callable(to_dict):
                    received.append(to_dict())
                    continue
                received.append(
                    {
                        "role": getattr(message, "role", ""),
                        "content": getattr(message, "content", "") or "",
                        "timestamp": str(getattr(message, "timestamp", "") or ""),
                        "metadata": dict(getattr(message, "metadata", None) or {}),
                        "thinking": getattr(message, "thinking", None),
                    }
                )
            since = getattr(snapshot, "since", None)
            if since is not None and since > len(held):
                # A tail past what is held cannot be joined; the next refresh
                # sends no anchor and gets all of it.
                self._history_anchor = ""
                return self.history
            self._raw_history = (held[:since] if since else []) + received
            self._history_anchor = getattr(snapshot, "anchor", "") or ""
            uid_changed = bool(previous_uid and current_uid and previous_uid != current_uid)
            self._history_conversation_uid = current_uid if isinstance(current_uid, str) and current_uid else None
            self._history_authoritative = True
            display_history = self._raw_history
            native_session_id = (
                snapshot.get("session_id", "")
                if isinstance(snapshot, dict)
                else getattr(snapshot, "session_id", "")
            ) or ""
            if native_session_id and not getattr(self, "conversation_id", ""):
                self.conversation_id = native_session_id

                self._notify_persistence_changed()
            if native_session_id and display_history and display_history[0].get("role") == "user":
                # Old resumed bases lost initialization metadata. Recover it
                # from typed source records for display only; the daemon and
                # raw mirror/anchor must remain byte-for-byte unchanged.
                try:
                    workspace = getattr(self, "workspace", None)
                    first = await asyncio.to_thread(
                        mark_system_initialization,
                        get_conversations_dir(Path(workspace) if workspace else None),
                        native_session_id,
                        display_history[0],
                    )
                    if first is not display_history[0]:
                        display_history = [first, *display_history[1:]]
                except Exception as exc:
                    logger.debug("session %s display provenance unavailable: %s", self.session_id, exc)
            # XML tool turns in the shape the web renders for native tools.
            self.history = web_history(display_history)
            seed_replaced, self._history_seeded = self._history_seeded, False
            if uid_changed or seed_replaced or (notify and anchor_digest(self._raw_history) != before):
                event = {"type": "history_updated"}
                if self._history_conversation_uid:
                    event["conversation_uid"] = self._history_conversation_uid
                self._emit_event(event)
            return self.history

    # --- nonblocking open path -------------------------------------------

    def _disk_conversation_dir(self) -> Optional[Path]:
        """Where this session's daemon writes its conversation, when known.

        A daemon on another computer keeps its history there: disk seeding is
        for this machine's agents only.
        """
        if self.device or not self.workspace:
            return None
        from kollabor_config.config_utils import get_project_data_dir

        return Path(get_project_data_dir(Path(self.workspace))) / "conversations"

    def _disk_seed_id(self, conversations_dir: Path) -> str:
        """Return only a validated daemon-provided conversation id.

        Never infer ownership from file mtime: a workspace can contain several
        resumed agents and the newest log is not necessarily this session.
        Discovery/adoption supplies the id from its validated ``session_log``;
        otherwise the mirror waits for the daemon's first snapshot.
        """
        if not self.conversation_id:
            return ""
        candidate = conversations_dir / f"{self.conversation_id}.jsonl"
        snapshot = conversations_dir / "snapshots" / f"{self.conversation_id}_snapshot.json"
        return self.conversation_id if candidate.exists() or snapshot.exists() else ""

    async def get_history_nonblocking(
        self, limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """The conversation without waiting on the daemon.

        The warm mirror answers at once; a cold one is seeded from this
        machine's disk. A background refresh (coalesced: one task, however
        many requests arrive) reconciles with the daemon afterward, and the
        ``history_updated`` event it emits when it lands brings late
        replies to the page instead of throwing them away. Until it lands the
        answer says ``stale``: the page replaces what it built from the seed.
        """
        if not self.history and not getattr(self, "_history_authoritative", False):
            conversations_dir = self._disk_conversation_dir()
            if conversations_dir is not None:
                conversation_id = self._disk_seed_id(conversations_dir)
                if conversation_id:
                    seeded = await read_history_from_disk_async(
                        conversations_dir, conversation_id, limit
                    )
                    # The daemon may have answered while the disk was read.
                    if seeded and not self.history:
                        self.history = seeded
                        self._history_anchor = (
                            ""  # seed is not anchor-safe: refetch whole
                        )
                        self._history_seeded = True
        history = self.history[-limit:] if limit else self.history
        self._schedule_refresh()
        turn_error = dict(self.last_turn_error) if self.last_turn_error else None
        if turn_error and limit and history is not self.history:
            turn_error = {
                **turn_error,
                "history_length": max(
                    0, turn_error["history_length"] - max(0, len(self.history) - limit)
                ),
            }
        return {
            "session_id": self.session_id,
            "history": history,
            "conversation_uid": getattr(self, "_history_conversation_uid", None),
            "last_turn_error": turn_error,
            "source": "disk" if self._history_seeded else "mirror",
            "stale": self._history_seeded,
        }

    def _schedule_refresh(self, notify: bool = True) -> None:
        """One background refresh at a time, run again for a request made during it.

        ``notify``: announce what the refresh changed to the open windows
        (history_updated). Keeping the mirror warm needs no announcement.
        """
        if not self.alive:
            return
        self._history_refresh_notify = self._history_refresh_notify or notify
        if (
            self._history_refresh_task is not None
            and not self._history_refresh_task.done()
        ):
            self._history_refresh_again = True
            return
        self.history_syncing = True

        async def _run() -> None:
            try:
                while True:
                    self._history_refresh_again = False
                    announce, self._history_refresh_notify = (
                        self._history_refresh_notify,
                        False,
                    )
                    await self.refresh_history(notify=announce)
                    if not self._history_refresh_again:
                        break
            except Exception as e:  # a background refresh never breaks a route
                logger.debug(
                    "session %s background refresh failed: %s", self.session_id, e
                )
            finally:
                self.history_syncing = False
                self._history_refresh_task = None

        self._history_refresh_task = asyncio.get_running_loop().create_task(
            _run(), name=f"session-history-{self.session_id}"
        )

    def _emit_event(self, event: Dict[str, Any]) -> None:
        """Fan an engine-generated event out to this session's subscribers."""
        if self.daemon is None:
            return
        self.daemon.fan_out(event)

    async def send_message(
        self,
        content: Any,
        *,
        request_id: str | None = None,
        thread_id: str | None = None,
        origin_context: Dict[str, Any] | None = None,
        private_goal_reply: bool = False,
        private_goal_reply_id: str | None = None,
        goal_conversation_uid: str | None = None,
    ) -> Dict[str, Any]:
        """Submit a user turn. Returns once accepted, not once complete."""
        if private_goal_reply:
            # Check before sending: an older daemon would otherwise accept the
            # command and publish its result on the shared event stream.
            if self.daemon is None or self.daemon.rpc is None:
                return {"accepted": False, "reason": "daemon does not support private goal replies"}
            try:
                capability = await self.daemon.rpc.call("state.goal_reply_capabilities", {})
            except Exception:
                return {"accepted": False, "reason": "daemon does not support private goal replies"}
            if not isinstance(capability, dict) or capability.get("private_goal_reply") is not True:
                return {"accepted": False, "reason": "daemon does not support private goal replies"}
        voice_turn = (
            isinstance(origin_context, dict)
            and origin_context.get("source") == "livekit_voice"
        )
        if voice_turn:
            # The capability was issued in this process; the daemon checks its own,
            # so mirror it there, but only while this process still holds it.
            if not validate_voice_admission_context(origin_context):
                return {"accepted": False, "reason": "invalid voice admission capability"}
            admitted = await self.voice_admission("admit", context=origin_context)
            if admitted.get("admitted") is not True:
                return {"accepted": False, "reason": "invalid voice admission capability"}
        try:
            kwargs = {"request_id": request_id, "thread_id": thread_id, "origin_context": origin_context}
            if private_goal_reply:
                kwargs["private_goal_reply"] = True
                kwargs["private_goal_reply_id"] = private_goal_reply_id
                kwargs["goal_conversation_uid"] = goal_conversation_uid
            result = await self.state.send_message(content, **kwargs)
        except BaseException:
            if voice_turn:
                await self.voice_admission("revoke", context=origin_context)
            raise
        if voice_turn and not (isinstance(result, dict) and result.get("accepted")):
            await self.voice_admission("revoke", context=origin_context)
        if thread_id and isinstance(result, dict) and result.get("accepted"):
            self.__dict__.setdefault("known_thread_ids", set()).add(thread_id)
        return result

    async def poll_goal_reply(self, reply_id: str) -> Dict[str, Any]:
        """Poll one private goal reply without blocking the daemon RPC loop."""
        if self.daemon is None or self.daemon.rpc is None:
            return {"status": "missing", "message": "goal reply unavailable"}
        result = await self.daemon.rpc.call("state.poll_goal_reply", {"reply_id": reply_id})
        return result if isinstance(result, dict) else {"status": "missing", "message": "goal reply unavailable"}

    async def voice_admission(self, action: str, **params: Any) -> Dict[str, Any]:
        """Admit or revoke a voice capability in this session's daemon."""
        if self.daemon is None or self.daemon.rpc is None:
            raise RuntimeError(f"session {self.session_id} has no live daemon")
        result = await self.daemon.rpc.call(
            "state.voice_admission", {"action": action, **params}
        )
        if not isinstance(result, dict):
            raise TypeError("state.voice_admission expected a result dict")
        if result.get("error"):
            raise ValueError(str(result["error"]))
        return result

    async def record_interrupted_reply(self, turn_id: str, text: str) -> Dict[str, Any]:
        """Keep the part of a voice reply the listener heard before talking over it."""
        if self.daemon is None or self.daemon.rpc is None:
            raise RuntimeError(f"session {self.session_id} has no live daemon")
        result = await self.daemon.rpc.call(
            "state.voice_interrupted_reply", {"turn_id": turn_id, "text": text}
        )
        if not isinstance(result, dict):
            raise TypeError("state.voice_interrupted_reply expected a result dict")
        if result.get("error"):
            raise ValueError(str(result["error"]))
        if result.get("recorded"):
            await self.refresh_history(notify=True)  # open windows hear the new row
        return result

    def cancel_turn(self, voice_turn_id: str | None = None) -> None:
        """Ask the daemon to cancel the in-flight turn (fire and forget).

        With `voice_turn_id`, only while that voice turn is the one running: a
        typed turn that started after it runs on.
        """
        if self.daemon is None:
            return
        if voice_turn_id is None:
            self._active_turn_task = asyncio.create_task(
                self.state.cancel_current_request(), name=f"cancel-{self.session_id}"
            )
            # A cancelled tool call may never report a result or a turn end: tell
            # the open windows so a gem does not keep acting out the dead call.
            # Its late tool result arrives marked, so it restarts nothing.
            self.daemon.mark_cancelled()
            self._emit_event({"type": "turn_cancelled"})
            return

        async def cancel_voice_turn() -> None:
            result = await self.state.cancel_current_request(voice_turn_id=voice_turn_id)
            # Marked only for a stop that happened: a refused or queued cancel
            # leaves the running typed turn's tools unmarked.
            if result.get("cancelled") and not result.get("queued"):
                if self.daemon is not None:
                    self.daemon.mark_cancelled()
                self._emit_event({"type": "turn_cancelled"})

        self._active_turn_task = asyncio.create_task(
            cancel_voice_turn(), name=f"cancel-{self.session_id}"
        )

    async def cancel_owned_turn(self, request_id: str) -> Dict[str, Any]:
        """Cancel only this admitted owner, without a current-turn fallback."""
        if self.daemon is None or self.daemon.rpc is None:
            return {"cancelled": False, "request_id": request_id}
        if request_id.startswith("goal-"):
            result = await self.daemon.rpc.call("state.cancel_goal_reply", {"reply_id": request_id})
            if not isinstance(result, dict) or not isinstance(result.get("cancelled"), bool):
                raise TypeError("state.cancel_goal_reply expected a cancelled boolean")
            return {**result, "request_id": request_id}
        result = await self.daemon.rpc.call(
            "state.acp_cancel", {"request_id": request_id}
        )
        if not isinstance(result, dict):
            raise TypeError("state.acp_cancel expected a result dict")
        if result.get("error"):
            raise ValueError(str(result["error"]))
        if not isinstance(result.get("cancelled"), bool):
            raise TypeError("state.acp_cancel expected a cancelled boolean")
        if result["cancelled"]:
            self.daemon.mark_cancelled()
        return {**result, "request_id": request_id}

    # === permissions ===

    async def resolve_permission(
        self, tool_id: str, decision: str, scope: str = "once", option_id: str | None = None
    ) -> bool:
        """Answer a permission prompt the daemon is blocked on.

        The HTTP surface speaks decision+scope; the daemon speaks
        ``ConfirmationResponse`` names, so translate at this boundary.
        """
        if self.daemon is None or self.daemon.rpc is None:
            return False

        response = _confirmation_response_name(decision, scope)
        payload = {"tool_id": tool_id, "control": response}
        if option_id is not None:
            payload = {"tool_id": tool_id, "option_id": option_id}
        try:
            result = await self.daemon.rpc.call(
                PERMISSION_RESPONSE_RPC_METHOD,
                payload,
            )
        except Exception as e:
            logger.warning("permission response failed for %s: %s", tool_id, e)
            return False
        self._pending_permissions.pop(tool_id, None)
        return bool(isinstance(result, dict) and result.get("ok"))

    # === event tracking ===

    async def _track_events(self) -> None:
        """Keep session-level counters and pending prompts in step with the daemon.

        SSE consumers get their own subscription; this one exists so that a
        session with no attached browser still reports accurate stats and knows
        which permission prompts are outstanding.
        """
        queue = self.subscribe()
        turn_error = ""
        try:
            while True:
                event = await queue.get()
                etype = event.get("type")

                if etype == "error":
                    turn_error = str(event.get("message") or "") or "engine error"

                elif etype == "turn_complete":
                    if event.get("input_source") == "acp_client" and event.get("queued") is True:
                        # This admitted request was cancelled before a native
                        # turn started; it has no history or usage to count.
                        continue
                    self.total_turns += 1
                    self.total_input_tokens += int(event.get("input_tokens", 0) or 0)
                    self.total_output_tokens += int(event.get("output_tokens", 0) or 0)
                    await self.refresh_history()
                    self.last_turn_error = (
                        {"message": turn_error, "history_length": len(self.history)}
                        if turn_error
                        else None
                    )
                    turn_error = ""

                elif etype == "user_message":
                    # The daemon took a human's message into its history, from
                    # any window or its terminal: a window that opens mid-turn
                    # must find it in the mirror. The refresh announces nothing
                    # (open windows reload on turn_complete), so a turn that
                    # starts never reloads the page that sent it.
                    self._schedule_refresh(notify=False)

                elif etype in ("hub_message", "tool_result"):
                    # Mid-turn conversation movement (a background message
                    # landed, a tool wrote its result): keep the mirror warm
                    # without a per-event RPC -- one coalesced refresh serves
                    # any burst. turn_complete does its own awaited refresh.
                    self._schedule_refresh()

                elif etype == "voice_reply_cut":
                    # A talk-over's cut queued while its turn unwound landed
                    # after the RPC answered: open windows reload the cut reply.
                    self._schedule_refresh(notify=True)

                elif etype == "permission_request":
                    # Store the whole normalized event, not just the raw
                    # details: a client that reloads mid-prompt needs to
                    # re-render it, and the daemon will not re-send it.
                    tool_id = str(event.get("tool_id") or "")
                    if tool_id:
                        self._pending_permissions[tool_id] = event
                elif etype == "permission_parked":
                    # Its turn ended and left the prompt open (a Stop, or 5 min
                    # unanswered): a window that loads it now must know.
                    tool_id = str(event.get("tool_id") or "")
                    stored = self._pending_permissions.get(tool_id)
                    if stored is not None:
                        details = {**(stored.get("details") or {}), "parked": True}
                        self._pending_permissions[tool_id] = {**stored, "details": details}
                elif etype == "acp_waiting":
                    self.acp_waiting = bool(event.get("waiting"))
                elif etype == "plan":
                    self.acp_plan = dict(event.get("payload") or {})
                elif etype == "usage_update":
                    self.acp_usage = dict(event.get("payload") or {})

                elif etype in ("permission_granted", "permission_denied", "permission_cancelled"):
                    self._pending_permissions.pop(str(event.get("tool_id") or ""), None)
                elif etype == "acp_session":
                    self.acp_plan = {}
                    self.acp_usage = {}
                    self.acp_waiting = False
                    if isinstance(self.profile, ProfileSnapshot) and self.profile.provider == "acp":
                        self.profile.supports_vision = event.get("supports_vision") is True
                elif etype == "profile_changed":
                    payload = event.get("profile")
                    if isinstance(payload, dict) and payload.get("name"):
                        self.profile = ProfileSnapshot.from_dict(payload)

                        self._notify_persistence_changed()
                        self._profile_revision += 1
                        if self.profile.provider != "acp":
                            self.acp_plan = {}
                            self.acp_usage = {}
                            self.acp_waiting = False

                elif etype == "daemon_closed":
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("session %s event tracking stopped: %s", self.session_id, e)
        finally:
            self.unsubscribe(queue)

    # === serialization ===

    def to_dict(self) -> Dict[str, Any]:
        identity = self.daemon.identity if self.daemon else ""
        agent_name = (
            self.agent or (self.daemon.agent_name if self.daemon else "") or "default"
        )
        model = getattr(self.profile, "model", "")
        provider = getattr(self.profile, "provider", "")
        harness = ""
        if provider == "acp":
            harness = getattr(self.profile, "description", "") or getattr(self.profile, "name", "")
        return {
            "harness": harness,
            "provider": provider,
            "acp_waiting": self.acp_waiting,
            "acp_usage": self.acp_usage,
            "acp_plan": self.acp_plan,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "name": self.display_name or session_display_name(self.session_id),
            "external": self.external,
            "profile": getattr(self.profile, "name", str(self.profile or "")),
            "model": model,
            # What the daemon will send next (mirrored by apply_profile_mirror).
            "effort": getattr(self.profile, "effort", "") or "",
            "supports_vision": (
                bool(getattr(self.profile, "supports_vision", False))
                if provider == "acp" else supports_vision(model, provider)
            ),
            "agent": agent_name,
            "workspace": self.workspace,
            "device": self.device,
            # The mode name, matching permissions.py; None (unknown) while the
            # facts read has not answered or last failed. Reporting a guessed
            # mode here would let the UI act on permissions the agent does not
            # have.
            "approval_mode": (
                self.approval_mode
                if self.approval_mode in _APPROVAL_MODE_MAP
                and not self.approval_mode_warning
                else None
            ),
            "approval_mode_warning": self.approval_mode_warning,
            "persistence_warning": self.persistence_warning,
            "deletion_pending": self.deletion_pending,
            "deletion_warning": self.deletion_warning,
            "created_at": self.created_at.isoformat(),
            "total_turns": self.total_turns,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "history_length": len(self.history),
            "active": self.alive,
            "identity": identity,
            "daemon_pid": self.daemon.pid if self.daemon else 0,
            "mcp_servers": self.mcp_server_names,
        }
