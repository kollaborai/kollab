#!/bin/zsh
# The phone app in the Android emulator, linked to a scratch computer over PhoneLink: the harness relay serves
# https://relay.localhost:<port> with a scratch CA that the emulator holds as a user CA (only debug builds trust
# it), `adb reverse` carries that port to this computer, and the drive pastes the link, approves it, waits for
# the web UI and answers Android's microphone prompt. Nothing touches ~/.kollab, a live engine or a public relay.
#
#   tests/live/phonelink_emulator.sh
#
# First build the debug APK (desktop/README.md, "Build and run it in the Android emulator") and the web UI
# (`npm run build` in packages/kollabor-webui/frontend). The emulator boots headless from an AVD in a scratch
# folder (KOLLAB_AVD_HOME keeps one between runs). Screenshots land in the folder printed at the end.
ROOT=${0:A:h:h:h}
A=${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}
export ANDROID_HOME=$A ANDROID_SDK_ROOT=$A
export JAVA_HOME=${JAVA_HOME:-/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}
export PATH=$A/platform-tools:$A/emulator:$A/cmdline-tools/latest/bin:$PATH
export ANDROID_SERIAL=emulator-5584
PY=${PYTHON:-$ROOT/.venv/bin/python}
APK=$ROOT/desktop/src-tauri/gen/android/app/build/outputs/apk/universal/debug/app-universal-debug.apk
FRONTEND=$ROOT/packages/kollabor-webui/frontend
[ -f $APK ] || { echo "no debug APK at $APK: build it first (desktop/README.md)"; exit 2; }
[ -d $FRONTEND/dist/phonelink ] || { echo "no phone bundle: run npm run build in $FRONTEND"; exit 2; }
T=$(mktemp -d) || exit 1; OUT=$T/screens; mkdir -p $OUT
export ANDROID_AVD_HOME=${KOLLAB_AVD_HOME:-$T/avd}
read RELAY CONTROL TLS <<< "$($PY -c 'import socket
s = [socket.socket() for _ in range(3)]
[x.bind(("127.0.0.1", 0)) for x in s]
print(*[x.getsockname()[1] for x in s])')"

# The scratch computer: the harness, its relay also on https://relay.localhost:$TLS
$PY $ROOT/tests/live/phonelink_scratch_cert.py $T/certs >/dev/null
PP=$ROOT; for d in $ROOT/packages/*/src; do PP="$PP:$d"; done
(cd $ROOT && HOME=$T/home KOLLAB_NO_KEYRING=1 PYTHONPATH=$PP PYTHONDONTWRITEBYTECODE=1 \
  KOLLAB_PHONELINK_ORIGIN="https://relay.localhost:$TLS" KOLLAB_PHONELINK_TLS=$T/certs \
  $PY tests/live/phonelink_harness.py $RELAY $CONTROL $T/scratch > $T/harness.log 2>&1) &
HARNESS=$!
for i in {1..60}; do grep -q '^ready' $T/harness.log 2>/dev/null && break; sleep 0.5; done
grep -q '^ready' $T/harness.log || { echo "the harness did not start:"; tail -5 $T/harness.log; kill $HARNESS; exit 1; }

# The phone: a headless Android 16 emulator
mkdir -p $ANDROID_AVD_HOME
[ -d $ANDROID_AVD_HOME/kollab-phone.avd ] || echo no | avdmanager create avd -n kollab-phone \
  -k "system-images;android-36;google_apis;arm64-v8a" -d pixel_7 > $T/avd.log 2>&1
emulator -avd kollab-phone -port 5584 -no-window -no-snapshot -no-boot-anim -gpu swiftshader_indirect > $T/emulator.log 2>&1 &
EMULATOR=$!
adb wait-for-device
for i in {1..120}; do [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] && break; sleep 3; done
echo "emulator: Android $(adb shell getprop ro.build.version.release | tr -d '\r')"

# The scratch CA as a user CA (the google_apis image is userdebug, so adb runs as root), then the relay port
adb root >/dev/null; sleep 4; adb wait-for-device
HASH=$($PY -c 'import hashlib, sys
from cryptography import x509
cert = x509.load_pem_x509_certificate(open(sys.argv[1], "rb").read())
print("%08x" % int.from_bytes(hashlib.md5(cert.subject.public_bytes()).digest()[:4], "little"))' $T/certs/ca.pem)
adb shell mkdir -p /data/misc/user/0/cacerts-added
adb push $T/certs/ca.pem /data/misc/user/0/cacerts-added/$HASH.0 >/dev/null
adb shell "chown -R system:system /data/misc/user/0/cacerts-added && chmod 755 /data/misc/user/0/cacerts-added && chmod 644 /data/misc/user/0/cacerts-added/$HASH.0"
adb reverse tcp:$TLS tcp:$TLS >/dev/null

# The app, fresh (no relay remembered from an earlier run), then the drive
adb install -r $APK > $T/install.log 2>&1 || { echo "install failed:"; tail -3 $T/install.log; }
adb shell pm clear ai.kollabor.kollab >/dev/null
adb shell am start -n ai.kollabor.kollab/.MainActivity >/dev/null
sleep 10
FRAGMENT=$(curl -s -X POST http://127.0.0.1:$CONTROL/offer | $PY -c 'import json, sys; print(json.load(sys.stdin)["fragment"])')
(cd $FRONTEND && node tests/phonelink-emulator-drive.mjs $ANDROID_SERIAL \
  "https://relay.localhost:$TLS/relay/v1/phone/#$FRAGMENT" http://127.0.0.1:$CONTROL $OUT)
CODE=$?

adb emu kill >/dev/null 2>&1; wait $EMULATOR 2>/dev/null
kill $HARNESS 2>/dev/null; pkill -f "phonelink_harness.py $RELAY $CONTROL" 2>/dev/null
[ -n "${KOLLAB_AVD_HOME:-}" ] || rm -rf $T/avd
rm -rf $T/home $T/scratch
echo "screenshots and logs: $T"
exit $CODE
