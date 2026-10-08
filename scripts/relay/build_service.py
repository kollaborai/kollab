"""Package the headless Kollab service from an explicit source allowlist."""

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path

FILES = (
    "kollabor_cli_main.py",
    "plugins/__init__.py",
    "plugins/hub/__init__.py",
    "plugins/hub/relay_service.py",
    "plugins/hub/relay_backend.py",
    "plugins/hub/relay_runtime.py",
    "plugins/hub/relay_selfhost.py",
    "plugins/hub/relay_client.py",
    "plugins/hub/relay_state.py",
    "plugins/hub/relay_commands.py",
    "plugins/hub/device_names.py",
    "plugins/hub/knock_wire.py",
    "plugins/hub/dns/__init__.py",
    "plugins/hub/dns/models.py",
    "plugins/hub/dns/storage.py",
    "plugins/hub/dns/discovery.py",
    "plugins/hub/dns/discovery_store.py",
    "plugins/hub/dns/discovery_publish.py",
    "scripts/relay/requirements.txt",
    "scripts/relay/peer_probe.py",
    "scripts/relay/measure_capacity.py",
)


def build(output: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    # Read all sources before opening the artifact; a missing dependency must
    # not leave a partially constructed release that could be deployed.
    sources = {name: (root / name).read_bytes() for name in FILES}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
    sources["source-sha256.json"] = json.dumps(hashes, indent=2, sort_keys=True).encode()
    with tarfile.open(output, "x:gz") as archive:
        for name, data in sources.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            archive.addfile(info, io.BytesIO(data))
    print(f"{output}: sha256={hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)
