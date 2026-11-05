#!/usr/bin/env python3
"""Validate immutable release inputs and publish verified artifacts without rebuilding."""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

try:
    from scripts.desktop_release import verify_manifest
    from scripts.publish import PACKAGES
except ModuleNotFoundError:  # Direct execution from scripts/.
    from desktop_release import verify_manifest
    from publish import PACKAGES

REPOSITORY = "kollaborai/kollab"
API = f"https://api.github.com/repos/{REPOSITORY}"
SHA = re.compile(r"[0-9a-f]{40}")
TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


class NoRedirect(HTTPRedirectHandler):
    """API calls must not carry credentials to a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def version(tag: str) -> str:
    if not TAG.fullmatch(tag):
        raise ValueError("release tag must be vX.Y.Z with no leading zeroes")
    return tag[1:]


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def provenance(repo: Path, tag: str, workflow_ref: str, controller: str) -> dict[str, str]:
    release_version = version(tag)
    if workflow_ref != "refs/heads/main" or not SHA.fullmatch(controller):
        raise ValueError("release controller must be a full commit SHA on refs/heads/main")
    tag_ref = f"refs/tags/{tag}"
    if git(repo, "cat-file", "-t", tag_ref) != "tag":
        raise ValueError("release tag must be annotated")
    source = git(repo, "rev-parse", "--verify", tag_ref + "^{commit}")
    for commit in (controller, source):
        subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, "origin/main"],
            check=True,
        )
    return {
        "tag": tag,
        "version": release_version,
        "source_sha": source,
        "tag_object": git(repo, "rev-parse", tag_ref),
        "controller_sha": controller,
    }


def request_json(
    url: str, *, method: str = "GET", data: dict | bytes | None = None, token: str = "", missing: bool = False
):
    headers = {"User-Agent": "Kollab-release-publisher", "Cache-Control": "no-cache"}
    if url.startswith("https://api.github.com/") or url.startswith("https://uploads.github.com/"):
        headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        if token:
            headers["Authorization"] = f"Bearer {token}"
    if isinstance(data, dict):
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    else:
        body = data
        if body is not None:
            headers["Content-Type"] = "application/octet-stream"
    try:
        with build_opener(NoRedirect()).open(
            Request(url, data=body, method=method, headers=headers), timeout=120
        ) as response:
            return json.load(response)
    except HTTPError as error:
        if missing and error.code == 404:
            return None
        # Never include response bodies, request headers, or credential values.
        raise ValueError(f"release API request failed: HTTP {error.code}") from None


def check_remote_tag(tag: str, source: str, tag_object: str) -> None:
    version(tag)
    if not SHA.fullmatch(source) or not SHA.fullmatch(tag_object):
        raise ValueError("release source and tag object must be full commit IDs")
    token = os.environ.get("GH_TOKEN", "")
    ref = request_json(f"{API}/git/ref/tags/{quote(tag, safe='')}", token=token)["object"]
    if ref.get("type") != "tag" or ref.get("sha") != tag_object:
        raise ValueError("release tag object changed after validation")
    obj = request_json(f"{API}/git/tags/{tag_object}", token=token)["object"]
    if obj.get("type") != "commit" or obj.get("sha") != source:
        raise ValueError("release source changed after validation")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package_files(directory: Path, release_version: str) -> dict[str, list[Path]]:
    version("v" + release_version)
    expected = {}
    for name in [*PACKAGES, "kollab"]:
        base = f"{name.replace('-', '_')}-{release_version}"
        expected[name] = [directory / (base + suffix) for suffix in ("-py3-none-any.whl", ".tar.gz")]
    paths = [path for pair in expected.values() for path in pair]
    if {p.name for p in directory.iterdir()} != {p.name for p in paths}:
        raise ValueError("expected exactly one wheel and sdist for each of the ten published packages")
    for name, pair in expected.items():
        for path in pair:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"distribution must be an ordinary file: {path.name}")
            if path.suffix == ".whl":
                with zipfile.ZipFile(path) as archive:
                    members = [x for x in archive.infolist() if x.filename.endswith(".dist-info/METADATA")]
                    if len(members) != 1 or members[0].file_size > 2_000_000:
                        raise ValueError(f"invalid wheel metadata: {path.name}")
                    metadata = archive.read(members[0])
            else:
                with tarfile.open(path) as archive:
                    members = [
                        x for x in archive.getmembers() if len(Path(x.name).parts) == 2 and x.name.endswith("/PKG-INFO")
                    ]
                    if len(members) != 1 or not members[0].isfile() or members[0].size > 2_000_000:
                        raise ValueError(f"invalid sdist metadata: {path.name}")
                    metadata = archive.extractfile(members[0]).read()
            fields = email.parser.BytesParser().parsebytes(metadata)
            # Core Metadata names may use either standardized separator.
            names = fields.get_all("Name", [])
            if (
                len(names) != 1
                or re.sub(r"[-_.]+", "-", names[0]).lower() != name
                or fields.get_all("Version") != [release_version]
            ):
                raise ValueError(f"distribution metadata differs from release: {path.name}")
    return expected


def pypi_files(name: str, release_version: str) -> dict[str, str]:
    result = request_json(f"https://pypi.org/pypi/{name}/{release_version}/json", missing=True)
    if result is None:
        return {}
    records = result["urls"]
    files = {item["filename"]: item["digests"]["sha256"] for item in records}
    if len(files) != len(records):
        raise ValueError(f"duplicate remote PyPI filename for {name}")
    return files


def pypi_plan(directory: Path, release_version: str, upload: Path) -> dict[str, str]:
    """Check the entire release before staging any missing files; never skip a mismatch."""
    packages = package_files(directory, release_version)
    pending = []
    for name, paths in packages.items():
        remote = pypi_files(name, release_version)
        if remote.keys() - {p.name for p in paths}:
            raise ValueError(f"unexpected published files for {name}")
        for path in paths:
            if path.name in remote:
                if remote[path.name] != digest(path):
                    raise ValueError(f"published PyPI digest differs: {path.name}; reuse the original run artifacts")
            else:
                pending.append((name, path))
    upload.mkdir(exist_ok=False)
    counts = {"workspace": 0, "root": 0}
    for group in counts:
        (upload / group).mkdir()
    for name, path in pending:
        group = "root" if name == "kollab" else "workspace"
        shutil.copyfile(path, upload / group / path.name)
        counts[group] += 1
    return {key + "_count": str(value) for key, value in counts.items()}


def verify_pypi(directory: Path, release_version: str, *, workspace_only: bool = False) -> None:
    packages = package_files(directory, release_version)
    if workspace_only:
        packages.pop("kollab")
    for attempt in range(12):
        incomplete = []
        for name, paths in packages.items():
            remote = pypi_files(name, release_version)
            expected = {path.name: digest(path) for path in paths}
            if remote.keys() - expected.keys() or any(k in remote and remote[k] != v for k, v in expected.items()):
                raise ValueError(f"published PyPI files differ for {name}")
            if remote != expected:
                incomplete.append(name)
        if not incomplete:
            print(f"Verified PyPI digests for {len(packages)} packages")
            return
        if attempt < 11:
            time.sleep(5)
    raise ValueError(f"PyPI publication incomplete: {', '.join(incomplete)}")


def publish_github(directory: Path, tag: str, source: str, tag_object: str) -> None:
    release_version = version(tag)
    verify_manifest(directory, release_version, windows="required")
    check_remote_tag(tag, source, tag_object)
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise ValueError("GitHub release job requires GH_TOKEN")
    files = {p.name: p for p in directory.iterdir()}
    release = request_json(f"{API}/releases/tags/{tag}", token=token, missing=True)
    if release is None:
        release = request_json(
            f"{API}/releases",
            method="POST",
            token=token,
            data={
                "tag_name": tag,
                "target_commitish": source,
                "name": tag,
                "draft": True,
                "generate_release_notes": True,
                "body": "macOS and Windows desktop installers are unsigned. "
                "On macOS, use Finder's Control-click > Open. "
                "On Windows, choose More info, then Run anyway.\n\n"
                f"[Changelog](https://github.com/{REPOSITORY}/blob/{tag}/CHANGELOG.md)",
            },
        )
    # target_commitish is ignored by GitHub when a tag exists, and may be a
    # branch name. The verified tag object above is the source identity.
    if release["tag_name"] != tag:
        raise ValueError("existing GitHub release identifies a different tag")
    remote = request_json(f"{API}/releases/{release['id']}/assets?per_page=100", token=token)
    by_name = {a["name"]: a for a in remote}
    if len(remote) != len(by_name) or by_name.keys() - files.keys():
        raise ValueError("unexpected or duplicate GitHub release assets")
    for name, asset in by_name.items():
        if asset.get("digest") != "sha256:" + digest(files[name]) or asset.get("state") != "uploaded":
            raise ValueError(f"existing GitHub asset differs: {name}; refusing replacement")
    missing = files.keys() - by_name.keys()
    if missing and not release["draft"]:
        raise ValueError("public GitHub release is incomplete; refusing to modify it")
    for name in sorted(missing):
        check_remote_tag(tag, source, tag_object)
        asset = request_json(
            f"https://uploads.github.com/repos/{REPOSITORY}/releases/{release['id']}/assets"
            f"?name={quote(name, safe='')}",
            method="POST",
            token=token,
            data=files[name].read_bytes(),
        )
        if asset.get("digest") != "sha256:" + digest(files[name]) or asset.get("state") != "uploaded":
            raise ValueError(f"uploaded GitHub digest differs: {name}")
    final = request_json(f"{API}/releases/{release['id']}/assets?per_page=100", token=token)
    if len(final) != len(files) or {a["name"]: a.get("digest") for a in final} != {
        n: "sha256:" + digest(p) for n, p in files.items()
    }:
        raise ValueError("GitHub asset verification failed; leaving draft unpublished")
    check_remote_tag(tag, source, tag_object)
    if release["draft"]:
        request_json(
            f"{API}/releases/{release['id']}",
            method="PATCH",
            token=token,
            data={"draft": False, "make_latest": "legacy"},
        )
    print(f"Verified GitHub release {tag}: {len(files)} assets")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    source = commands.add_parser("source")
    source.add_argument("--repo", type=Path, default=Path.cwd())
    source.add_argument("--tag", required=True)
    source.add_argument("--workflow-ref", required=True)
    source.add_argument("--controller", required=True)
    source.add_argument("--output", type=Path, required=True)
    for name in ("check-tag", "plan-pypi", "verify-pypi", "github"):
        command = commands.add_parser(name)
        command.add_argument("--tag", required=True)
        if name != "verify-pypi":
            command.add_argument("--source", required=True)
            command.add_argument("--tag-object", required=True)
        if name != "check-tag":
            command.add_argument("--dist", required=True, type=Path)
        if name == "plan-pypi":
            command.add_argument("--upload", required=True, type=Path)
            command.add_argument("--output", required=True, type=Path)
        if name == "verify-pypi":
            command.add_argument("--workspace-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "source":
            result = provenance(args.repo, args.tag, args.workflow_ref, args.controller)
            with args.output.open("a") as stream:
                stream.writelines(f"{key}={value}\n" for key, value in result.items())
        elif args.command == "verify-pypi":
            verify_pypi(args.dist, version(args.tag), workspace_only=args.workspace_only)
        elif args.command == "github":
            publish_github(args.dist, args.tag, args.source, args.tag_object)
        else:
            check_remote_tag(args.tag, args.source, args.tag_object)
            if args.command == "plan-pypi":
                result = pypi_plan(args.dist, version(args.tag), args.upload)
                with args.output.open("a") as stream:
                    stream.writelines(f"{key}={value}\n" for key, value in result.items())
    except (OSError, ValueError, subprocess.CalledProcessError, KeyError) as error:
        print(f"Release publishing stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
