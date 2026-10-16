#!/usr/bin/env bash
# Wrap the caller's Tauri build; archive/upload remain the caller's responsibility.
set +x
set -euo pipefail
umask 077

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

preflight() {
  case "$1" in
    unsigned) return ;;
    developer-id) ;;
    *) fail 'mode must be unsigned or developer-id' ;;
  esac
  local name
  for name in APPLE_CERTIFICATE APPLE_CERTIFICATE_PASSWORD APPLE_SIGNING_IDENTITY APPLE_TEAM_ID; do
    [ -n "${!name:-}" ] || fail "developer-id requires $name"
  done
  case "$APPLE_SIGNING_IDENTITY" in
    'Developer ID Application: '*) ;;
    *) fail 'APPLE_SIGNING_IDENTITY must name a Developer ID Application certificate' ;;
  esac
  if [ -n "${APPLE_API_KEY:-}${APPLE_API_ISSUER:-}${APPLE_API_PRIVATE_KEY:-}" ]; then
    for name in APPLE_API_KEY APPLE_API_ISSUER APPLE_API_PRIVATE_KEY; do
      [ -n "${!name:-}" ] || fail "API notarization requires $name"
    done
    [ -z "${APPLE_ID:-}${APPLE_PASSWORD:-}" ] || fail 'choose API key authentication OR Apple ID authentication'
  else
    for name in APPLE_ID APPLE_PASSWORD; do
      [ -n "${!name:-}" ] || fail "Apple ID notarization requires $name"
    done
  fi
}

# Reject symlinks, existing unrelated directories and paths outside this job's temp dir.
check_state_path() {
  python3 - "$RUNNER_TEMP" "$APPLE_SIGNING_STATE_DIR" <<'PY'
from pathlib import Path
import sys
root, state = map(Path, sys.argv[1:])
if state.is_symlink() or state.parent.resolve() != root.resolve() or not state.name.startswith("kollab-apple-signing-"):
    raise SystemExit("unsafe Apple signing state directory")
PY
}

cleanup() {
  [ -n "${APPLE_SIGNING_STATE_DIR:-}" ] || return 0
  [ -e "$APPLE_SIGNING_STATE_DIR" ] || return 0
  check_state_path || return 1
  [ -f "$APPLE_SIGNING_STATE_DIR/.owned-by-kollab" ] || { printf 'Refusing cleanup of unowned signing state\n' >&2; return 1; }
  local result=0 original=() item
  if [ -f "$APPLE_SIGNING_STATE_DIR/search-list-changed" ]; then
    while IFS= read -r item; do original+=("$item"); done < "$APPLE_SIGNING_STATE_DIR/original-keychains.txt"
    if [ "${#original[@]}" -gt 0 ]; then
      security list-keychains -d user -s "${original[@]}" || result=1
    else
      result=1
    fi
  fi
  if [ -e "$APPLE_SIGNING_STATE_DIR/signing.keychain-db" ]; then
    security delete-keychain "$APPLE_SIGNING_STATE_DIR/signing.keychain-db" || result=1
  fi
  # Even if keychain cleanup failed, remove the unencrypted import/key files.
  python3 - "$APPLE_SIGNING_STATE_DIR" "$result" <<'PY' || result=1
from pathlib import Path
import shutil, sys
state = Path(sys.argv[1])
for name in ("certificate.p12", "notary-key.p8"):
    (state / name).unlink(missing_ok=True)
if sys.argv[2] == "0":
    shutil.rmtree(state)
PY
  return "$result"
}

cleanup_on_exit() {
  local result=$?
  trap - EXIT
  if ! cleanup; then
    printf 'error: signing cleanup incomplete; the workflow always() step will retry\n' >&2
    [ "$result" -ne 0 ] || result=1
  fi
  exit "$result"
}

prepare_keychain() {
  : "${RUNNER_TEMP:?RUNNER_TEMP is required}" "${APPLE_SIGNING_STATE_DIR:?APPLE_SIGNING_STATE_DIR is required}"
  check_state_path
  mkdir "$APPLE_SIGNING_STATE_DIR" # Never reuse or overwrite an existing directory.
  : > "$APPLE_SIGNING_STATE_DIR/.owned-by-kollab"
  trap cleanup_on_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  security list-keychains -d user > "$APPLE_SIGNING_STATE_DIR/original-keychains.raw"
  python3 - "$APPLE_SIGNING_STATE_DIR" <<'PY'
from pathlib import Path
import base64, os, shlex, sys
state = Path(sys.argv[1])
paths = shlex.split((state / "original-keychains.raw").read_text())
if not paths or any("\n" in path for path in paths):
    raise SystemExit("cannot safely preserve the original keychain search list")
(state / "original-keychains.txt").write_text("\n".join(paths) + "\n")
try:
    certificate = base64.b64decode("".join(os.environ["APPLE_CERTIFICATE"].split()), validate=True)
except ValueError:
    raise SystemExit("APPLE_CERTIFICATE must be base64-encoded PKCS#12 data")
if not certificate:
    raise SystemExit("APPLE_CERTIFICATE decodes to an empty file")
(state / "certificate.p12").write_bytes(certificate)
PY
  local keychain="$APPLE_SIGNING_STATE_DIR/signing.keychain-db" password original=() item identities
  password=$(openssl rand -base64 32)
  # Restore even if create/import changes the search list before setup finishes.
  : > "$APPLE_SIGNING_STATE_DIR/search-list-changed"
  security create-keychain -p "$password" "$keychain"
  security set-keychain-settings -lut 21600 "$keychain"
  security unlock-keychain -p "$password" "$keychain"
  security import "$APPLE_SIGNING_STATE_DIR/certificate.p12" -k "$keychain" -P "$APPLE_CERTIFICATE_PASSWORD" -T /usr/bin/codesign
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$password" "$keychain" > /dev/null
  identities=$(security find-identity -v -p codesigning "$keychain")
  printf '%s\n' "$identities" | grep -Fq -- "\"$APPLE_SIGNING_IDENTITY\"" || fail 'the imported keychain has no valid matching signing identity'
  while IFS= read -r item; do original+=("$item"); done < "$APPLE_SIGNING_STATE_DIR/original-keychains.txt"
  security list-keychains -d user -s "$keychain" "${original[@]}"
}

