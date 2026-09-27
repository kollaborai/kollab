"""Build a narrow publisher artifact from exact source files, without credentials."""

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path

FILES = (
    "plugins/__init__.py",
    "plugins/hub/__init__.py",
    "plugins/hub/dns/__init__.py",
    "plugins/hub/dns/models.py",
    "plugins/hub/dns/storage.py",
    "plugins/hub/dns/discovery.py",
    "plugins/hub/dns/discovery_publish.py",
    "scripts/discovery/requirements.txt",
)


def build(output: Path):
    root = Path(__file__).resolve().parents[2]
    hashes = {}
    with tarfile.open(output, "x:gz") as archive:
        for name in FILES:
            data = (root / name).read_bytes()
            hashes[name] = hashlib.sha256(data).hexdigest()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            archive.addfile(info, io.BytesIO(data))
        data = json.dumps(hashes, indent=2, sort_keys=True).encode()
        info = tarfile.TarInfo("source-sha256.json")
        info.size, info.mode = len(data), 0o644
        archive.addfile(info, io.BytesIO(data))
    print(f"{output}: sha256={hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)
