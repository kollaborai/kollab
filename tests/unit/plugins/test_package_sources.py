"""Plugin package Git source validation (issue #1028)."""

import json
import subprocess
import tarfile

import pytest


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://github.com/example/plugin", ("https://github.com/example/plugin", None)),
        ("https://github.com/example/plugin#release/1.2", ("https://github.com/example/plugin", "release/1.2")),
        ("ssh://github.com/example/plugin#main", ("ssh://github.com/example/plugin", "main")),
        ("git@github.com:example/plugin#v1", ("git@github.com:example/plugin", "v1")),
        ("/tmp/plugin", None),
        ("file:///tmp/plugin", None),
        ("https://user:secret@example.com/repo", None),
        ("https://user@example.com/repo", None),
        ("ssh://user@example.com/repo", None),
        ("https://example.com/repo#-bad", None),
        ("https://example.com/repo#bad ref", None),
        ("https://example.com/repo#", None),
        ("https://example.com/repo#main#other", None),
        ("https://example.com/repo?token=secret", None),
    ],
)
def test_parse_git_source(source, expected, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins.packages import parse_git_source

    if expected is None:
        with pytest.raises(ValueError):
            parse_git_source(source)
    else:
        assert parse_git_source(source) == expected


@pytest.mark.parametrize(
    ("files", "archive_limit", "file_limit", "reject"),
    [
        ({"small.txt": b"small"}, 1_000_000, 10, False),
        ({"large.txt": b"x" * 2048}, 1, 10, True),
        ({"one.txt": b"1", "two.txt": b"2"}, 1_000_000, 2, True),
    ],
)
def test_stage_git_source_limits_archive_before_extract(
    files, archive_limit, file_limit, reject, tmp_path, monkeypatch
):
    from kollabor_plugins import packages

    repository = tmp_path / "remote"
    repository.mkdir()
    manifest = {
        "format": 1,
        "id": "fixture",
        "name": "Fixture",
        "version": "1.0.0",
        "description": "Archive limit fixture",
        "kollab": ">=0.0.0",
        "uses": [],
    }
    (repository / "kollab-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name, contents in files.items():
        (repository / name).write_bytes(contents)
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "fixture"], check=True, capture_output=True)
    commit = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()

    monkeypatch.setattr(packages, "parse_git_source", lambda _source: (str(repository), None))
    monkeypatch.setattr(packages, "resolve_git_commit", lambda _url, _ref: commit)
    monkeypatch.setattr(packages, "_MAX_PLUGIN_ARCHIVE_BYTES", archive_limit, raising=False)
    monkeypatch.setattr(packages, "_MAX_PLUGIN_ARCHIVE_FILES", file_limit, raising=False)
    extracted = []
    extractall = tarfile.TarFile.extractall

    def record_extractall(archive, *args, **kwargs):
        extracted.append(True)
        return extractall(archive, *args, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "extractall", record_extractall)
    stage = tmp_path / "stage"
    if reject:
        with pytest.raises(packages.ManifestReviewLimitError):
            packages.stage_git_source("https://example.invalid/plugin", stage, "1.0.0")
        assert extracted == []
        assert not stage.exists()
    else:
        staged = packages.stage_git_source("https://example.invalid/plugin", stage, "1.0.0")
        assert staged["manifest"]["id"] == "fixture"
        assert extracted == [True]
        assert (stage / "small.txt").read_bytes() == b"small"
