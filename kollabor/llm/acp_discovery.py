"""Bounded, isolated first-use discovery for ACP model-picker options.

Discovery uses a separate ACP process and session in a private temporary
directory. It never prompts the harness, passes MCP servers, changes a user's
active session, or writes the learned option set to the name-keyed disk cache.
Context-keyed option progress is retained in process memory briefly; raw
credentials and launch environments are never retained in the cache.

The picker does not wait for any of this. The engine learns each profile's
choices once in the background (``warm_in_background``), keeps the last good
list in process memory (``known_outcomes``) and refreshes it behind that list
when it is old (#953). Like the rest of this module it keeps the list in memory
only: a restart warms again, so another login's options are never left behind.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, replace
from typing import Any, Iterable

from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor_ai.acp_options import find_option, option_values

logger = logging.getLogger(__name__)

_now = time.monotonic  # one clock for every timer here, so a test can move it
_CACHE_TTL_SECONDS = 30.0
_RESOLVE_TIMEOUT_SECONDS = 4.0
_PROBE_TIMEOUT_SECONDS = 20.0
_STARTUP_TIMEOUT_SECONDS = 8.0
_OPERATION_TIMEOUT_SECONDS = 2.0
_MAX_MODELS = 64
_MODELS_PER_REQUEST = 6
_MAX_AUTOMATIC_ROUNDS = 11  # ceil(MAX_MODELS / MODELS_PER_REQUEST)
_MAX_MODEL_NAME_LENGTH = 256
_MAX_CONCURRENT_PROFILES = 2
_STALE_AFTER_SECONDS = 600.0  # a complete list is probed again, behind itself, once this old
_RETRY_BACKOFF_SECONDS = (15.0, 60.0, 300.0)  # wait after 1, 2, 3+ failed attempts in a row
_MAX_FAILING_VISIBLE_SECONDS = 1800.0  # a good list stays up this long while its refreshes fail
_WARM_MAX_ROUNDS = 40  # per probe cycle; discovery stops reporting "more" long before
_AUTH_FAILED_MESSAGE = "ACP authentication failed; the existing CLI login was left unchanged."
_INCOMPLETE_MESSAGE = "Some model effort choices could not be confirmed; retry to try again."


@dataclass(frozen=True)
class ACPDiscoveryOutcome:
    """Fresh picker options and an honest discovery state for one profile."""

    options: dict[str, Any]
    status: str
    message: str | None = None
    next_index: int = 0
    attempted_models: tuple[str, ...] = ()
    batch_rounds: int = 0
    more: bool = False


@dataclass(frozen=True)
class _CachedOutcome:
    expires_at: float
    outcome: ACPDiscoveryOutcome


@dataclass
class _InFlight:
    task: asyncio.Task[ACPDiscoveryOutcome]
    waiters: int = 0


_cache: dict[str, _CachedOutcome] = {}
_inflight: dict[str, _InFlight] = {}
_profile_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PROFILES)


def _configured_profile(profile: Any) -> ACPProfile:
    """Copy only ACP launch inputs; never copy profile MCP configuration."""
    name = str(getattr(profile, "name", "") or "")
    command = getattr(profile, "command", None)
    if isinstance(command, str):
        command = (command,)
    if not name or not isinstance(command, (tuple, list)) or not command:
        raise ValueError("ACP profile needs a name and command")
    return ACPProfile.from_dict(name, {
        "command": tuple(str(part) for part in command),
        "cwd": getattr(profile, "cwd", None) or None,
        "workspace": getattr(profile, "workspace", None) or None,
        "env": getattr(profile, "env", None) or {},
        "pass_env": getattr(profile, "pass_env", None) or (),
        "mcp_servers": (),
    })


def _context_key(
    profile: ACPProfile,
    command: tuple[str, ...],
    effective_env: dict[str, str],
    configured_cwd: str,
) -> str:
    """Hash the effective account/launch context without retaining its values."""
    payload = {
        "profile": profile.name,
        "command": list(command),
        "environment": effective_env,
        "workspace": os.path.realpath(configured_cwd),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _outcome(
    status: str,
    *,
    models: list[str] | None = None,
    efforts_by_model: dict[str, list[str]] | None = None,
    unknown_effort_models: list[str] | None = None,
    message: str | None = None,
    next_index: int = 0,
    attempted_models: Iterable[str] = (),
    batch_rounds: int = 0,
    more: bool = False,
) -> ACPDiscoveryOutcome:
    return ACPDiscoveryOutcome(
        options={
            "models": list(models or []),
            "efforts_by_model": dict(efforts_by_model or {}),
            "unknown_effort_models": list(unknown_effort_models or []),
        },
        status=status,
        message=message,
        next_index=next_index,
        attempted_models=tuple(attempted_models),
        batch_rounds=batch_rounds,
        more=more,
    )


def _failure_message(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "ACP model discovery timed out; try opening the picker again."
    if getattr(exc, "error_class", None) == "authentication":
        return _AUTH_FAILED_MESSAGE
    return "ACP model discovery could not start; check the configured harness and its login."


def _models(options: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[str], bool]:
    option = find_option(options, "model")
    if option is None:
        return None, [], False
    candidates = option_values(option)
    models: list[str] = []
    truncated = len(candidates) > _MAX_MODELS
    for value in candidates[:_MAX_MODELS]:
        if value and len(value) <= _MAX_MODEL_NAME_LENGTH and value not in models:
            models.append(value)
        else:
            truncated = True
    return option, models, truncated


def _efforts(options: list[dict[str, Any]]) -> list[str]:
    option = find_option(options, "thought_level") or find_option(options, "effort")
    return option_values(option) if option is not None else []


async def _close_runner(runner: ACPRunner | None) -> None:
    if runner is None:
        return
    # A single cancellation must not interrupt ACPRunner's bounded process-tree
    # teardown and release the global slot while an owned child is still alive.
    close_task = asyncio.create_task(runner.close())
    try:
        await asyncio.shield(close_task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(close_task)
        finally:
            raise


async def _start_runner(profile: ACPProfile) -> ACPRunner:
    logger.debug("acp discovery: starting a probe harness for %s", profile.name)
    runner = ACPRunner(
        profile,
        remember_options=False,
        operation_timeout=_OPERATION_TIMEOUT_SECONDS,
        startup_timeout=_STARTUP_TIMEOUT_SECONDS,
        persist_profile_resolution=False,
    )
    try:
        await asyncio.wait_for(
            runner.start(), timeout=2 * _STARTUP_TIMEOUT_SECONDS + 0.25
        )
    except BaseException:
        await _close_runner(runner)
        raise
    return runner


async def _discover_resolved(
    profile: ACPProfile,
    command: tuple[str, ...],
    cached: ACPDiscoveryOutcome | None = None,
) -> ACPDiscoveryOutcome:
    models: list[str] = []
    efforts_by_model: dict[str, list[str]] = {}
    unknown: list[str] = []
    truncated = False
    runner: ACPRunner | None = None
    message: str | None = None
    failure: BaseException | None = None

    next_index = cached.next_index if cached is not None else 0
    attempted_models: set[str] = set()
    batch_rounds = 0
    attempted_this_request = 0
    batch_counted = False
    if cached is not None:
        attempted_models.update(cached.attempted_models)
        batch_rounds = cached.batch_rounds if cached.more else 0
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            async with _profile_semaphore:
                with tempfile.TemporaryDirectory(prefix="kollab-acp-discovery-") as scratch:
                    probe_profile = replace(
                        profile,
                        command=command,
                        cwd=scratch,
                        workspace=None,
                        mcp_servers=(),
                    )
                    try:
                        runner = await _start_runner(probe_profile)
                        current_options = runner.advertised_config_options()
                        model_option, models, truncated = _models(current_options)
                        if model_option is None:
                            message = "The harness did not advertise model choices."
                        else:
                            model_id = str(model_option.get("id") or "model")
                            current_model = str(model_option.get("currentValue") or "")
                            if cached is not None and cached.options.get("models") == models:
                                efforts_by_model.update({
                                    model: list(efforts)
                                    for model, efforts in cached.options.get("efforts_by_model", {}).items()
                                    if model in models
                                })
                                unknown = [
                                    model for model in cached.options.get("unknown_effort_models", [])
                                    if model in models and model not in efforts_by_model
                                ]
                                retry_failures = not cached.more
                            elif cached is not None:
                                next_index = 0
                                attempted_models.clear()
                                batch_rounds = 0
                                retry_failures = True
                            else:
                                retry_failures = True

                            count = len(models)
                            order = (
                                list(range(next_index % count, count))
                                + list(range(0, next_index % count))
                                if count else []
                            )
                            for position in order:
                                model = models[position]
                                if model in efforts_by_model:
                                    continue
                                if model in attempted_models and not retry_failures:
                                    continue
                                if attempted_this_request >= _MODELS_PER_REQUEST:
                                    break
                                attempted_this_request += 1
                                next_index = (position + 1) % max(count, 1)
                                if runner is None:
                                    try:
                                        runner = await _start_runner(probe_profile)
                                        current_options = runner.advertised_config_options()
                                        restarted_model, restarted_models, _ = _models(current_options)
                                        if restarted_model is None or model not in restarted_models:
                                            raise RuntimeError(
                                                "the restarted harness changed its model options"
                                            )
                                        model_id = str(restarted_model.get("id") or "model")
                                        current_model = str(
                                            restarted_model.get("currentValue") or ""
                                        )
                                    except asyncio.CancelledError:
                                        raise
                                    except Exception as exc:  # noqa: BLE001 -- retain partial options
                                        failure = exc
                                        unknown.extend(
                                            candidate for candidate in models
                                            if candidate not in efforts_by_model
                                            and candidate not in unknown
                                        )
                                        break
                                if model == current_model:
                                    efforts_by_model[model] = _efforts(current_options)
                                    attempted_models.add(model)
                                    continue
                                try:
                                    attempted_models.add(model)
                                    current_options = await asyncio.wait_for(
                                        runner.set_config_option(model_id, model),
                                        timeout=_OPERATION_TIMEOUT_SECONDS + 0.25,
                                    )
                                    selected_model = find_option(current_options, "model")
                                    if (
                                        selected_model is None
                                        or str(selected_model.get("currentValue") or "") != model
                                    ):
                                        raise RuntimeError(
                                            "the harness did not confirm the selected model"
                                        )
                                    efforts_by_model[model] = _efforts(current_options)
                                    unknown = [
                                        candidate for candidate in unknown if candidate != model
                                    ]
                                    current_model = model
                                except asyncio.CancelledError:
                                    raise
                                except Exception as exc:  # noqa: BLE001 -- state may have changed
                                    unknown.append(model)
                                    message = _failure_message(exc)
                                    failure = exc
                                    await _close_runner(runner)
                                    runner = None
                                    current_model = ""
                                    current_options = []
                            for model in models:
                                if model not in efforts_by_model and model not in unknown:
                                    unknown.append(model)
                            if len(efforts_by_model) == len(models):
                                unknown = []
                                if not truncated:
                                    next_index = 0
                            if attempted_this_request:
                                batch_rounds += 1
                                batch_counted = True
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 -- keep partial discovery honest
                        failure = exc
                        message = _failure_message(exc)
                    finally:
                        if attempted_this_request and not batch_counted:
                            batch_rounds += 1
                            batch_counted = True
                        # Process cleanup must finish while its private cwd and
                        # global concurrency slot are still owned.
                        await _close_runner(runner)
                        runner = None
    except asyncio.CancelledError:
        raise
    except TimeoutError as exc:
        message = _failure_message(exc)
        remaining = [model for model in models if model not in efforts_by_model]
        unknown = list(dict.fromkeys(unknown + remaining))
    except Exception as exc:  # noqa: BLE001 -- picker must fall back honestly
        failure = exc
        message = _failure_message(exc)

    if not models:
        if failure is not None:
            return _outcome("unavailable", message=message or _failure_message(failure))
        if message:
            return _outcome("unavailable", message=message)
        return _outcome("unavailable", message="The harness did not advertise model choices.")
    if truncated:
        message = "The harness advertised more model choices than discovery can inspect."
    unknown = list(dict.fromkeys(unknown))
    more = bool(
        not truncated
        and batch_rounds < _MAX_AUTOMATIC_ROUNDS
        and any(model not in attempted_models for model in models)
    )
    if unknown or truncated or failure is not None or more:
        return _outcome(
            "partial", models=models, efforts_by_model=efforts_by_model,
            unknown_effort_models=unknown,
            message=message or (
                "Loading more model options." if more else _INCOMPLETE_MESSAGE
            ),
            next_index=next_index,
            attempted_models=attempted_models,
            batch_rounds=batch_rounds,
            more=more,
        )
    return _outcome(
        "ready", models=models, efforts_by_model=efforts_by_model,
        unknown_effort_models=[],
        attempted_models=attempted_models,
        batch_rounds=batch_rounds,
    )


def _cache_result(key: str, flight: _InFlight) -> None:
    if _inflight.get(key) is flight:
        _inflight.pop(key, None)
    task = flight.task
    if task.cancelled():
        return
    try:
        result = task.result()
    except BaseException:
        return
    if result.status in {"ready", "partial"} and result.options.get("models"):
        now = _now()
        for old_key, old_result in tuple(_cache.items()):
            if old_result.expires_at <= now:
                _cache.pop(old_key, None)
        if len(_cache) >= 128:
            oldest = min(_cache, key=lambda item: _cache[item].expires_at)
            _cache.pop(oldest, None)
        _cache[key] = _CachedOutcome(now + _CACHE_TTL_SECONDS, result)


async def discover_profile(
    profile_value: Any,
    *,
    fresh: bool = False,
    seed: ACPDiscoveryOutcome | None = None,
) -> ACPDiscoveryOutcome:
    """Discover one ACP profile's actual choices, with short in-process caching.

    ``fresh`` probes even when a complete result is cached (a partial one still
    seeds the next round). ``seed`` is where a probe starts when no partial
    result is cached: a retry then asks only for the models that failed.
    """
    try:
        profile = _configured_profile(profile_value)
        resolved = await asyncio.wait_for(
            asyncio.to_thread(profile.resolve, persist_cache=False),
            timeout=_RESOLVE_TIMEOUT_SECONDS,
        )
        command, effective_env, configured_cwd = resolved
        key = _context_key(profile, command, effective_env, configured_cwd)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- do not expose launch inputs/errors
        return _outcome("unavailable", message=_failure_message(exc))

    cached = _cache.get(key)
    now = _now()
    start_from = seed
    if cached is not None:
        if cached.expires_at > now:
            if cached.outcome.status != "ready":
                start_from = cached.outcome
            elif not fresh:
                return cached.outcome
        _cache.pop(key, None)

    flight = _inflight.get(key)
    if flight is None:
        task = asyncio.create_task(
            _discover_resolved(profile, command, start_from),
            name="acp-model-discovery",
        )
        flight = _InFlight(task)
        _inflight[key] = flight
        task.add_done_callback(
            lambda _finished, cache_key=key, current=flight: _cache_result(cache_key, current)
        )
    flight.waiters += 1
    released = False
    try:
        return await asyncio.shield(flight.task)
    except asyncio.CancelledError:
        flight.waiters -= 1
        released = True
        if flight.waiters == 0 and not flight.task.done():
            flight.task.cancel()
            try:
                await asyncio.shield(flight.task)
            except BaseException:
                pass
        raise
    finally:
        if not released:
            flight.waiters -= 1


async def discover_profiles(profiles: Iterable[Any]) -> dict[str, ACPDiscoveryOutcome]:
    """Discover every configured ACP profile; one failure never hides siblings."""
    rows = [profile for profile in profiles if str(getattr(profile, "provider", "")).lower() == "acp"]
    if not rows:
        return {}
    outcomes = await asyncio.gather(
        *(discover_profile(profile) for profile in rows),
        return_exceptions=True,
    )
    result: dict[str, ACPDiscoveryOutcome] = {}
    for profile, outcome in zip(rows, outcomes):
        name = str(getattr(profile, "name", "") or "")
        if isinstance(outcome, BaseException):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            result[name] = _outcome("unavailable", message=_failure_message(outcome))
        else:
            result[name] = outcome
    return result


# --- Background warm cache (#953) -------------------------------------------
#
# The picker used to wait for every harness on every open and forgot the answer
# 30 s later. Now each ACP profile is probed in the background, one task per
# profile (a slow harness never holds back a fast one), and its last good list
# is kept here in memory. ``known_outcomes`` answers from it at once; a list is
# never hidden by age, only replaced by a newer one of equal or better quality.


@dataclass
class _Warm:
    """What the picker shows for one profile, and when it may be probed again."""

    fingerprint: str
    outcome: ACPDiscoveryOutcome  # what known_outcomes answers
    tried: float  # when the last attempt ended
    ok: bool = False  # the last finished attempt gave a complete list
    progress: bool = False  # the outcome is an unfinished round
    failures: int = 0  # failed attempts in a row
    failing_since: float | None = None  # when the first of them ended


@dataclass
class _WarmTask:
    task: asyncio.Task[None]
    fingerprint: str


_warm: dict[object, _Warm] = {}
_warm_tasks: dict[object, _WarmTask] = {}
_PENDING = _outcome("partial", message="Loading more model options.", more=True)


def _warm_key(scope: str, name: str) -> str | tuple[str, str]:
    """Keep the engine folder's existing keys while isolating named workspaces."""
    return name if not scope else (scope, name)


