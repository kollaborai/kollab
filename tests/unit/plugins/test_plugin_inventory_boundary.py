"""Plugin inventory producers fail closed on malformed state and manifest text."""

import json
import math

from kollabor_plugins import registry
from kollabor_plugins.packages import validate_manifest

CANARY = "secret-token-canary-1397"


def _entry():
    return {
        "id": "hello",
        "name": "Hello",
        "version": "1.2.3",
        "kind": "installed",
        "source": "https://example.invalid/hello.git",
        "commit": "a" * 40,
        "enabled": True,
        "enable_order": 1,
        "shadowed": False,
        "state": "ok",
        "error": None,
        "web": {"entry": "web/dist/index.js", "style": None, "hash": "b" * 64},
        "contributes": {"menu": [{"id": "home", "label": "Home", "open": {"view": "home"}}]},
        "services": [
            {
                "name": "worker",
                "state": "running",
                "desired": "running",
                "pid": 123,
                "since": 1000.0,
                "restarts": 0,
                "heartbeat": 1001.0,
                "heartbeat_s": 60.0,
                "heartbeat_monitor_since": 999.0,
                "stop_deadline": None,
                "error": None,
                "secrets": ["API_TOKEN"],
            }
        ],
        "settings": [{"type": "label", "path": "plugin-values.hello.label", "label": "Label", "editable": False}],
        "settings_section": {"title": "Settings"},
        "uses": ["net:example.invalid"],
        "granted": [],
    }


def _manifest():
    return {
        "format": 1,
        "id": "hello",
        "kollab": ">=0",
        "name": "Hello",
        "version": "1.2.3",
        "description": "A plugin",
        "services": {"worker": {"command": ["python", "worker.py"]}},
    }


def _type_errors(entry):
    errors = []
    checks = {
        "id": isinstance(entry.get("id"), str),
        "name": isinstance(entry.get("name"), str),
        "version": entry.get("version") is None or isinstance(entry.get("version"), str),
        "kind": entry.get("kind") in ("built-in", "installed", "dev"),
        "source": entry.get("source") is None or isinstance(entry.get("source"), str),
        "commit": entry.get("commit") is None or isinstance(entry.get("commit"), str),
        "enabled": type(entry.get("enabled")) is bool,
        "enable_order": entry.get("enable_order") is None or type(entry.get("enable_order")) is int,
        "shadowed": type(entry.get("shadowed")) is bool,
        "state": entry.get("state") in ("ok", "error", "off", "stopping", "stop-failed", "changed-on-disk", "skipped"),
        "error": entry.get("error") is None or isinstance(entry.get("error"), str),
        "web": entry.get("web") is None or isinstance(entry.get("web"), dict),
        "contributes": isinstance(entry.get("contributes"), dict),
        "services": isinstance(entry.get("services"), list),
        "settings": isinstance(entry.get("settings"), list),
        "settings_section": entry.get("settings_section") is None or isinstance(entry.get("settings_section"), dict),
        "uses": isinstance(entry.get("uses"), list) and all(isinstance(value, str) for value in entry["uses"]),
        "granted": isinstance(entry.get("granted"), list) and all(isinstance(value, str) for value in entry["granted"]),
    }
    errors.extend(key for key, valid in checks.items() if not valid)
    for service in entry.get("services", []) if isinstance(entry.get("services"), list) else []:
        if not isinstance(service, dict):
            errors.append("service is not an object")
            continue
        fields = {
            "service.name": isinstance(service.get("name"), str),
            "service.state": service.get("state")
            in (
                "starting",
                "running",
                "backoff",
                "stopping",
                "stop-failed",
                "exited",
                "stopped",
                "crash-stopped",
                "error",
                "unknown",
                "failed",
            ),
            "service.desired": service.get("desired") in ("running", "stopped"),
            "service.pid": service.get("pid") is None or type(service.get("pid")) is int,
            "service.since": service.get("since") is None or type(service.get("since")) in (int, float),
            "service.restarts": type(service.get("restarts")) is int,
            "service.heartbeat": service.get("heartbeat") is None or type(service.get("heartbeat")) in (int, float),
            "service.heartbeat_s": service.get("heartbeat_s") is None
            or type(service.get("heartbeat_s")) in (int, float),
            "service.heartbeat_monitor_since": service.get("heartbeat_monitor_since") is None
            or type(service.get("heartbeat_monitor_since")) in (int, float),
            "service.stop_deadline": service.get("stop_deadline") is None
            or type(service.get("stop_deadline")) in (int, float),
            "service.error": service.get("error") is None or isinstance(service.get("error"), str),
            "service.secrets": isinstance(service.get("secrets"), list)
            and all(isinstance(value, str) for value in service["secrets"]),
        }
        errors.extend(
            f"{key}={service.get(key.removeprefix('service.'), None)!r}" for key, valid in fields.items() if not valid
        )
        if service.get("error") not in (
            None,
            "Could not prepare plugin engine credentials",
            "Could not start this process. View Log for details.",
            "Could not stop this process. Retry stopping it.",
            "Could not verify the stop. Check the process before retrying.",
        ):
            errors.append("service.error is not a fixed message")
    if entry.get("error") not in (
        None,
        "Plugin files changed since approval",
        "Plugin metadata could not be loaded",
        "Python entry failed while loading; see the process log for details.",
    ):
        errors.append("error is not a fixed message")
    if isinstance(entry.get("enable_order"), (int, float)) and not math.isfinite(entry["enable_order"]):
        errors.append("enable_order is not finite")
    return errors


