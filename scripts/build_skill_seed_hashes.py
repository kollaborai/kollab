"""Build bundles/data/skill-seed-hashes.json from git history.

Maps each bundled skill name to every file this repo has committed under
bundles/skills/<name>/: {relative path: [sha256 of each committed version]}.
config_utils._retire_seeded_skills uses it to tell an unmodified seed copy
from a user's edited one. The old seeding copied only missing files, so one
seed copy can mix versions; the check is per file, not per folder.

Run from anywhere inside the repo: python scripts/build_skill_seed_hashes.py
"""

import hashlib
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "bundles" / "data" / "skill-seed-hashes.json"
SKILL_FILE = re.compile(r"bundles/skills/([^/]+)/(.+)")
COMMIT = re.compile(r"[0-9a-f]{40}")


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=REPO_ROOT, capture_output=True)


def main() -> None:
    # first-parent diffs so a merge that resolves a conflict still
    # contributes the version it produced
    log = _git(
        "log",
        "--format=%H",
        "--name-only",
        "--diff-merges=first-parent",
        "--",
        "bundles/skills",
    )
    if log.returncode != 0:
        raise SystemExit(log.stderr.decode())

    pairs: set[tuple[str, str, str, str]] = set()
    commit = ""
    for line in log.stdout.decode().splitlines():
        if COMMIT.fullmatch(line):
            commit = line
        elif match := SKILL_FILE.fullmatch(line):
            pairs.add((commit, line, match.group(1), match.group(2)))

    versions: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for sha, path, name, rel in sorted(pairs):
        blob = _git("cat-file", "blob", f"{sha}:{path}")
        if blob.returncode == 0:  # missing at this commit: it was deleted there
            versions[name][rel].add(hashlib.sha256(blob.stdout).hexdigest())

    data = {
        name: {rel: sorted(hashes) for rel, hashes in sorted(files.items())}
        for name, files in sorted(versions.items())
        if "SKILL.md" in files
    }
    OUTPUT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    total = sum(len(hashes) for files in data.values() for hashes in files.values())
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}: {len(data)} skills, {total} file versions")


if __name__ == "__main__":
    main()