def _in_warm_scope(key: object, scope: str) -> bool:
    """Only reconcile profiles belonging to the workspace being refreshed."""
    return isinstance(key, str) if not scope else isinstance(key, tuple) and key[0] == scope


def warming_enabled() -> bool:
    """False when KOLLAB_PICKER_WARM is 0, false, no or off.

    The picker then discovers on request, as it did before #953: the rollback
    lever, and what test and end-to-end launchers set so they start no harness.
    """
    return os.environ.get("KOLLAB_PICKER_WARM", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _is_acp(profile: Any) -> bool:
    return str(getattr(profile, "provider", "")).lower() == "acp"


def _name(profile: Any) -> str:
    return str(getattr(profile, "name", "") or "")


def launch_fingerprint(profile: Any) -> str:
    """Hash of everything that decides whose choices a profile lists.

    The launch inputs ``_configured_profile`` reads, plus the values of the
    variables a profile passes through from the engine's environment (a login
    often lives there). Only the hash is kept; sync and cheap, so it needs no
    ``profile.resolve``.
    """
    try:
        command = getattr(profile, "command", None) or ()
        if isinstance(command, str):
            command = (command,)
        pass_env = sorted(str(item) for item in (getattr(profile, "pass_env", None) or ()))
        payload = {
            "name": _name(profile),
            "command": [str(part) for part in command],
            "cwd": str(getattr(profile, "cwd", None) or ""),
            "workspace": str(getattr(profile, "workspace", None) or ""),
            "env": {
                str(key): str(value)
                for key, value in dict(getattr(profile, "env", None) or {}).items()
            },
            "passed": {item: os.environ.get(item) for item in pass_env},
        }
    except Exception:  # noqa: BLE001 -- an odd profile still gets a stable key
        payload = {"name": _name(profile)}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def known_outcomes(
    profiles: Iterable[Any], *, scope: str = ""
) -> dict[str, ACPDiscoveryOutcome]:
    """What the picker shows for each ACP profile right now. Never waits, never
    launches anything: a profile not learned yet reads as "loading"."""
    shown: dict[str, ACPDiscoveryOutcome] = {}
    for profile in profiles:
        if not _is_acp(profile):
            continue
        name = _name(profile)
        key = _warm_key(scope, name)
        record = _warm.get(key)
        fingerprint = launch_fingerprint(profile)
        current = record is not None and record.fingerprint == fingerprint
        running = _warm_tasks.get(key)
        refreshing = (
            current
            and running is not None
            and not running.task.done()
            and running.fingerprint == fingerprint
            and record is not None
            and record.outcome.status != "ready"
        )
        if current and record is not None and refreshing:
            shown[name] = replace(
                record.outcome,
                status="partial",
                message="Loading more model options.",
                more=True,
            )
        else:
            shown[name] = record.outcome if current and record is not None else _PENDING
    return shown


def _backoff(failures: int) -> float:
    return _RETRY_BACKOFF_SECONDS[min(max(failures, 1), len(_RETRY_BACKOFF_SECONDS)) - 1]


def _due(record: _Warm | None, fingerprint: str, now: float, force: bool) -> bool:
    if record is None or record.fingerprint != fingerprint:
        return True
    if record.progress:  # a probe that stopped half way: pick it up again
        return force or now - record.tried >= _RETRY_BACKOFF_SECONDS[0]
    if record.ok:  # a complete list is refreshed behind itself once it is old
        return now - record.tried >= _STALE_AFTER_SECONDS
    return force or now - record.tried >= _backoff(record.failures)


def _cancel(entry: _WarmTask) -> None:
    try:
        entry.task.cancel()
    except RuntimeError:  # its event loop is gone; nothing left to stop
        pass


def warm_in_background(
    profiles: Iterable[Any], *, force: bool = False, scope: str = ""
) -> None:
    """Make sure every ACP profile's choices are learned, or being learned.

    Starts one background probe per profile that has none yet, whose list is
    old (``_STALE_AFTER_SECONDS``) or whose last attempt failed and waited out
    its backoff. ``force`` (the picker's Retry) skips that wait, but only for
    profiles whose list is not complete: a ready one is left alone. Profiles
    gone from this workspace's list are forgotten. Never waits; call it from a running loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    wanted = {
        _warm_key(scope, _name(profile)): (_name(profile), profile)
        for profile in profiles
        if _is_acp(profile) and _name(profile)
    }
    for key in [key for key in _warm if _in_warm_scope(key, scope) and key not in wanted]:
        del _warm[key]
    for key in [key for key in _warm_tasks if _in_warm_scope(key, scope) and key not in wanted]:
        _cancel(_warm_tasks.pop(key))
    now = _now()
    for key, (name, profile) in wanted.items():
        fingerprint = launch_fingerprint(profile)
        running = _warm_tasks.get(key)
        if running is not None and not running.task.done() and running.task.get_loop() is loop:
            if running.fingerprint == fingerprint:
                continue
            _cancel(_warm_tasks.pop(key))  # edited while it was being probed
        if not _due(_warm.get(key), fingerprint, now, force):
            continue
        task = loop.create_task(
            _warm_profile(profile, name, fingerprint, key), name=f"acp-warm-{name}"
        )
        _warm_tasks[key] = _WarmTask(task, fingerprint)


def _retry_seed(key: object, fingerprint: str) -> ACPDiscoveryOutcome | None:
    """A retry asks only for the models that failed: it starts from the last
    incomplete list. A complete one is never a seed, or a refresh would probe nothing."""
    record = _warm.get(key)
    if record is None or record.fingerprint != fingerprint or record.ok or record.progress:
        return None
    outcome = record.outcome
    if outcome.status == "partial" and not outcome.more and outcome.options.get("models"):
        return outcome
    return None


async def _warm_profile(profile: Any, name: str, fingerprint: str, key: object) -> None:
    """One probe cycle for one profile: its rounds in a row, each stored as it ends."""
    started = _now()
    seed = _retry_seed(key, fingerprint)
    outcome = _PENDING
    logger.info("model picker warm: probing %s", name)
    try:
        for _round in range(_WARM_MAX_ROUNDS):
            outcome = await discover_profile(profile, fresh=True, seed=seed)
            seed = None  # the next round continues from discovery's own partial result
            _store(key, fingerprint, outcome)
            if not outcome.more:
                break
        else:  # discovery stops reporting "more" long before this; end the cycle honestly
            outcome = replace(outcome, status="partial", more=False, message=_INCOMPLETE_MESSAGE)
            _store(key, fingerprint, outcome)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- a broken probe never takes the engine down
        logger.warning("model picker warm: %s failed", name, exc_info=True)
        _store(key, fingerprint, _outcome("unavailable", message=_failure_message(exc)))
    else:
        logger.info(
            "model picker warm: %s %s, %d models, %.1f s",
            name, outcome.status, len(outcome.options.get("models", [])), _now() - started,
        )
    finally:
        entry = _warm_tasks.get(key)
        if entry is not None and entry.task is asyncio.current_task():
            del _warm_tasks[key]


def _rank(outcome: ACPDiscoveryOutcome) -> int:
    """Quality of a list: a complete one, then one with models, then nothing."""
    if outcome.status == "ready":
        return 3
    return 2 if outcome.options.get("models") else 1


def _store(key: object, fingerprint: str, outcome: ACPDiscoveryOutcome) -> None:
    """Keep a probe result, never trading a better visible list for a worse one."""
    now = _now()
    record = _warm.get(key)
    good = outcome.more or outcome.status == "ready"
    if record is None or record.fingerprint != fingerprint:
        _warm[key] = _Warm(
            fingerprint, outcome, tried=now,
            ok=not outcome.more and outcome.status == "ready", progress=outcome.more,
            failures=0 if good else 1, failing_since=None if good else now,
        )
        return
    record.tried = now
    if outcome.more:
        record.progress = True
        # An unfinished round shows where nothing better is up: the first warm builds
        # its list round by round, and a refresh never downgrades the list it replaces.
        if record.outcome.more or _rank(record.outcome) == 1:
            record.outcome = outcome
        return
    record.progress = False
    if outcome.status == "ready":
        record.outcome, record.ok = outcome, True
        record.failures, record.failing_since = 0, None
        return
    record.ok = False
    record.failures += 1
    if record.failing_since is None:
        record.failing_since = now
    if record.outcome.more:  # an unfinished list: end it honestly and keep its rows
        record.outcome = outcome if _rank(outcome) >= _rank(record.outcome) else replace(
            record.outcome, status="partial", more=False,
            message=outcome.message or _INCOMPLETE_MESSAGE,
        )
    elif (
        now - record.failing_since >= _MAX_FAILING_VISIBLE_SECONDS
        or outcome.message == _AUTH_FAILED_MESSAGE
        or _rank(outcome) >= _rank(record.outcome)
    ):
        record.outcome = outcome


async def stop_warming() -> None:
    """Stop every background probe (engine shutdown) and wait until their
    harnesses are closed."""
    entries = list(_warm_tasks.values())
    _warm_tasks.clear()
    loop = asyncio.get_running_loop()
    stopping = []
    for entry in entries:
        if entry.task.get_loop() is loop and not entry.task.done():
            entry.task.cancel()
            stopping.append(entry.task)
    if stopping:
        await asyncio.gather(*stopping, return_exceptions=True)
