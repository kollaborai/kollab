"""Package the headless Kollab service from an explicit source allowlist.

Pass the same --frontend-dist directory to scripts/build_binary.py to bundle
one prebuilt frontend into the service and installed CLI/desktop artifacts.
"""

import argparse
import hashlib
import importlib.util
import io
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path

# Load the standalone helper by path: this script runs without kollab installed.
_spec = importlib.util.spec_from_file_location(
    "_kollabor_build_info", Path(__file__).resolve().parents[2] / "kollabor/build_info.py"
)
_build_info = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _build_info
_spec.loader.exec_module(_build_info)
BuildProvenance = _build_info.BuildProvenance
read_embedded_provenance = _build_info.read_embedded_provenance
resolve_build_provenance = _build_info.resolve_build_provenance
write_embedded_provenance = _build_info.write_embedded_provenance

FILES = (
    "kollabor_cli_main.py",
    "pyproject.toml",
    "kollabor/__init__.py",
    "kollabor/version.py",
    "kollabor/build_info.py",
    "kollabor/binary.py",
    "plugins/__init__.py",
    "plugins/hub/__init__.py",
    "plugins/hub/relay_service.py",
    "plugins/hub/relay_backend.py",
    "plugins/hub/relay_runtime.py",
    "plugins/hub/relay_selfhost.py",
    "plugins/hub/relay_client.py",
    "plugins/hub/phone_portal.py",
    "plugins/hub/relay_state.py",
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


def build(
    output: Path, *, frontend_dist: Path | None = None, provenance: BuildProvenance | None = None
) -> None:
    root = Path(__file__).resolve().parents[2]
    # Read all sources before opening the artifact; a missing dependency must
    # not leave a partially constructed release that could be deployed.
    sources = {name: (root / name).read_bytes() for name in FILES}
    # The artifact has no .git, so carry the checkout identity as generated data.
    generated = root / "kollabor/_build_info.py"
    identity = provenance or resolve_build_provenance(
        root, inherited=read_embedded_provenance(generated)
    )
    with tempfile.TemporaryDirectory() as scratch:
        write_embedded_provenance(Path(scratch) / "_build_info.py", identity)
        sources["kollabor/_build_info.py"] = (Path(scratch) / "_build_info.py").read_bytes()
    selected = os.environ.get("KOLLAB_FRONTEND_DIST")
    if frontend_dist is None and selected is not None:
        if not selected:
            raise ValueError("KOLLAB_FRONTEND_DIST must name a built frontend directory")
        frontend_dist = Path(selected)
    frontend = frontend_dist or root / "packages/kollabor-webui/frontend/dist"
    if frontend.is_symlink():
        raise ValueError("PhoneLink browser assets must not be symlinks")
    loader = frontend / "phonelink" / "loader"
    if not (loader / "index.html").is_file():
        raise ValueError("Build the Kollab frontend before packaging a PhoneLink-capable directory")
    # The directory carries only the small loader, never the phone app itself.
    for asset in sorted(loader.rglob("*")):
        if asset.is_symlink():
            raise ValueError("PhoneLink browser assets must not be symlinks")
        if asset.is_file():
            sources["plugins/hub/phone_static/" + asset.relative_to(loader).as_posix()] = asset.read_bytes()
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
    parser.add_argument("--frontend-dist", type=Path, help="Use an isolated built frontend directory")
    args = parser.parse_args()
    build(args.output, frontend_dist=args.frontend_dist)
