"""Git sources for installable plugin packages."""

import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from kollabor_config.atomic_write import write_text_atomic
from kollabor_config.file_lock import locked_file

logger = logging.getLogger(__name__)

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


_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z", re.ASCII)


def _git(*args: str) -> bytes:
    completed = subprocess.run(["git", *args], capture_output=True, check=False)
    if completed.returncode:
        diagnostic = completed.stderr.decode("utf-8", "replace")[:4096]
        # URLs may contain arbitrary credentials in userinfo, paths, or queries.
        diagnostic = re.sub(r"(?:https?|ssh)://[^\s'\"<>]+", "[redacted URL]", diagnostic)
        diagnostic = re.sub(
            r"(?i)\b(password|token|secret|credential|authorization)\s*[:=]\s*[^\s,;]+",
            r"\1=[REDACTED]",
            diagnostic,
        )
        diagnostic = re.sub(r"(?i)\b(bearer|basic)\s+[^\s,;]+", r"\1 [REDACTED]", diagnostic)
        diagnostic = re.sub(r"\b(?:sk|ghp|gho|github_pat)-[A-Za-z0-9_-]+", "[REDACTED]", diagnostic).strip()
        message = f"Git {args[0]} failed (exit {completed.returncode})"
        raise ValueError(f"{message}: {diagnostic}" if diagnostic else message)
    return completed.stdout


def resolve_git_commit(url: str, ref: str | None) -> str:
    """Resolve exactly one remote branch or tag, or the remote's HEAD."""
    lines = _git("ls-remote", "--", url).decode("utf-8", "strict").splitlines()
    refs = {}
    for line in lines:
        parts = line.split("\t", 1)
        if len(parts) == 2 and _COMMIT.fullmatch(parts[0]):
            refs[parts[1]] = parts[0]
    if ref is None:
        commit = refs.get("HEAD")
    else:
        branch = refs.get(f"refs/heads/{ref}")
        tag = refs.get(f"refs/tags/{ref}")
        if bool(branch) == bool(tag):
            raise ValueError("Git ref is missing or ambiguous")
        commit = branch or refs.get(f"refs/tags/{ref}^{{}}") or tag
    if not commit or not _COMMIT.fullmatch(commit):
        raise ValueError("Git source has no resolvable commit")
    return commit


def files_sha256(root: Path) -> str:
    """Hash relative paths, entry types, executable bits, and file contents."""
    root = Path(root).resolve()
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            target = os.readlink(path).encode("utf-8")
            entry_type = b"l"
            content = target
        elif path.is_dir():
            entry_type = b"d"
            content = b""
        elif path.is_file():
            entry_type = b"x" if path.stat().st_mode & 0o111 else b"f"
            content = path.read_bytes()
        else:
            raise ValueError("Unsupported file type in plugin")
        digest.update(entry_type)
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def stage_git_source(source: str, stage: Path, kollab_version: str) -> dict:
    """Stage a checked Git commit without exposing its Git metadata to plugins."""
    url, ref = parse_git_source(source)
    commit = resolve_git_commit(url, ref)
    stage = Path(stage)
    stage.parent.mkdir(parents=True, exist_ok=True)
    stage.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix=".plugin-clone-", dir=stage.parent) as temporary:
            clone = Path(temporary) / "repo"
            _git("clone", "--no-checkout", "--", url, str(clone))
            archive = _git("-C", str(clone), "archive", commit)
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
                tar.extractall(stage, filter="data")
        manifest = validate_manifest(stage, kollab_version)
        return {
            "source": url,
            "ref": ref,
            "commit": commit,
            "files_sha256": files_sha256(stage),
            "manifest": manifest,
        }
    except BaseException:
        shutil.rmtree(stage)
        raise


def verify_stage(stage: Path, staged: dict, commit: str, digest: str) -> None:
    """Refuse confirmation if the pinned revision or any staged file changed."""
    if not _COMMIT.fullmatch(commit) or commit != staged.get("commit"):
        raise ValueError("Plugin commit changed")
    if digest != staged.get("files_sha256") or files_sha256(stage) != digest:
        raise ValueError("Plugin files changed after staging")


def _read_registry(path: Path) -> dict:
    if not path.exists():
        return {"plugins": {}, "dev": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Cannot read plugin registry") from exc
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), dict) or not isinstance(data.get("dev"), dict):
        raise ValueError("Invalid plugin registry")
    return data


