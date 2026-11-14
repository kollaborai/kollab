"""Headless plugin management commands."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from kollabor_config.atomic_write import write_text_atomic
from kollabor_config.file_lock import locked_file

from .packages import (
    _ID,
    _SERVICE,
    _read_registry,
    _recover_locked,
    _write_json,
    install_stage,
    recover_installs,
    stage_git_source,
    validate_manifest,
)


def _stop_for_change(home: Path, plugin_id: str, services: dict) -> None:
    """Stop verified wrappers after approval and wait for their process groups."""
    import time

    from .services import _recorded_process_alive, control_service

    data = home / "plugin-data" / plugin_id
    run_dir = data / "run"
    if any(path.is_symlink() for path in (home / "plugin-data", data, run_dir)):
        raise ValueError("Plugin data path may not be a symlink")
    if not isinstance(services, dict) or any(
        not isinstance(name, str) or not _SERVICE.fullmatch(name) for name in services
    ):
        raise ValueError("Invalid installed service declarations")
    unknown = [pid for pid in run_dir.glob("*.pid") if pid.stem not in services and _recorded_process_alive(pid)]
    if unknown:
        raise ValueError("An unrecognized plugin service is still running")
    active = [name for name in services if _recorded_process_alive(run_dir / f"{name}.pid")]
    for name in active:
        control_service(plugin_id, name, "stop")
    timeout = max((float(services[name].get("stop_timeout_s", 30)) for name in active), default=0) + 5
    deadline = time.monotonic() + timeout
    while any(_recorded_process_alive(run_dir / f"{name}.pid") for name in active):
        if time.monotonic() >= deadline:
            raise ValueError("Plugin services did not stop in time")
        time.sleep(0.05)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kollab plugin")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list", help="List installed plugins")
    for action in ("enable", "disable"):
        command = subparsers.add_parser(action, help=f"{action.title()} an installed plugin")
        command.add_argument("plugin_id")
    logs = subparsers.add_parser("logs", help="Show plugin service logs")
    logs.add_argument("plugin_id")
    logs.add_argument("service", nargs="?")
    new = subparsers.add_parser("new", help="Create a plugin from the hello template")
    new.add_argument("plugin_id")
    remove = subparsers.add_parser("remove", help="Remove an installed plugin")
    remove.add_argument("plugin_id")
    remove.add_argument("--purge", action="store_true")
    update = subparsers.add_parser("update", help="Update an installed Git plugin")
    update.add_argument("plugin_id")
    add = subparsers.add_parser("add", help="Install a Git plugin after confirmation")
    add.add_argument("source")
    dev = subparsers.add_parser("dev", help="Attach a local development checkout")
    dev.add_argument("folder_or_off")
    dev.add_argument("plugin_id", nargs="?")
    run = subparsers.add_parser("run-service", help=argparse.SUPPRESS)
    run.add_argument("plugin_id")
    run.add_argument("service")
    run.add_argument("nonce")
    args = parser.parse_args(argv)
    if args.action == "run-service":
        from .services import run_service

        return run_service(args.plugin_id, args.service, args.nonce)
    if args.action == "logs":
        if not _ID.fullmatch(args.plugin_id):
            raise ValueError("Invalid plugin id")
        home = Path.home() / ".kollab"
        record = _read_registry(home / "plugins.json")["plugins"].get(args.plugin_id)
        if not isinstance(record, dict):
            raise ValueError("Plugin is not installed")
        root = home / "plugins" / args.plugin_id
        if root.is_symlink():
            raise ValueError("Plugin directory may not be a symlink")
        manifest = json.loads((root / "kollab-plugin.json").read_text(encoding="utf-8"))
        services = manifest.get("services", {})
        if not isinstance(services, dict):
            raise ValueError("Invalid plugin services")
        names = [args.service] if args.service else sorted(services)
        if any(not _SERVICE.fullmatch(name) or name not in services for name in names):
            raise ValueError("Unknown or invalid service")
        run = home / "plugin-data" / args.plugin_id / "run"
        if any(path.is_symlink() for path in (home / "plugin-data", run.parent, run)):
            raise ValueError("Plugin data path may not be a symlink")
        for name in names:
            log = run / f"{name}.log"
            if not log.exists() and not log.is_symlink():
                print(f"{name}: no log yet")
                continue
            fd = os.open(log, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as stream:
                print(f"--- {name} ---")
                print(stream.read(10 * 1024 * 1024), end="")
        return 0
    if args.action == "new":
        import shutil

        from kollabor_config.config_utils import _get_bundled_dir

        if not _ID.fullmatch(args.plugin_id):
            raise ValueError("Invalid plugin id")
        bundle = _get_bundled_dir("plugins")
        if bundle is None or not (bundle / "hello").is_dir():
            raise ValueError("Bundled hello plugin is not available")
        target = Path.cwd() / args.plugin_id
        shutil.copytree(bundle / "hello", target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        manifest_path = target / "kollab-plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["id"] = args.plugin_id
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Created {target}")
        return 0
    if args.action in {"add", "update", "remove", "enable", "disable", "dev"} and not sys.stdin.isatty():
        raise ValueError("Plugin management requires interactive confirmation")
    if args.action in {"enable", "disable"}:
        from .settings import set_enabled

        print(f"{args.action.title()} {args.plugin_id}? Plugin code runs as your user.")
        if input("Confirm? [yes/no] ").strip().lower() != "yes":
            return 1
        set_enabled(args.plugin_id, args.action == "enable")
        print(f"{'Enabled' if args.action == 'enable' else 'Disabled'} {args.plugin_id}")
        return 0
    if args.action == "dev":
        if args.folder_or_off == "off":
            if not args.plugin_id or not _ID.fullmatch(args.plugin_id):
                raise ValueError("A valid plugin id is required")
            print(f"Detach {args.plugin_id}@dev? Staged settings will be discarded; other data is retained.")
        else:
            import shlex

            from kollabor.version import get_runtime_version

            if args.plugin_id is not None:
                raise ValueError("Unexpected extra argument")
            folder = Path(args.folder_or_off).expanduser().resolve(strict=True)
            manifest = validate_manifest(folder, get_runtime_version())
            print(f"Plugin: {manifest['name']} ({manifest['id']}@dev)")
            print(f"Local folder: {folder}")
            print("Uses: " + (", ".join(manifest.get("uses", [])) or "none"))
            for name, service in manifest.get("services", {}).items():
                print(f"Process {name}: {shlex.join(service['command'])}")
                print("  Secrets: " + (", ".join(service.get("secrets", [])) or "none"))
        print("Development code is unpinned and runs as your user with your computer's permissions.")
        if input("Continue? [yes/no] ").strip().lower() != "yes":
            return 1
    recover_installs()
    home = Path.home() / ".kollab"
    registry = _read_registry(home / "plugins.json")
    if args.action == "dev":
        with locked_file(home / "plugins.json.lock", private=True):
            _recover_locked(home)
            registry = _read_registry(home / "plugins.json")
            if args.folder_or_off == "off":
                if not args.plugin_id or not _ID.fullmatch(args.plugin_id):
                    raise ValueError("A valid plugin id is required")
                if args.plugin_id not in registry["dev"]:
                    raise ValueError("Development plugin is not attached")
                dev_data = home / "plugin-data" / f"{args.plugin_id}@dev"
                with locked_file(dev_data / "values.json.lock", private=True):
                    del registry["dev"][args.plugin_id]
                    _write_json(home / "plugins.json", registry)
                    (dev_data / "values.json").unlink(missing_ok=True)
                print(f"Detached {args.plugin_id}@dev; staged settings discarded")
                return 0
            if args.plugin_id is not None:
                raise ValueError("Unexpected extra argument")
            from kollabor.version import get_runtime_version

            folder = Path(args.folder_or_off).expanduser().resolve(strict=True)
            manifest = validate_manifest(folder, get_runtime_version())
            plugin_id = manifest["id"]
            target = home / "plugin-data" / f"{plugin_id}@dev"
            target.mkdir(parents=True, exist_ok=True)
            with locked_file(target / "values.json.lock", private=True):
                old_values = home / "plugin-data" / plugin_id / "values.json"
                if not (target / "values.json").exists() and old_values.parent.is_dir():
                    with locked_file(old_values.parent / "values.json.lock", private=True):
                        if old_values.is_file():
                            from .settings import _read_json

                            source_values = _read_json(old_values)
                            write_text_atomic(target / "values.json", json.dumps(source_values, indent=2) + "\n")
            registry["dev"][plugin_id] = str(folder)
            _write_json(home / "plugins.json", registry)
            print(f"Attached {plugin_id}@dev from {folder}; local code runs as your user")
            return 0
    if args.action == "remove":
        import shutil

        from kollabor_config.file_lock import try_lock, unlock

        from .packages import remove_package
        from .services import _recorded_process_alive

        plugin_id = args.plugin_id
        if not _ID.fullmatch(plugin_id):
            raise ValueError("Invalid plugin id")
        prior = registry["plugins"].get(plugin_id)
        if not isinstance(prior, dict):
            raise ValueError("Plugin is not installed")
        root = home / "plugins" / plugin_id
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Invalid installed plugin folder")
        manifest = json.loads((root / "kollab-plugin.json").read_text(encoding="utf-8"))
        name = manifest.get("name", plugin_id)
        services = manifest.get("services", {})
        if not isinstance(services, dict):
            raise ValueError("Invalid installed service declarations")
        print(f"Remove {name} ({plugin_id})? Plugin data will be kept unless purged.")
        if input("Remove this plugin? [yes/no] ").strip().lower() != "yes":
            return 1
        if args.purge and input(f"Delete {name}'s data folder permanently? [yes/no] ").strip().lower() != "yes":
            return 1
        _stop_for_change(home, plugin_id, services)
        data = home / "plugin-data" / plugin_id
        run_dir = data / "run"
        if any(path.is_symlink() for path in (home / "plugin-data", data, run_dir)):
            raise ValueError("Plugin data path may not be a symlink")
        run_dir.mkdir(parents=True, exist_ok=True)
        names = set(services) | {p.stem for p in run_dir.glob("*.pid")}
        if any(not isinstance(item, str) or not _SERVICE.fullmatch(item) for item in names):
            raise ValueError("Invalid installed service name")
        locks = []
        try:
            for service_name in sorted(names):
                fd = os.open(
                    run_dir / f"{service_name}.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
                )
                if not try_lock(fd):
                    os.close(fd)
                    raise ValueError("A plugin service is running; stop it and try again")
                locks.append(fd)
                if _recorded_process_alive(run_dir / f"{service_name}.pid"):
                    raise ValueError("A plugin service is running; stop it and try again")
            remove_package(plugin_id, expected_record=prior)
        finally:
            for fd in reversed(locks):
                unlock(fd)
                os.close(fd)
        if args.purge:
            if data.is_symlink() or (home / "plugin-data").is_symlink():
                raise ValueError("Plugin removed, but data path changed; data was not purged")
            shutil.rmtree(data)
        print(f"Removed {plugin_id}" + (" and purged its data" if args.purge else ""))
        return 0

    if args.action == "update":
        import hashlib
        import shlex

        from kollabor.version import get_runtime_version

        from .services import _recorded_process_alive

        plugin_id = args.plugin_id
        if not _ID.fullmatch(plugin_id):
            raise ValueError("Invalid plugin id")
        prior = registry["plugins"].get(plugin_id)
        if not isinstance(prior, dict) or not isinstance(prior.get("source"), str):
            raise ValueError("Plugin is not installed from Git")
        root = home / "plugins" / plugin_id
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Invalid installed plugin folder")
        run_dir = home / "plugin-data" / plugin_id / "run"
        source = prior["source"]
        if prior.get("ref"):
            source += "#" + prior["ref"]
        with tempfile.TemporaryDirectory(prefix=".plugin-update-", dir=home / "plugins") as temporary:
            stage = Path(temporary) / "package"
            staged = stage_git_source(source, stage, get_runtime_version())
            manifest = staged["manifest"]
            if manifest["id"] != plugin_id:
                raise ValueError("Updated plugin id does not match installed plugin id")

            def snapshot(folder):
                files = {}
                for path in folder.rglob("*"):
                    if path.is_symlink():
                        raise ValueError("Plugin folder contains a symlink")
                    if path.is_file():
                        with path.open("rb") as stream:
                            files[path.relative_to(folder).as_posix()] = hashlib.file_digest(
                                stream, "sha256"
                            ).hexdigest()
                return files

            before, after = snapshot(root), snapshot(stage)
            changed = sorted(
                {str(Path(name).parent) for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
            )
            print(f"Plugin: {manifest['name']} ({plugin_id})")
            print(f"Source: {staged['source']}")
            print(f"Commit: {prior.get('commit', 'unknown')}..{staged['commit']}")
            print(f"Files SHA-256: {staged['files_sha256']}")
            print("Changed folders: " + (", ".join(changed) or "none"))
            print("Uses: " + (", ".join(manifest.get("uses", [])) or "none"))
            for name, service in manifest.get("services", {}).items():
                print(f"Process {name}: {shlex.join(service['command'])}")
                print("  Secrets: " + (", ".join(service.get("secrets", [])) or "none"))
            print("Plugin code runs as your user and can use your computer outside Kollab.")
            if input("Update this plugin? [yes/no] ").strip().lower() != "yes":
                return 1
            from kollabor_config.file_lock import try_lock, unlock

            if any(path.is_symlink() for path in (home / "plugin-data", run_dir.parent, run_dir)):
                raise ValueError("Plugin data path may not be a symlink")
            run_dir.mkdir(parents=True, exist_ok=True)
            old_manifest = json.loads((root / "kollab-plugin.json").read_text(encoding="utf-8"))
            old_services = old_manifest.get("services", {})
            if not isinstance(old_services, dict):
                raise ValueError("Invalid installed service declarations")
            _stop_for_change(home, plugin_id, old_services)
            names = set(old_services) | set(manifest.get("services", {})) | {p.stem for p in run_dir.glob("*.pid")}
            if any(not isinstance(name, str) or not _SERVICE.fullmatch(name) for name in names):
                raise ValueError("Invalid installed service name")
            locks = []
            try:
                for name in sorted(names):
                    fd = os.open(run_dir / f"{name}.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
                    if not try_lock(fd):
                        os.close(fd)
                        raise ValueError("A plugin service is running; stop it and try again")
                    locks.append(fd)
                    if _recorded_process_alive(run_dir / f"{name}.pid"):
                        raise ValueError("A plugin service is running; stop it and try again")
                install_stage(stage, staged, staged["commit"], staged["files_sha256"], expected_record=prior)
            finally:
                for fd in reversed(locks):
                    unlock(fd)
                    os.close(fd)
            print(f"Updated {plugin_id}")
            return 0

    if args.action == "add":
        if not sys.stdin.isatty():
            raise ValueError("Plugin installation requires interactive confirmation")
        from kollabor.version import get_runtime_version

        with tempfile.TemporaryDirectory(prefix=".plugin-add-", dir=home / "plugins") as temporary:
            stage = Path(temporary) / "package"
            staged = stage_git_source(args.source, stage, get_runtime_version())
            manifest = staged["manifest"]
            if manifest["id"] in registry["plugins"]:
                raise ValueError("Plugin is already installed; use update")
            print(f"Plugin: {manifest['name']} ({manifest['id']})")
            print(f"Source: {staged['source']}")
            print(f"Commit: {staged['commit']}")
            print(f"Files SHA-256: {staged['files_sha256']}")
            print("Uses: " + (", ".join(manifest.get("uses", [])) or "none"))
            print(
                "Parts: "
                + (", ".join(key for key in ("python", "settings", "web", "services") if manifest.get(key)) or "none")
            )
            for name, service in manifest.get("services", {}).items():
                import shlex

                print(f"Process {name}: {shlex.join(service['command'])}")
                print("  Secrets: " + (", ".join(service.get("secrets", [])) or "none"))
            print("Plugin code runs as your user and can use your computer outside Kollab.")
            if input("Install this exact version? [yes/no] ").strip().lower() != "yes":
                return 1
            install_stage(stage, staged, staged["commit"], staged["files_sha256"])
            print(f"Installed {manifest['id']}")
            return 0
    for plugin_id, record in sorted(registry["plugins"].items()):
        if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id) or not isinstance(record, dict):
            print(f"{plugin_id}  invalid registry entry")
            continue
        status = "enabled" if record.get("enabled") else "disabled"
        try:
            root = home / "plugins" / plugin_id
            if root.is_symlink():
                raise ValueError("Plugin directory is a symlink")
            manifest = json.loads((root / "kollab-plugin.json").read_text(encoding="utf-8"))
            version = manifest.get("version", "unknown")
            services = manifest.get("services", {})
            processes = ", ".join(sorted(services)) if isinstance(services, dict) else "invalid"
            print(f"{plugin_id}  {version}  {status}  {processes or 'no processes'}  {record.get('source', 'unknown')}")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            print(f"{plugin_id}  {status}  manifest unavailable: {exc}")
    for plugin_id, folder in sorted(registry["dev"].items()):
        print(f"{plugin_id}@dev  dev  {folder}")
    return 0