def test_plugin_inventory_boundary_normalizes_every_field_and_bounds_manifest_strings(tmp_path):
    malformed_entries = [
        ("entry id", lambda row: row.update(id={"token": CANARY})),
        ("entry name", lambda row: row.update(name=CANARY)),
        ("entry version", lambda row: row.update(version={"token": CANARY})),
        ("entry kind", lambda row: row.update(kind={"token": CANARY})),
        ("entry source", lambda row: row.update(source=f"https://example.invalid/?token={CANARY}")),
        ("entry commit", lambda row: row.update(commit={"token": CANARY})),
        ("entry enabled", lambda row: row.update(enabled="true")),
        ("entry enable_order", lambda row: row.update(enable_order={"token": CANARY})),
        ("entry shadowed", lambda row: row.update(shadowed={"token": CANARY})),
        ("entry state", lambda row: row.update(state={"token": CANARY})),
        ("entry error", lambda row: row.update(state="error", error=f"Failure: {CANARY}")),
        ("entry web", lambda row: row.update(web={"entry": {"token": CANARY}, "style": [CANARY], "hash": CANARY})),
        (
            "entry contributes",
            lambda row: row.update(contributes={"menu": [{"id": CANARY, "label": CANARY, "open": {"view": CANARY}}]}),
        ),
        ("entry services", lambda row: row.update(services={"name": CANARY})),
        ("entry settings", lambda row: row.update(settings={"label": CANARY})),
        ("entry settings_section", lambda row: row.update(settings_section={"title": CANARY})),
        ("entry uses", lambda row: row.update(uses=[f"net:{CANARY}"])),
        ("entry granted", lambda row: row.update(granted={"token": CANARY})),
        ("service name", lambda row: row["services"][0].update(name=CANARY)),
        ("service state", lambda row: row["services"][0].update(state=CANARY)),
        ("service desired", lambda row: row["services"][0].update(desired=CANARY)),
        ("service pid", lambda row: row["services"][0].update(pid={"token": CANARY})),
        ("service since", lambda row: row["services"][0].update(since={"token": CANARY})),
        ("service restarts", lambda row: row["services"][0].update(restarts=CANARY)),
        ("service heartbeat", lambda row: row["services"][0].update(heartbeat={"token": CANARY})),
        ("service heartbeat_s", lambda row: row["services"][0].update(heartbeat_s=CANARY)),
        (
            "service heartbeat_monitor_since",
            lambda row: row["services"][0].update(heartbeat_monitor_since={"token": CANARY}),
        ),
        ("service stop_deadline", lambda row: row["services"][0].update(stop_deadline={"token": CANARY})),
        (
            "service error",
            lambda row: row["services"][0].update(state="error", error=f"Could not start {CANARY}: OS detail"),
        ),
        ("service secrets", lambda row: row["services"][0].update(secrets=[CANARY])),
    ]
    normalize = getattr(registry, "_normalize_plugin_entry", lambda item: item)
    failures = []
    for label, mutate in malformed_entries:
        raw = _entry()
        mutate(raw)
        try:
            normalized = normalize(raw)
        except Exception as exc:  # malformed inventory must not escape the producer boundary
            failures.append(f"{label}: raised {type(exc).__name__}")
            continue
        if label == "entry id":
            if normalized is not None:
                failures.append(f"{label}: invalid identity was retained")
            continue
        if not isinstance(normalized, dict):
            failures.append(f"{label}: did not produce an inventory row")
            continue
        encoded = json.dumps(normalized, sort_keys=True, default=str)
        if CANARY in encoded:
            failures.append(f"{label}: secret-looking text survived normalization")
        failures.extend(f"{label}: invalid {problem}" for problem in _type_errors(normalized))

    too_long = "x" * 4097
    long_manifest_values = [
        ("name", lambda manifest: manifest.update(name=too_long)),
        ("version", lambda manifest: manifest.update(version=too_long)),
        ("description", lambda manifest: manifest.update(description=too_long)),
        ("kollab range", lambda manifest: manifest.update(kollab=too_long)),
        ("plugin id", lambda manifest: manifest.update(id=too_long)),
        ("uses", lambda manifest: manifest.update(uses=[too_long])),
        ("web entry", lambda manifest: manifest.update(web={"entry": too_long})),
        ("contribution label", lambda manifest: manifest.update(contributes={"menu": [{"label": too_long}]})),
        ("service name", lambda manifest: manifest["services"].update({too_long: {"command": ["python"]}})),
        ("service command", lambda manifest: manifest["services"]["worker"].update(command=[too_long])),
        ("service restart", lambda manifest: manifest["services"]["worker"].update(restart=too_long)),
    ]
    for label, mutate in long_manifest_values:
        root = tmp_path / label.replace(" ", "-")
        root.mkdir()
        manifest = _manifest()
        mutate(manifest)
        (root / "kollab-plugin.json").write_text(json.dumps(manifest))
        try:
            validate_manifest(root, "1.0.0")
        except ValueError as exc:
            if "4096" not in str(exc):
                failures.append(f"manifest {label}: failed for a different reason: {exc}")
        else:
            failures.append(f"manifest {label}: accepted a string longer than 4096 characters")

    assert failures == [], "; ".join(failures)


def test_normalized_setting_keeps_help_a_string_for_terminal_search():
    # the terminal config view lowercases help text, so an absent help must stay a string
    setting = registry._normalize_setting(
        {"type": "checkbox", "config_path": "plugins.demo.enabled", "label": "Enabled"}
    )

    assert setting is not None
    assert isinstance(setting["help"], str)