def _write_json(path: Path, data: dict) -> None:
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _recover_locked(home: Path) -> None:
    plugins_dir = home / "plugins"
    registry_path = home / "plugins.json"
    for journal in sorted(plugins_dir.glob(".*.transaction.json")):
        try:
            transaction = json.loads(journal.read_text(encoding="utf-8"))
            plugin_id = transaction["id"]
            if (
                not isinstance(plugin_id, str)
                or not _ID.fullmatch(plugin_id)
                or journal.name != f".{plugin_id}.transaction.json"
            ):
                raise ValueError("Invalid transaction id")
            previous = transaction["registry"]
            if (
                not isinstance(previous, dict)
                or not isinstance(previous.get("plugins"), dict)
                or not isinstance(previous.get("dev"), dict)
            ):
                raise ValueError("Invalid transaction registry")
            live = plugins_dir / plugin_id
            old = plugins_dir / f"{plugin_id}.old"
            if transaction.get("committed") is True:
                if old.exists():
                    shutil.rmtree(old)
            else:
                if old.exists():
                    if live.exists():
                        shutil.rmtree(live)
                    os.replace(old, live)
                elif plugin_id not in previous["plugins"] and live.exists():
                    shutil.rmtree(live)
                if _read_registry(registry_path) != previous:
                    _write_json(registry_path, previous)
            journal.unlink()
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot recover plugin transaction {journal.name}") from exc


def recover_installs() -> None:
    """Finish or roll back interrupted swaps before reading the registry."""
    home = Path.home() / ".kollab"
    (home / "plugins").mkdir(parents=True, exist_ok=True)
    with locked_file(home / "plugins.json.lock", private=True):
        _recover_locked(home)


def install_stage(stage: Path, staged: dict, commit: str, digest: str, *, expected_record: dict | None = None) -> None:
    """Atomically swap a confirmed staged package into the user's plugin store."""
    plugin_id = staged.get("manifest", {}).get("id")
    if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id):
        raise ValueError("Invalid plugin id")
    home = Path.home() / ".kollab"
    plugins_dir = home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    registry_path = home / "plugins.json"
    live = plugins_dir / plugin_id
    old = plugins_dir / f"{plugin_id}.old"
    journal = plugins_dir / f".{plugin_id}.transaction.json"
    with locked_file(home / "plugins.json.lock", private=True):
        _recover_locked(home)
        verify_stage(stage, staged, commit, digest)
        registry = _read_registry(registry_path)
        prior = registry["plugins"].get(plugin_id)
        if expected_record is not None and prior != expected_record:
            raise ValueError("Plugin registry changed during approval; try again")
        if live.exists() != (prior is not None) or old.exists():
            raise ValueError("Plugin folder and registry disagree")
        transaction = {"id": plugin_id, "registry": registry, "committed": False}
        _write_json(journal, transaction)
        try:
            if live.exists():
                os.replace(live, old)
            os.replace(stage, live)
            now = datetime.now(timezone.utc).isoformat()
            record = {
                "source": staged["source"],
                "ref": staged["ref"],
                "commit": commit,
                "files_sha256": digest,
                "enabled": prior.get("enabled", True) if prior else True,
                "enabled_path": prior.get("enabled_path", os.environ.get("PATH", os.defpath))
                if prior
                else os.environ.get("PATH", os.defpath),
                "granted": staged["manifest"].get("uses", []),
                "added": prior.get("added", now) if prior else now,
                "updated": now,
            }
            next_registry = {**registry, "plugins": {**registry["plugins"], plugin_id: record}}
            _write_json(registry_path, next_registry)
            transaction["committed"] = True
            _write_json(journal, transaction)
            _recover_locked(home)
        except BaseException:
            # Leave the journal if recovery itself fails; next startup retries.
            try:
                _recover_locked(home)
            except Exception:
                logger.exception("Plugin installation rollback failed; recovery will retry")
            raise


def remove_package(plugin_id: str, *, expected_record: dict) -> None:
    """Remove an approved disabled package using the same recoverable journal as install."""
    if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id):
        raise ValueError("Invalid plugin id")
    home = Path.home() / ".kollab"
    plugins_dir = home / "plugins"
    registry_path = home / "plugins.json"
    live = plugins_dir / plugin_id
    old = plugins_dir / f"{plugin_id}.old"
    journal = plugins_dir / f".{plugin_id}.transaction.json"
    with locked_file(home / "plugins.json.lock", private=True):
        _recover_locked(home)
        registry = _read_registry(registry_path)
        prior = registry["plugins"].get(plugin_id)
        if prior != expected_record:
            raise ValueError("Plugin registry changed during approval; try again")
        if not isinstance(prior, dict):
            raise ValueError("Plugin is not installed")
        if live.is_symlink() or not live.is_dir() or old.exists() or old.is_symlink():
            raise ValueError("Plugin folder and registry disagree")
        transaction = {"id": plugin_id, "registry": registry, "committed": False}
        _write_json(journal, transaction)
        try:
            os.replace(live, old)
            next_plugins = dict(registry["plugins"])
            del next_plugins[plugin_id]
            _write_json(registry_path, {**registry, "plugins": next_plugins})
            transaction["committed"] = True
            _write_json(journal, transaction)
            _recover_locked(home)
        except Exception:
            _recover_locked(home)
            raise
