"""Write a file whole, so a reader never sees half of it."""

import os
import shutil
import tempfile
from pathlib import Path
from typing import Union


def write_text_atomic(path: Union[str, Path], text: str) -> None:
    """Replace ``path`` with ``text`` by renaming a finished sibling file over it.

    Opening the target with "w" empties it first, and a second kollab that reads
    config.json in that moment finds nothing, starts from a blank config and saves
    that over the user's profiles and keys. A rename is atomic: a reader sees the
    old file or the new one. A symlinked target (a dotfiles setup) is written
    through, so it stays a link. The file is 0600 (mkstemp's mode): API keys live
    in these files.
    """
    target = Path(os.path.realpath(path))
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def keep_unreadable_copy(path: Union[str, Path]) -> None:
    """Copy a config file that could not be parsed to ``<name>.corrupt``.

    Called before a fresh config replaces it, so a typo in a hand edit does not
    cost the user their profiles and keys.
    """
    try:
        shutil.copy2(path, f"{path}.corrupt")
    except OSError:
        pass
