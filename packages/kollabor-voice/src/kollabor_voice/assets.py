"""Verified, resumable model cache. Only the singleton installer writes it."""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
from pathlib import Path

from .control import manifest


def verified(path: Path, item: dict) -> bool:
    if not path.is_file() or path.stat().st_size != item["size"]:
        return False
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest() == item["sha256"]


def download(url: str, destination: Path, item: dict, progress) -> None:
    """Resume only when the server honors the exact requested byte range."""
    partial = destination.with_suffix(destination.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset >= item["size"]:
        if verified(partial, item):
            partial.replace(destination)
            return
        offset = 0
    headers = {"User-Agent": "Kollab-Voice/1"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        resumed = offset and response.status == 206
        if resumed and not response.headers.get("Content-Range", "").startswith(
            f"bytes {offset}-"
        ):
            raise RuntimeError("Download returned the wrong byte range")
        if not resumed:
            offset = 0
        with partial.open("ab" if resumed else "wb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
                offset += len(chunk)
                if offset > item["size"]:
                    raise RuntimeError(f"Unexpected size for {item['name']}")
                progress(offset, item["size"])
    if not verified(partial, item):
        # Keep the previous published model intact. A corrupt partial can restart.
        raise RuntimeError(
            f"Checksum mismatch for {item['name']}; run /voicemode retry"
        )
    partial.replace(destination)


def ensure_models(root: Path, progress, bundles=None) -> dict[str, Path]:
    paths = {}
    for key, bundle in (bundles or manifest()["bundles"]).items():
        target = root / "models" / key / bundle["revision"]
        target.mkdir(parents=True, exist_ok=True)
        for item in bundle["files"]:
            path = target / item["name"]
            path.parent.mkdir(parents=True, exist_ok=True)
            progress(state="checking", detail=f"Verifying {key} / {item['name']}")
            if verified(path, item):
                continue
            # Reuse old downloaded base weights only after verifying identity.
            legacy = (
                Path.home()
                / ".kollab/projects/default/voice/models/stt-base/main"
                / item["name"]
            )
            if key == "whisper-base" and verified(legacy, item):
                partial = path.with_suffix(path.suffix + ".part")
                shutil.copyfile(legacy, partial)
                partial.replace(path)
                continue
            if bundle.get("hf_cache"):
                cached = (
                    Path.home()
                    / ".cache/huggingface/hub"
                    / bundle["hf_cache"]
                    / item["name"]
                )
                if verified(cached, item):
                    partial = path.with_suffix(path.suffix + ".part")
                    shutil.copyfile(cached, partial)
                    partial.replace(path)
                    continue
            download(
                bundle["base_url"] + item["name"],
                path,
                item,
                lambda done, total, key=key, item=item: progress(
                    state="downloading",
                    detail=f"{key} / {item['name']}",
                    downloaded=done,
                    total=total,
                ),
            )
        paths[key] = target
    return paths
