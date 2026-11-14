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
    if args.action in {"add", "enable", "disable", "dev"} and not sys.stdin.isatty():
        raise ValueError("Plugin management requires interactive confirmation")
    if args.action in {"enable", "disable"}:
        from .settings import set_enabled

        print(f"{args.action.title()} {args.plugin_id}? Plugin code runs as your user.")
        if input("Confirm? [yes/no] ").strip().lower() != "yes":
            return 1
        set_enabled(args.plugin_id, args.action == "enable")
        return 0
    if args.action == "dev":
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
                del registry["dev"][args.plugin_id]
                _write_json(home / "plugins.json", registry)
                print(f"Detached {args.plugin_id}@dev; development data retained")
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
                if old_values.is_file() and not (target / "values.json").exists():
                    write_text_atomic(target / "values.json", old_values.read_text(encoding="utf-8"))
            registry["dev"][plugin_id] = str(folder)
            _write_json(home / "plugins.json", registry)
            print(f"Attached {plugin_id}@dev from {folder}; local code runs as your user")
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
