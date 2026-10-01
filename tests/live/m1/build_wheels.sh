#!/usr/bin/env bash
# build_wheels.sh <git-ref> <outdir>
# Exports <git-ref> with `git archive` (never the dirty working tree), stamps
# every version as $M1_VERSION in the export only, builds a wheel for the root
# project and each packages/* project the way publish.yml does (`uv build
# --package <name>`: sdist first, wheel from the sdist), and prints the wheels
# with sha256. Only the wheels land in <outdir>.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
REF=${1:?usage: build_wheels.sh <git-ref> <outdir>}
OUT=${2:?usage: build_wheels.sh <git-ref> <outdir>}
command -v uv >/dev/null || die "uv is required (publish.yml builds with uv)"
SHA=$(git -C "$M1_REPO" rev-parse --verify --quiet "$REF^{commit}") || die "unknown git ref: $REF"
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)

SRC=$(mktemp -d "${TMPDIR:-/tmp}/kollab-m1-src.XXXXXX")
say "exporting $REF ($SHA) to $SRC"
git -C "$M1_REPO" archive --format=tar "$SHA" | tar -xf - -C "$SRC"

say "stamping versions as $M1_VERSION in the export (and kollabor-*>= constraints)"
python3 - "$SRC" "$M1_VERSION" <<'PY'
import glob, re, sys
src, ver = sys.argv[1:3]
files = [f"{src}/pyproject.toml", *sorted(glob.glob(f"{src}/packages/*/pyproject.toml"))]
for path in files:
    text = open(path).read()
    text, n_ver = re.subn(r'(?m)^version\s*=\s*"[^"]+"', f'version = "{ver}"', text, count=1)
    text, n_dep = re.subn(r'("kollabor-[a-z]+(?:\[[^\]]*\])?)>=[^",]+"', rf'\1>={ver}"', text)
    assert n_ver == 1, f"{path}: expected one version line, found {n_ver}"
    open(path, "w").write(text)
    print(f"  {path[len(src)+1:]}: version + {n_dep} constraint(s)")
assert len(files) == 11, f"expected root + ten packages, found {len(files)} pyproject files"
PY

# Order matches publish.yml; kollabor-voice is not published there but the
# milestone asks for all ten packages, and the root wheel bundles its source.
PKGS=(kollabor-events kollabor-ai kollabor-rpc kollabor-config kollabor-plugins kollabor-tui kollabor-agent kollabor-engine kollabor-webui kollabor-voice)
DIST=$SRC/_dist
mkdir -p "$DIST"
cd "$SRC"
for pkg in "${PKGS[@]}" kollab; do
  say "building $pkg"
  uv build --quiet --out-dir "$DIST/$pkg" --package "$pkg"
  cp -f "$DIST/$pkg"/*.whl "$OUT"/
done

say "checking the root wheel carries every tracked module and data file"
python3 - "$M1_REPO" "$SHA" "$OUT" "$M1_VERSION" <<'PY'
import glob, subprocess, sys, zipfile
repo, sha, out, ver = sys.argv[1:5]
tracked = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "-z", "--name-only", sha],
                         capture_output=True, text=True, check=True).stdout.split("\0")
(root,) = glob.glob(f"{out}/kollab-{ver}-*.whl")
names = set(zipfile.ZipFile(root).namelist())
want = []
for f in tracked:
    if f.startswith(("kollabor/", "plugins/")) and (f.endswith(".py") or f.endswith((".json", ".yaml", ".yml"))):
        want.append(f)
    elif f.startswith("bundles/") and not f.endswith((".bak", "~", ".tmp")):
        want.append(f)
    elif f.startswith("packages/") and "/src/" in f and f.endswith(".py") and "kollabor-webui" not in f:
        want.append(f.split("/src/", 1)[1])
missing = sorted(set(want) - names)
print(f"  {len(set(want))} expected files, {len(missing)} missing from the root wheel")
for f in missing[:40]:
    print("  MISSING:", f)
sys.exit(1 if missing else 0)
PY

say "wheels in $OUT (version $M1_VERSION):"
cd "$OUT"
: > MANIFEST.txt
echo "# ref $REF -> $SHA" >> MANIFEST.txt
COUNT=0
for w in ./*-"$M1_VERSION"-*.whl; do
  shasum -a 256 "$w" | sed 's| \./| |' | tee -a MANIFEST.txt
  COUNT=$((COUNT + 1))
done
[ "$COUNT" -eq "$M1_EXPECT_WHEELS" ] || die "expected $M1_EXPECT_WHEELS wheels, found $COUNT"
say "done: $COUNT wheels"
