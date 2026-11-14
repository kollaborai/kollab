"""Capability-scoped context supplied to an engine plugin router."""

import json
from pathlib import Path
from typing import Callable

from kollabor_config.file_lock import locked_file

from .packages import _SERVICE
from .settings import _valid_data_id, save, setting_widgets


class _Settings:
    def __init__(self, context):
        self.context = context

    def get(self):
        values = {
            row["config_path"]: row.get("default")
            for row in setting_widgets(self.context.root, self.context.manifest)
            if isinstance(row, dict) and isinstance(row.get("config_path"), str)
        }
        path = self.context.data / "values.json"
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid plugin settings") from exc
            if not isinstance(saved, dict):
                raise ValueError("Invalid plugin settings")
            values.update(saved)
        return values

    def save(self, values):
        save(self.context.id, values)


class _Secrets:
    def __init__(self, grants):
        self.grants = frozenset(grants)

    def get(self, name):
        if not isinstance(name, str) or f"secret:{name}" not in self.grants:
            raise PermissionError("Plugin does not have this secret grant")
        from kollabor_ai.profile_manager import _keyring_get

        return _keyring_get(name)


class _Agents:
    async def run(self, *args, **kwargs):
        raise NotImplementedError("Plugin agent runs are not available until step 4")


class _Events:
    def __init__(self, plugin_id, publish):
        self.plugin_id = plugin_id
        self._publish = publish

    def publish(self, topic, data):
        if self._publish is None:
            raise RuntimeError("Plugin event publisher is unavailable")
        return self._publish(self.plugin_id, topic, data)


class _Services:
    def __init__(self, context):
        self.context = context

    def status(self):
        from .registry import _service_entries

        return _service_entries(Path.home() / ".kollab", self.context.id, self.context.manifest)


class PluginContext:
    def __init__(
        self,
        plugin_id: str,
        root: Path,
        data: Path,
        manifest: dict,
        *,
        grants=(),
        publish: Callable | None = None,
        notify: Callable | None = None,
    ):
        if not _valid_data_id(plugin_id):
            raise ValueError("Invalid plugin id")
        self.id = plugin_id
        self.root = Path(root)
        self.data = Path(data)
        self.manifest = manifest
        self.settings = _Settings(self)
        self.secrets = _Secrets(grants)
        self.events = _Events(plugin_id, publish)
        self.services = _Services(self)
        self.agents = _Agents()
        self._notify = notify

    def notify(self, text, button=None):
        if self._notify is None:
            raise RuntimeError("Plugin notification publisher is unavailable")
        return self._notify(self.id, text, button)

    def lock(self, name):
        if not isinstance(name, str) or not _SERVICE.fullmatch(name):
            raise ValueError("Invalid plugin lock name")
        directory = self.data / "locks"
        if directory.is_symlink():
            raise ValueError("Plugin lock directory cannot be a symlink")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink():
            raise ValueError("Plugin lock directory cannot be a symlink")
        return locked_file(directory / f"{name}.lock", private=True)

    @staticmethod
    def local_only(endpoint):
        endpoint.__kollab_local_only__ = True
        return endpoint