verify_signature() {
  local details
  codesign --verify --deep --strict --verbose=2 "$1"
  details=$(codesign --display --verbose=4 "$1" 2>&1)
  printf '%s\n' "$details" | grep -Fxq "TeamIdentifier=$APPLE_TEAM_ID" || fail 'signature team does not match APPLE_TEAM_ID'
  printf '%s\n' "$details" | grep -Eq 'flags=.*runtime' || fail 'signature has no hardened runtime'
  printf '%s\n' "$details" | grep -Eq '^Timestamp=' || fail 'signature has no secure timestamp'
}

notarize() {
  local app="$1" auth=() submission_status=0
  if [ -n "${APPLE_API_KEY:-}" ]; then
    printf '%s\n' "$APPLE_API_PRIVATE_KEY" > "$APPLE_SIGNING_STATE_DIR/notary-key.p8"
    auth=(--key "$APPLE_SIGNING_STATE_DIR/notary-key.p8" --key-id "$APPLE_API_KEY" --issuer "$APPLE_API_ISSUER")
  else
    auth=(--apple-id "$APPLE_ID" --password "$APPLE_PASSWORD" --team-id "$APPLE_TEAM_ID")
  fi
  ditto -c -k --sequesterRsrc --keepParent "$app" "$APPLE_SIGNING_STATE_DIR/notary-upload.zip"
  xcrun notarytool submit "$APPLE_SIGNING_STATE_DIR/notary-upload.zip" "${auth[@]}" --wait --timeout 30m --output-format json > "$APPLE_SIGNING_STATE_DIR/notary-result.json" || submission_status=$?
  python3 - "$APPLE_SIGNING_STATE_DIR/notary-result.json" "$submission_status" <<'PY'
import json, sys
with open(sys.argv[1]) as stream:
    result = json.load(stream)
print("Notarization submission:", result.get("id", "unknown"), "status:", result.get("status", "unknown"))
if sys.argv[2] != "0":
    raise SystemExit("notarytool failed or timed out; do not archive or publish this app")
if result.get("status") != "Accepted":
    raise SystemExit("Apple did not accept the submission; do not archive or publish this app")
PY
  xcrun stapler staple "$app"
  xcrun stapler validate "$app"
  codesign --verify --deep --strict --verbose=2 "$app"
  spctl --assess --type execute --verbose=2 "$app"
}

command=${1:-}
mode=${APPLE_SIGNING_MODE:-unsigned}
case "$command" in
  preflight)
    [ "$#" -le 2 ] || fail 'usage: sign-macos.sh preflight [MODE]'
    preflight "${2:-$mode}"
    exit 0
    ;;
  cleanup) cleanup; exit $? ;;
  build)
    if [ "$#" -lt 4 ] || [ "${3:-}" != -- ]; then
      fail 'usage: sign-macos.sh build /path/Kollab.app -- COMMAND [ARGS...]'
    fi
    app=$2
    shift 3
    ;;
  *) fail 'usage: sign-macos.sh preflight [MODE] | build /path/Kollab.app -- COMMAND [ARGS...] | cleanup' ;;
esac
preflight "$mode"
case "$app" in
  /*/Kollab.app) ;;
  *) fail 'the expected app must be an absolute path ending in /Kollab.app' ;;
esac
if [ -e "$app" ] || [ -L "$app" ]; then
  fail 'expected app path already exists; use a fresh build output directory'
fi
if [ "$mode" = developer-id ]; then prepare_keychain; fi

(
  # Tauri signs only. Explicit notarytool/stapler checks below own notarization.
  unset APPLE_CERTIFICATE APPLE_CERTIFICATE_PASSWORD APPLE_ID APPLE_PASSWORD APPLE_TEAM_ID
  unset APPLE_API_KEY APPLE_API_ISSUER APPLE_API_PRIVATE_KEY APPLE_API_KEY_PATH
  if [ "$mode" = unsigned ]; then APPLE_SIGNING_IDENTITY=-; fi
  export APPLE_SIGNING_IDENTITY
  # Preserve the caller's working directory and argument boundaries. Never eval.
  "$@"
)
[ -d "$app" ] || fail 'Tauri did not produce Kollab.app at the expected target path'
[ -f "$app/Contents/Resources/kollab" ] || fail 'Kollab.app is missing its bundled kollab sidecar'
if [ "$mode" = developer-id ]; then
  # The scie sidecar is sealed bundle data, not nested code: codesign cannot sign it.
  verify_signature "$app"
  notarize "$app"
  cleanup || fail 'signing cleanup incomplete; do not archive or publish this app'
  trap - EXIT INT TERM
fi
printf 'Prepared %s (%s); the caller may now archive it\n' "$app" "$mode"
