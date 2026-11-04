"""Release provenance, retry safety and artifact checks at the write boundary."""

import io
import subprocess
import tarfile
import zipfile

import pytest

from scripts import release_publish as release
from scripts.desktop_release import manifest, windows_manifest, write_checksum

SOURCE = "a" * 40
TAG_OBJECT = "b" * 40
VERSION = "0.19.0"
TAG = "v" + VERSION


@pytest.fixture
def distributions(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in [*release.PACKAGES, "kollab"]:
        base = name.replace("-", "_") + "-" + VERSION
        metadata = f"Metadata-Version: 2.3\nName: {name}\nVersion: {VERSION}\n\n".encode()
        with zipfile.ZipFile(dist / (base + "-py3-none-any.whl"), "w") as archive:
            archive.writestr(base + ".dist-info/METADATA", metadata)
        with tarfile.open(dist / (base + ".tar.gz"), "w:gz") as archive:
            member = tarfile.TarInfo(base + "/PKG-INFO")
            member.size = len(metadata)
            archive.addfile(member, io.BytesIO(metadata))
    return dist


@pytest.fixture
def assets(tmp_path):
    directory = tmp_path / "assets"
    directory.mkdir()
    for name in manifest(VERSION) | windows_manifest(VERSION):
        if not name.endswith(".sha256"):
            path = directory / name
            path.write_bytes(name.encode())
            write_checksum(path)
    return directory


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Release Test")
    git(repo, "config", "user.email", "release@example.invalid")
    (repo / "source").write_text("release\n")
    git(repo, "add", "source")
    git(repo, "commit", "-m", "test: release")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", head)
    git(repo, "tag", "-a", TAG, "-m", "release")
    return repo, head


@pytest.mark.parametrize(
    "tag", ["main", "-v0.19.0", "v01.19.0", "v0.19.0\n", "v0.19.0;echo hi", "v0.19.0/extra", "v0.19.0-rc1"]
)
def test_tag_rejects_nonrelease_inputs(tag):
    with pytest.raises(ValueError):
        release.version(tag)


def test_provenance_records_tag_object_and_reviewed_source(repository):
    repo, head = repository
    result = release.provenance(repo, TAG, "refs/heads/main", head)
    assert result == {
        "tag": TAG,
        "version": VERSION,
        "source_sha": head,
        "tag_object": git(repo, "rev-parse", "refs/tags/" + TAG),
        "controller_sha": head,
    }


def test_branch_dispatch_cannot_validate(repository):
    repo, head = repository
    with pytest.raises(ValueError, match="refs/heads/main"):
        release.provenance(repo, TAG, "refs/heads/writer", head)


def test_unmerged_tag_cannot_validate(repository):
    repo, head = repository
    git(repo, "checkout", "-b", "writer")
    (repo / "source").write_text("unreviewed\n")
    git(repo, "commit", "-am", "test: unmerged source")
    git(repo, "tag", "-a", "v0.20.0", "-m", "unmerged")
    with pytest.raises(subprocess.CalledProcessError):
        release.provenance(repo, "v0.20.0", "refs/heads/main", head)


def test_lightweight_tag_is_rejected(repository):
    repo, head = repository
    git(repo, "tag", "v0.20.0")
    with pytest.raises(ValueError, match="annotated"):
        release.provenance(repo, "v0.20.0", "refs/heads/main", head)


@pytest.mark.parametrize("change", ["object", "source"])
def test_remote_tag_change_stops_before_write(monkeypatch, change):
    replies = [{"object": {"type": "tag", "sha": TAG_OBJECT}}, {"object": {"type": "commit", "sha": SOURCE}}]
    replies[0 if change == "object" else 1]["object"]["sha"] = "c" * 40
    monkeypatch.setattr(release, "request_json", lambda *a, **k: replies.pop(0))
    with pytest.raises(ValueError, match="changed"):
        release.check_remote_tag(TAG, SOURCE, TAG_OBJECT)


def test_package_manifest_checks_all_ten_and_excludes_voice(distributions):
    packages = release.package_files(distributions, VERSION)
    assert len(packages) == 10
    assert sum(len(paths) for paths in packages.values()) == 20
    assert "kollabor-voice" not in packages


@pytest.mark.parametrize("case", ["extra", "missing", "symlink", "metadata", "duplicate_metadata"])
def test_bad_package_set_is_rejected_before_network(distributions, tmp_path, monkeypatch, case):
    path = distributions / f"kollab-{VERSION}-py3-none-any.whl"
    if case == "extra":
        (distributions / "unexpected.whl").write_bytes(b"extra")
    elif case == "missing":
        path.unlink()
    elif case == "symlink":
        original = tmp_path / "original.whl"
        path.rename(original)
        path.symlink_to(original)
    else:
        with zipfile.ZipFile(path, "w") as archive:
            if case == "metadata":
                archive.writestr("kollab.dist-info/METADATA", "Name: kollab\nVersion: 9.0.0\n")
            else:
                archive.writestr("one.dist-info/METADATA", "Name: kollab\nVersion: 0.19.0\n")
                archive.writestr("two.dist-info/METADATA", "Name: kollab\nVersion: 0.19.0\n")
    monkeypatch.setattr(release, "pypi_files", lambda *a: pytest.fail("network before metadata validation"))
    with pytest.raises(ValueError):
        release.pypi_plan(distributions, VERSION, tmp_path / "upload")


def test_plan_stages_dependencies_separately_from_root(distributions, tmp_path, monkeypatch):
    monkeypatch.setattr(release, "pypi_files", lambda *a: {})
    upload = tmp_path / "upload"
    assert release.pypi_plan(distributions, VERSION, upload) == {"workspace_count": "18", "root_count": "2"}
    assert len(list((upload / "workspace").iterdir())) == 18
    assert {p.name for p in (upload / "root").iterdir()} == {p.name for p in distributions.glob("kollab-*")}


def test_matching_partial_upload_resumes_only_missing_files(distributions, tmp_path, monkeypatch):
    path = next(distributions.glob("kollabor_ai-*.whl"))
    monkeypatch.setattr(
        release, "pypi_files", lambda name, _: {path.name: release.digest(path)} if name == "kollabor-ai" else {}
    )
    upload = tmp_path / "upload"
    assert release.pypi_plan(distributions, VERSION, upload)["workspace_count"] == "17"
    assert not (upload / "workspace" / path.name).exists()


def test_remote_mismatch_stops_whole_plan_before_staging(distributions, tmp_path, monkeypatch):
    path = next(distributions.glob("kollab-*.whl"))
    monkeypatch.setattr(release, "pypi_files", lambda name, _: {path.name: "0" * 64} if name == "kollab" else {})
    upload = tmp_path / "upload"
    with pytest.raises(ValueError, match="digest differs"):
        release.pypi_plan(distributions, VERSION, upload)
    assert not upload.exists()


def test_published_verification_compares_digests(distributions, monkeypatch):
    packages = release.package_files(distributions, VERSION)
    monkeypatch.setattr(release, "pypi_files", lambda name, _: {p.name: release.digest(p) for p in packages[name]})
    release.verify_pypi(distributions, VERSION)
    monkeypatch.setattr(release, "pypi_files", lambda *a: {"unexpected.whl": "0" * 64})
    with pytest.raises(ValueError, match="differ"):
        release.verify_pypi(distributions, VERSION)


def github_api(monkeypatch, assets, *, existing=0, draft=True, bad_digest=False):
    state = {
        "release": {"id": 123, "tag_name": TAG, "target_commitish": SOURCE, "draft": draft},
        "assets": [],
        "writes": [],
        "tag_checks": 0,
    }
    for i, path in enumerate(sorted(assets.iterdir())[:existing]):
        state["assets"].append(
            {
                "id": i + 1,
                "name": path.name,
                "state": "uploaded",
                "digest": "sha256:" + ("0" * 64 if bad_digest else release.digest(path)),
            }
        )

    def check(*args):
        state["tag_checks"] += 1

    def request(url, *, method="GET", data=None, **kwargs):
        if method != "GET":
            state["writes"].append((method, url))
        if "/git/" in url:
            pytest.fail("tag validation mock missing")
        if "/assets?" in url:
            if method == "GET":
                return state["assets"]
            name = url.split("?name=")[1]
            item = {
                "id": len(state["assets"]) + 1,
                "name": name,
                "state": "uploaded",
                "digest": "sha256:" + release.digest(assets / name),
            }
            state["assets"].append(item)
            return item
        if method == "PATCH":
            assert len(state["assets"]) == 22
            state["release"]["draft"] = data["draft"]
            return state["release"]
        return state["release"]

    monkeypatch.setenv("GH_TOKEN", "test-value")
    monkeypatch.setattr(release, "request_json", request)
    monkeypatch.setattr(release, "check_remote_tag", check)
    return state


def test_complete_draft_published_only_after_22_digest_checks(assets, monkeypatch):
    state = github_api(monkeypatch, assets, existing=3)
    release.publish_github(assets, TAG, SOURCE, TAG_OBJECT)
    assert len(state["assets"]) == 22
    assert state["release"]["draft"] is False
    assert sum(method == "POST" for method, _ in state["writes"]) == 19
    assert state["tag_checks"] >= 21


def test_matching_public_release_is_read_only(assets, monkeypatch):
    state = github_api(monkeypatch, assets, existing=22, draft=False)
    state["release"]["target_commitish"] = "main"
    release.publish_github(assets, TAG, SOURCE, TAG_OBJECT)
    assert state["writes"] == []


@pytest.mark.parametrize("draft,bad", [(False, False), (True, True)])
def test_incomplete_public_release_or_different_asset_is_never_replaced(assets, monkeypatch, draft, bad):
    state = github_api(monkeypatch, assets, existing=1, draft=draft, bad_digest=bad)
    with pytest.raises(ValueError):
        release.publish_github(assets, TAG, SOURCE, TAG_OBJECT)
    assert state["writes"] == []


def test_invalid_binary_manifest_stops_before_remote_calls(assets, monkeypatch):
    (assets / "kollab-linux-aarch64.sha256").unlink()
    monkeypatch.setattr(
        release, "request_json", lambda *a, **k: pytest.fail("remote mutation before artifact validation")
    )
    with pytest.raises(ValueError, match="manifest mismatch"):
        release.publish_github(assets, TAG, SOURCE, TAG_OBJECT)


def test_authenticated_api_redirect_does_not_forward_credentials(monkeypatch):
    import http.server
    import threading
    from urllib.request import Request

    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            if self.path == "/origin":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_port}/destination")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        # Route the trusted API request to a controlled local HTTP server while
        # preserving its headers, exercising the real opener and redirect policy.
        monkeypatch.setattr(
            release,
            "Request",
            lambda url, **kwargs: Request(f"http://127.0.0.1:{server.server_port}/origin", **kwargs),
        )
        with pytest.raises(ValueError, match="HTTP 302"):
            release.request_json(release.API + "/releases", token="test-credential")
        assert seen == [("/origin", "Bearer test-credential")]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
