"""Git sources for installable plugin packages."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z", re.ASCII)
_SCP_SOURCE = re.compile(r"git@[A-Za-z0-9.-]+:[^\s?#]+\Z", re.ASCII)


def parse_git_source(source: str) -> tuple[str, str | None]:
    """Validate an install source without echoing a potentially secret URL.

    A ref, when supplied, is resolved to a branch or tag by the installer; this
    function only checks its syntax. Local paths are reserved for dev mode.
    """
    if not isinstance(source, str) or not source or any(c.isspace() for c in source):
        raise ValueError("Invalid plugin Git source")
    url, separator, ref = source.partition("#")
    if separator and not _REF.fullmatch(ref):
        raise ValueError("Invalid plugin Git ref")
    if _SCP_SOURCE.fullmatch(url):
        return url, ref if separator else None
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme in {"https", "ssh"}
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and not parts.query
            and not parts.fragment
            and bool(parts.path and parts.path != "/")
            and parts.port != 0
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Invalid plugin Git source")
    return url, ref if separator else None


_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,39}\Z", re.ASCII)
_SERVICE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}\Z", re.ASCII)
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z", re.ASCII)
_MANIFEST_KEYS = {
    "format",
    "id",
    "name",
    "version",
    "description",
    "kollab",
    "python",
    "engine",
    "services",
    "web",
    "contributes",
    "agents",
    "skills",
    "commands",
    "hooks",
    "mcp",
    "settings",
    "uses",
}
_SERVICE_KEYS = {"command", "autostart", "restart", "stop_timeout_s", "heartbeat_s", "secrets"}
_CONTRIBUTION_KEYS = {"menu", "views", "pages", "modals", "slash", "renderers"}


def _within(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"Invalid {label} path")
    path = Path(value)
    if path.is_absolute() or not (root / path).resolve().is_relative_to(root):
        raise ValueError(f"{label} path must resolve inside the plugin")
    resolved = root / path
    if not resolved.exists():
        raise ValueError(f"{label} path does not exist")
    return resolved


def validate_manifest(root: Path, kollab_version: str) -> dict:
    """Read and validate a package manifest without executing its code."""
    root = Path(root).resolve()
    manifest_path = _within(root, "kollab-plugin.json", "manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid plugin manifest JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Plugin manifest must be an object")
    try:
        required = manifest.get("kollab")
        if not isinstance(required, str) or not required:
            raise ValueError("Missing kollab version range")
        if Version(kollab_version) not in SpecifierSet(required):
            raise ValueError(f"Plugin requires kollab {required}")
    except (InvalidSpecifier, InvalidVersion) as exc:
        raise ValueError("Invalid kollab version range") from exc
    if type(manifest.get("format")) is not int or manifest["format"] != 1:
        raise ValueError("Unsupported plugin format")
    unknown = set(manifest) - _MANIFEST_KEYS
    if unknown:
        raise ValueError(f"Unknown manifest keys: {', '.join(sorted(unknown))}".lower())
    plugin_id = manifest.get("id")
    if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id):
        raise ValueError("Invalid plugin id")
    for field in ("name", "version", "description"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError(f"Invalid plugin {field}")
    uses = manifest.get("uses", [])
    if not isinstance(uses, list) or any(not isinstance(item, str) for item in uses):
        raise ValueError("Invalid uses")
    for field in ("python", "engine"):
        if field in manifest:
            value = manifest[field]
            if not isinstance(value, str) or value.count(":") != 1:
                raise ValueError(f"Invalid {field} entry")
            path, symbol = value.split(":")
            if not _SYMBOL.fullmatch(symbol):
                raise ValueError(f"Invalid {field} symbol")
            if not _within(root, path, field).is_file():
                raise ValueError(f"{field} entry must be a file")
    for field in ("agents", "skills", "commands", "hooks", "mcp", "settings"):
        if field in manifest:
            _within(root, manifest[field], field)
    web = manifest.get("web", {})
    if not isinstance(web, dict) or set(web) - {"entry", "style"}:
        raise ValueError("Invalid web keys")
    for field, value in web.items():
        path = _within(root, value, f"web {field}")
        if not path.is_file() or not path.resolve().is_relative_to((root / "web" / "dist").resolve()):
            raise ValueError("web assets must be files inside web/dist")
    contributes = manifest.get("contributes", {})
    if not isinstance(contributes, dict) or set(contributes) - _CONTRIBUTION_KEYS:
        raise ValueError("Invalid contributes keys")
    if any(not isinstance(value, list) for value in contributes.values()):
        raise ValueError("Invalid contributes entries")
    services = manifest.get("services", {})
    if not isinstance(services, dict):
        raise ValueError("Invalid services")
    for name, service in services.items():
        if not isinstance(name, str) or not _SERVICE.fullmatch(name) or not isinstance(service, dict):
            raise ValueError("Invalid service name or definition")
        if set(service) - _SERVICE_KEYS:
            raise ValueError("Unknown service keys")
        command = service.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
            raise ValueError("Invalid service command")
        if service.get("restart", "on-failure") not in {"on-failure", "always", "never"}:
            raise ValueError("Invalid service restart")
        if "autostart" in service and type(service["autostart"]) is not bool:
            raise ValueError("Invalid service autostart")
        for key in ("stop_timeout_s", "heartbeat_s"):
            if key in service and (type(service[key]) not in (int, float) or service[key] <= 0):
                raise ValueError(f"Invalid service {key}")
        secrets = service.get("secrets", [])
        if not isinstance(secrets, list) or any(not isinstance(x, str) or f"secret:{x}" not in uses for x in secrets):
            raise ValueError("Undeclared service secret")
    for path in root.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(root):
            raise ValueError("Symlink must resolve inside the plugin")
    return manifest
