"""Private token storage shared by the engine and plugin service wrappers."""

import os
import secrets
import stat
from pathlib import Path

from kollabor_config.atomic_write import write_text_atomic
from kollabor_config.file_lock import locked_file


class PluginTokenFiles:
    """Stable, private plugin tokens; reading never creates credentials."""

    @staticmethod
    def _run(context, create: bool) -> Path:
        data_root = Path.home() / ".kollab" / "plugin-data"
        data = data_root / context.id
        if context.data != data:
            raise ValueError("Invalid plugin data directory")
        run = data / "run"
        for path in (data_root, data, run):
            if path.is_symlink():
                raise ValueError("Plugin token directory cannot be a symlink")
        if create:
            run.mkdir(parents=True, exist_ok=True, mode=0o700)
            for path in (data_root, data, run):
                if path.is_symlink():
                    raise ValueError("Plugin token directory cannot be a symlink")
        return run

    @staticmethod
    def _read(path: Path) -> str:
        if path.is_symlink():
            raise ValueError("Plugin token cannot be a symlink")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise ValueError("Plugin token file must be private")
            raw = os.read(descriptor, 128)
        finally:
            os.close(descriptor)
        try:
            token = raw.decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Invalid plugin token") from exc
        if len(token) != 43 or not all(ch.isascii() and (ch.isalnum() or ch in "-_") for ch in token):
            raise ValueError("Invalid plugin token")
        return token

    def mint(self, context) -> str:
        run = self._run(context, create=True)
        path = run / "token"
        with locked_file(run / "token.lock", private=True):
            if path.is_symlink():
                raise ValueError("Plugin token cannot be a symlink")
            if not path.exists():
                write_text_atomic(path, secrets.token_urlsafe(32))
            return self._read(path)
