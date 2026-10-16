"""Run without the application/pytest environment; Apple tools are fixture executables."""

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "desktop/scripts/sign-macos.sh"
WORKFLOW = ROOT / ".github/workflows/desktop-macos-signing.yml"
TARGET = "aarch64-apple-darwin"


def load_yaml(source):
    # Ruby's standard YAML parser keeps these tests independent of pytest/PyYAML.
    result = subprocess.run([
        "ruby", "-rjson", "-ryaml", "-e", "puts JSON.generate(YAML.load(STDIN.read))"
    ], input=source, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def documented_examples():
    docs = (ROOT / "desktop/docs/apple-signing.md").read_text()
    return [load_yaml(source) for source in re.findall(r"```yaml\n(.*?)\n```", docs, re.S)]


MOCK_TOOL = r'''
import json, os, pathlib, sys
tool, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["MOCK_LOG"], "a") as stream:
    stream.write(json.dumps([tool, args]) + "\n")
app = pathlib.Path(os.environ["MOCK_APP"])
if tool == "security":
    if args == ["list-keychains", "-d", "user"]:
        print('"/fixture/login with spaces.keychain-db"\n"/fixture/extra.keychain-db"')
    elif args[0] == "create-keychain":
        pathlib.Path(args[-1]).touch()
    elif args[0] == "import" and os.environ.get("MOCK_IMPORT_FAIL"):
        sys.exit(12)
    elif args[0] == "find-identity":
        print('1) FIXTURE_HASH "' + os.environ["APPLE_SIGNING_IDENTITY"] + '"')
    elif args[0] == "delete-keychain":
        if os.environ.get("MOCK_CLEANUP_FAIL"):
            sys.exit(15)
        pathlib.Path(args[-1]).unlink()
elif tool == "cargo":
    if os.environ.get("MOCK_CARGO_FAIL"):
        sys.exit(42)
    assert os.getcwd() == os.environ["MOCK_BUILD_CWD"]
    assert not any(os.environ.get(name) for name in ("APPLE_CERTIFICATE", "APPLE_CERTIFICATE_PASSWORD", "APPLE_ID", "APPLE_PASSWORD", "APPLE_API_KEY", "APPLE_API_ISSUER", "APPLE_API_PRIVATE_KEY", "APPLE_API_KEY_PATH"))
    expected = "Developer ID Application: Fixture (FIXTURETEAM)" if os.environ.get("MOCK_SIGNED") else "-"
    if os.environ.get("MOCK_UNWRAPPED_UNSIGNED"):
        assert "APPLE_SIGNING_IDENTITY" not in os.environ
    else:
        assert os.environ["APPLE_SIGNING_IDENTITY"] == expected
    if os.environ.get("MOCK_NO_APP"):
        sys.exit(0)
    (app / "Contents/Resources").mkdir(parents=True, exist_ok=True)
    (app / "stapled").unlink(missing_ok=True)
elif tool == "codesign" and "--display" in args:
    team = "WRONG_TEAM" if os.environ.get("MOCK_BAD_TEAM") else "FIXTURETEAM"
    print("TeamIdentifier=" + team, file=sys.stderr)
    if not os.environ.get("MOCK_NO_RUNTIME"):
        print("flags=0x10000(runtime)", file=sys.stderr)
    if not os.environ.get("MOCK_NO_TIMESTAMP"):
        print("Timestamp=fixture", file=sys.stderr)
elif tool == "ditto":
    pathlib.Path(args[-1]).write_text("stapled" if (app / "stapled").exists() else "unstapled")
elif tool == "xcrun":
    if args[:2] == ["notarytool", "submit"]:
        print(json.dumps({"id": "fixture-submission", "status": os.environ.get("MOCK_NOTARY_STATUS", "Accepted")}))
        if os.environ.get("MOCK_NOTARY_FAIL"):
            sys.exit(124)
    elif args[:2] == ["stapler", "staple"]:
        (app / "stapled").touch()
    elif args[:2] == ["stapler", "validate"]:
        if os.environ.get("MOCK_STAPLE_FAIL"):
            sys.exit(13)
        assert (app / "stapled").exists()
elif tool == "spctl" and os.environ.get("MOCK_GATEKEEPER_FAIL"):
    sys.exit(14)
'''


class SigningContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".apple-signing-test-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.scratch = Path(self.temp.name)
        tools = self.scratch / "tools"
        tools.mkdir()
        for name in ("security", "cargo", "codesign", "ditto", "xcrun", "spctl"):
            executable = tools / name
            executable.write_text("#!" + sys.executable + "\n" + MOCK_TOOL)
            executable.chmod(0o700)
        # The tree scripts/desktop_release.py stage unpacks: a launcher beside
        # runtime/, whose Mach-O files (magic numbers only here) need signing.
        self.runtime = self.scratch / "app-runtime"
        for name, body in (("kollab", b"#!/bin/sh\n"), ("runtime/KOLLAB-APP", b""),
                           ("runtime/bin/python3.12", bytes.fromhex("cffaedfe") + b"python"),
                           ("runtime/lib/python3.12/site-packages/ext.so", bytes.fromhex("cafebabe") + b"universal"),
                           ("runtime/lib/python3.12/os.py", b"import abc\n")):
            (self.runtime / name).parent.mkdir(parents=True, exist_ok=True)
            (self.runtime / name).write_bytes(body)
        self.machos = ["runtime/bin/python3.12", "runtime/lib/python3.12/site-packages/ext.so"]
        self.state = self.scratch / "kollab-apple-signing-fixture"
        self.artifacts = self.scratch / "artifacts"
        self.build_number = 0
        # Exclude the developer's venv and keychain state from all fixture tools.
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("APPLE_")}
        self.env.update(
            PATH=str(tools) + ":/usr/bin:/bin:/usr/sbin:/sbin",
            PYTHONDONTWRITEBYTECODE="1",
            RUNNER_TEMP=str(self.scratch),
            APPLE_SIGNING_STATE_DIR=str(self.state),
            MOCK_LOG=str(self.scratch / "tools.jsonl"),
            MOCK_BUILD_CWD=str(ROOT / "desktop"),
            MOCK_APP=str(self.scratch / "unused/Kollab.app"),
            KOLLAB_APP_RUNTIME=str(self.runtime),
        )

    def signed(self, api=False):
        self.env.update(
            APPLE_SIGNING_MODE="developer-id",
            APPLE_CERTIFICATE=base64.b64encode(b"fixture certificate, not PKCS12").decode(),
            APPLE_CERTIFICATE_PASSWORD="fixture-p12-password",
            APPLE_SIGNING_IDENTITY="Developer ID Application: Fixture (FIXTURETEAM)",
            APPLE_TEAM_ID="FIXTURETEAM",
            MOCK_SIGNED="1",
        )
        if api:
            self.env.update(APPLE_API_KEY="fixture-key-id", APPLE_API_ISSUER="fixture-issuer", APPLE_API_PRIVATE_KEY="fixture-private-key")
        else:
            self.env.update(APPLE_ID="fixture@example.invalid", APPLE_PASSWORD="fixture-app-specific-password")

    def run_script(self, *args):
        return subprocess.run(["/bin/bash", str(SCRIPT), *args], cwd=ROOT / "desktop", env=self.env, capture_output=True, text=True)

    def run_build(self, mode=None, app=None, extra_args=()):
        if mode is not None:
            self.env["APPLE_SIGNING_MODE"] = mode
        self.build_number += 1
        target = self.env.get("MOCK_TARGET", TARGET)
        self.app = app or self.scratch / ("target output " + str(self.build_number)) / target / "release/bundle/macos/Kollab.app"
        self.env["MOCK_APP"] = str(self.app)
        self.build_command = ["cargo", "tauri", "build", "--target", target, "--bundles", "app", *extra_args]
        return self.run_script("build", str(self.app), "--", *self.build_command)

    def configure_documented_run(self):
        self.build_number += 1
        run_id = "fixture-" + str(self.build_number)
        target_dir = self.scratch / ("documented target " + str(self.build_number))
        self.state = self.scratch / ("kollab-apple-signing-" + run_id + "-1")
        self.app = target_dir / TARGET / "release/bundle/macos/Kollab.app"
        self.github_env = self.scratch / ("github-env-" + str(self.build_number))
        self.env.pop("APPLE_SIGNING_STATE_DIR", None)
        self.env.update(
            TARGET=TARGET, CARGO_TARGET_DIR=str(target_dir), MOCK_APP=str(self.app),
            GITHUB_REF="refs/heads/main", GITHUB_RUN_ID=run_id, GITHUB_RUN_ATTEMPT="1",
            GITHUB_ENV=str(self.github_env),
        )
        steps = documented_examples()[1]["steps"]
        self.doc_guard = steps[0]
        self.doc_unsigned = next(step for step in steps if step.get("if") == "env.APPLE_SIGNING_MODE == 'unsigned'")
        self.doc_signed = next(step for step in steps if "secrets." in json.dumps(step.get("env", {})))
        self.doc_cleanup = next(step for step in steps if step.get("run", "").endswith(" cleanup"))

    def run_documented_step(self, step):
        # Model step-scoped secret injection; repository secrets are not job env.
        env = {name: value for name, value in self.env.items()
               if not name.startswith("APPLE_") or name in ("APPLE_SIGNING_MODE", "APPLE_SIGNING_STATE_DIR")}
        for name, source in step.get("env", {}).items():
            self.assertEqual(source, "${{ secrets." + name + " }}")
            env[name] = self.env.get(name, "")
        return subprocess.run(["/bin/bash", "-e", "-o", "pipefail", "-c", step["run"]],
                              cwd=ROOT / step.get("working-directory", "."), env=env,
                              capture_output=True, text=True)

    def apply_documented_environment(self):
        lines = self.github_env.read_text().splitlines()
        self.assertEqual(lines, ["APPLE_SIGNING_STATE_DIR=" + str(self.state)])
        name, value = lines[0].split("=", 1)
        self.env[name] = value

    def calls(self):
        path = self.scratch / "tools.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def signings(self):
        """(path relative to Resources or the app itself, codesign flags) for each signing, in order."""
        resources = str(self.app / "Contents/Resources") + "/"
        return [(args[-1].removeprefix(resources) if args[-1] != str(self.app) else "app", args[:-1])
                for tool, args in self.calls() if tool == "codesign" and "--sign" in args]

    def assert_sealed(self, identity, flags):
        signed = self.signings()
        self.assertEqual(sorted(path for path, _ in signed[:-1]), sorted(self.machos))
        self.assertEqual(signed[-1][0], "app", "the app is signed after the code inside it")
        for _, args in signed:
            self.assertEqual(args, ["--force", "--sign", identity, *flags])
        self.assertEqual((self.app / "Contents/Resources/kollab").read_bytes(), b"#!/bin/sh\n")
        self.assertTrue((self.app / "Contents/Resources/runtime/lib/python3.12/os.py").is_file())
        self.assertIn(["codesign", ["--verify", "--deep", "--strict", "--verbose=2", str(self.app)]], self.calls())

    def assert_failed_and_clean(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("the caller may now archive", result.stdout)
        self.assertFalse(self.state.exists(), "owned signing state must be cleaned on failure")

    def test_default_unsigned_uses_no_apple_services_or_signing_state(self):
        self.env.pop("APPLE_SIGNING_STATE_DIR")
        self.env.pop("RUNNER_TEMP")
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({call[0] for call in self.calls()}, {"cargo", "codesign"})
        self.assert_sealed("-", ["--timestamp=none"])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.artifacts.exists())
        self.assertTrue(self.app.is_dir())

    def test_unsigned_ignores_inherited_apple_credentials(self):
        for mode in (None, "unsigned"):
            with self.subTest(mode=mode):
                self.signed()
                self.env.pop("APPLE_SIGNING_MODE")
                self.env.pop("MOCK_SIGNED")
                result = self.run_build(mode=mode)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("security", {call[0] for call in self.calls()})

    def test_intel_target_is_forwarded_to_the_callers_build(self):
        self.env["MOCK_TARGET"] = "x86_64-apple-darwin"
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("x86_64-apple-darwin", self.calls()[0][1])
        self.assertTrue(self.app.is_dir())

    def test_caller_arguments_working_directory_and_output_path_are_preserved(self):
        extra = ("--config", '{"version":"1.2.3","bundle":{"macOS":{"hardenedRuntime":true}}}')
        result = self.run_build(extra_args=extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call for call in self.calls() if call[0] == "cargo"], [["cargo", self.build_command[1:]]])
        self.assertIn("target output 1", str(self.app))

    def test_existing_app_is_preserved_without_starting_a_build(self):
        app = self.scratch / "previous/Kollab.app"
        app.mkdir(parents=True)
        sentinel = app / "keep.txt"
        sentinel.write_text("previous artifact")
        result = self.run_build(app=app)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), "previous artifact")
        self.assertEqual(self.calls(), [])

    def test_missing_command_bad_path_and_unknown_mode_fail_before_build(self):
        for args in (("build", "/fixture/Kollab.app", "--"),
                     ("build", "relative/Kollab.app", "--", "cargo"),
                     ("build", "/fixture/Other.app", "--", "cargo")):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_script(*args).returncode, 0)
        self.assertNotEqual(self.run_build(mode="automatic").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_requested_signing_without_secrets_fails_before_build(self):
        result = self.run_build(mode="developer-id")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("APPLE_CERTIFICATE", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_every_common_secret_is_required_for_signing(self):
        self.signed()
        for name in ("APPLE_CERTIFICATE", "APPLE_CERTIFICATE_PASSWORD", "APPLE_SIGNING_IDENTITY", "APPLE_TEAM_ID"):
            with self.subTest(name=name):
                value = self.env.pop(name)
                self.assertNotEqual(self.run_script("preflight").returncode, 0)
                self.env[name] = value
        self.assertEqual(self.calls(), [])

    def test_all_notary_auth_subsets_allow_exactly_one_complete_set(self):
        self.signed()
        names = ("APPLE_ID", "APPLE_PASSWORD", "APPLE_API_KEY", "APPLE_API_ISSUER", "APPLE_API_PRIVATE_KEY")
        for mask in range(1 << len(names)):
            with self.subTest(mask=mask):
                for index, name in enumerate(names):
                    self.env.pop(name, None)
                    if mask & (1 << index):
                        self.env[name] = "fixture-value"
                result = self.run_script("preflight")
                self.assertEqual(result.returncode == 0, mask in (0b00011, 0b11100), result.stderr)
        self.assertEqual(self.calls(), [])

    def test_development_certificate_is_rejected(self):
        self.signed()
        self.env["APPLE_SIGNING_IDENTITY"] = "Apple Development: Fixture"
        self.assertNotEqual(self.run_script("preflight", "developer-id").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_caller_can_archive_the_app_after_acceptance_and_stapling(self):
        self.signed()
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.artifacts.exists(), "helper must leave distributable creation to the caller")
        self.assertTrue((self.app / "stapled").exists())
        self.assert_sealed("Developer ID Application: Fixture (FIXTURETEAM)", ["--options", "runtime", "--timestamp"])
        calls = self.calls()
        # Notarization checks code in Resources, which --deep never reaches.
        for path in self.machos:
            nested = str(self.app / "Contents/Resources" / path)
            self.assertIn(["codesign", ["--verify", "--strict", nested]], calls)
            self.assertIn(["codesign", ["--display", "--verbose=4", nested]], calls)
        submit = next(index for index, call in enumerate(calls) if call[0] == "xcrun")
        last_signing = max(index for index, (tool, args) in enumerate(calls) if tool == "codesign" and "--sign" in args)
        self.assertLess(last_signing, submit, "everything is signed before notarization")
        self.assertIn(["security", ["list-keychains", "-d", "user", "-s", "/fixture/login with spaces.keychain-db", "/fixture/extra.keychain-db"]], calls)
        self.assertIn("spctl", {call[0] for call in calls})
        self.assertFalse(self.state.exists())
        self.assertNotIn("fixture-p12-password", result.stdout + result.stderr)
        self.assertNotIn("fixture-app-specific-password", result.stdout + result.stderr)
        # An existing packaging job archives only after this successful return.
        self.artifacts.mkdir()
        archive = self.artifacts / "caller-owned-name.zip"
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(self.app), str(archive)],
                       env=self.env, check=True, capture_output=True)
        self.assertEqual(archive.read_text(), "stapled")

    def test_documented_unsigned_step_builds_without_the_signing_wrapper(self):
        self.configure_documented_run()
        self.env.update(APPLE_SIGNING_MODE="unsigned", MOCK_UNWRAPPED_UNSIGNED="1")
        self.assertEqual(self.run_documented_step(self.doc_guard).returncode, 0)
        result = self.run_documented_step(self.doc_unsigned)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0], ["cargo", ["tauri", "build", "--target", TARGET, "--bundles", "app", "--config", "src-tauri/tauri.release.json"]])
        self.assert_sealed("-", ["--timestamp=none"])
        self.assertFalse(self.github_env.exists())
        self.assertFalse(self.state.exists())

    def test_documented_signing_step_and_cleanup_run_with_both_auth_methods(self):
        original = self.env.copy()
        for api in (False, True):
            with self.subTest(api=api):
                self.env = original.copy()
                self.signed(api=api)
                self.configure_documented_run()
                self.assertEqual(self.run_documented_step(self.doc_guard).returncode, 0)
                result = self.run_documented_step(self.doc_signed)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((self.app / "stapled").is_file())
                self.assertFalse(self.state.exists())
                cargo_args = [args for tool, args in self.calls() if tool == "cargo"][-1]
                config = json.loads(cargo_args[cargo_args.index("--config") + 1])
                self.assertTrue(config["bundle"]["macOS"]["hardenedRuntime"])
                self.assertIn("src-tauri/tauri.release.json", cargo_args)
                self.apply_documented_environment()
                cleanup = self.run_documented_step(self.doc_cleanup)
                self.assertEqual(cleanup.returncode, 0, cleanup.stderr)

    def test_documented_cleanup_retries_using_the_exported_state_path(self):
        self.signed()
        self.configure_documented_run()
        self.env.update(MOCK_CARGO_FAIL="1", MOCK_CLEANUP_FAIL="1")
        result = self.run_documented_step(self.doc_signed)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.state.exists())
        self.assertFalse((self.state / "certificate.p12").exists())
        self.assertNotIn("the caller may now archive", result.stdout)
        self.apply_documented_environment()
        self.env.pop("MOCK_CLEANUP_FAIL")
        cleanup = self.run_documented_step(self.doc_cleanup)
        self.assertEqual(cleanup.returncode, 0, cleanup.stderr)
        self.assertFalse(self.state.exists())

    def test_api_authentication_uses_an_ephemeral_key_file(self):
        self.signed(api=True)
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        submit = next(args for tool, args in self.calls() if tool == "xcrun" and args[:2] == ["notarytool", "submit"])
        self.assertIn(str(self.state / "notary-key.p8"), submit)
        self.assertNotIn("--apple-id", submit)
        self.assertFalse(self.state.exists())
        self.assertNotIn("fixture-private-key", result.stdout + result.stderr)

    def test_import_build_notary_stapler_and_gatekeeper_failures_stop_publication(self):
        stages = {"MOCK_IMPORT_FAIL": ("security", "import"), "MOCK_CARGO_FAIL": ("cargo", "tauri"),
                  "MOCK_NOTARY_FAIL": ("xcrun", "notarytool"), "MOCK_STAPLE_FAIL": ("xcrun", "stapler"),
                  "MOCK_GATEKEEPER_FAIL": ("spctl", "--assess")}
        for flag, expected in stages.items():
            with self.subTest(flag=flag):
                self.signed()
                self.env[flag] = "1"
                start = len(self.calls())
                self.assert_failed_and_clean(self.run_build())
                self.assertIn(expected, [(tool, args[0]) for tool, args in self.calls()[start:]])
                self.assertIn(["security", ["list-keychains", "-d", "user", "-s", "/fixture/login with spaces.keychain-db", "/fixture/extra.keychain-db"]], self.calls()[start:])
                self.env.pop(flag)

    def test_nonaccepted_notary_status_never_becomes_a_distributable(self):
        self.signed()
        for status in ("Invalid", "In Progress"):
            with self.subTest(status=status):
                self.env["MOCK_NOTARY_STATUS"] = status
                self.assert_failed_and_clean(self.run_build())

    def test_wrong_team_or_missing_hardened_runtime_fails(self):
        self.signed()
        for flag in ("MOCK_BAD_TEAM", "MOCK_NO_RUNTIME", "MOCK_NO_TIMESTAMP"):
            with self.subTest(flag=flag):
                self.env[flag] = "1"
                self.assert_failed_and_clean(self.run_build())
                self.env.pop(flag)

    def test_missing_app_fails_validation(self):
        self.env["MOCK_NO_APP"] = "1"
        self.assert_failed_and_clean(self.run_build())

    def test_a_missing_or_unstaged_runtime_fails_before_build(self):
        for runtime in (None, self.scratch / "nowhere", self.runtime / "runtime"):
            with self.subTest(runtime=runtime):
                self.env.pop("KOLLAB_APP_RUNTIME", None)
                if runtime is not None:
                    self.env["KOLLAB_APP_RUNTIME"] = str(runtime)
                result = self.run_build()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("KOLLAB_APP_RUNTIME", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_seal_signs_an_existing_build_ad_hoc_once(self):
        self.app = self.scratch / "built/Kollab.app"
        (self.app / "Contents/Resources").mkdir(parents=True)
        result = self.run_script("seal", str(self.app))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_sealed("-", ["--timestamp=none"])
        self.assertNotIn("security", {call[0] for call in self.calls()})
        again = self.run_script("seal", str(self.app))
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("already holds a kollab runtime", again.stderr)
        self.assertNotEqual(self.run_script("seal", "relative/Kollab.app").returncode, 0)

    def test_cleanup_does_not_remove_unowned_or_outside_state(self):
        self.state.mkdir()
        sentinel = self.state / "keep.txt"
        sentinel.write_text("unrelated")
        self.assertNotEqual(self.run_script("cleanup").returncode, 0)
        self.assertTrue(sentinel.exists())
        outside = self.scratch / "nested/kollab-apple-signing-outside"
        outside.mkdir(parents=True)
        (outside / ".owned-by-kollab").touch()
        (outside / "keep.txt").write_text("unrelated")
        self.env["APPLE_SIGNING_STATE_DIR"] = str(outside)
        self.assertNotEqual(self.run_script("cleanup").returncode, 0)
        self.assertTrue((outside / "keep.txt").exists())
        linked = self.scratch / "kollab-apple-signing-symlink"
        linked.symlink_to(outside, target_is_directory=True)
        self.env["APPLE_SIGNING_STATE_DIR"] = str(linked)
        self.assertNotEqual(self.run_script("cleanup").returncode, 0)
        self.assertTrue((outside / "keep.txt").exists())
        self.assertEqual(self.calls(), [])

    def test_cleanup_is_idempotent(self):
        self.assertEqual(self.run_script("cleanup").returncode, 0)
        self.assertEqual(self.run_script("cleanup").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_cleanup_file_removal_errors_cannot_report_success(self):
        self.state.mkdir()
        (self.state / ".owned-by-kollab").touch()
        blocked = self.state / "certificate.p12"
        blocked.mkdir()
        sentinel = blocked / "keep.txt"
        sentinel.write_text("unexpected directory must not be deleted as a key file")
        self.assertNotEqual(self.run_script("cleanup").returncode, 0)
        self.assertTrue(sentinel.is_file())

    def test_cleanup_failure_removes_plaintext_secrets_and_can_be_retried(self):
        self.signed()
        self.env.update(MOCK_CARGO_FAIL="1", MOCK_CLEANUP_FAIL="1")
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.state.exists())
        self.assertFalse((self.state / "certificate.p12").exists())
        self.assertFalse((self.state / "notary-key.p8").exists())
        self.assertFalse(list(self.artifacts.glob("*.zip")))
        self.env.pop("MOCK_CLEANUP_FAIL")
        self.assertEqual(self.run_script("cleanup").returncode, 0)
        self.assertFalse(self.state.exists())

    def test_bad_certificate_encoding_is_cleaned_before_import(self):
        self.signed()
        self.env["APPLE_CERTIFICATE"] = "not base64!"
        self.assert_failed_and_clean(self.run_build())
        self.assertFalse(any(tool == "security" and args[0] == "import" for tool, args in self.calls()))

    def test_cleanup_failure_after_acceptance_cannot_report_success(self):
        self.signed()
        self.env["MOCK_CLEANUP_FAIL"] = "1"
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture-submission", result.stdout)
        self.assertNotIn("the caller may now archive", result.stdout)
        self.assertFalse((self.state / "certificate.p12").exists())
        self.env.pop("MOCK_CLEANUP_FAIL")
        self.assertEqual(self.run_script("cleanup").returncode, 0)


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = load_yaml(WORKFLOW.read_text())
        cls.input_example, cls.job_example = documented_examples()

    def test_active_workflow_only_validates_without_secrets_or_publication(self):
        self.assertEqual(set(self.workflow["jobs"]), {"validate"})
        self.assertNotIn("secrets.", WORKFLOW.read_text())
        self.assertNotIn("actions/upload-artifact", WORKFLOW.read_text())
        self.assertNotIn("pull_request_target", self.workflow["on"])
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_documented_gate_defaults_to_unsigned_for_runs_without_inputs(self):
        mode = self.input_example["on"]["workflow_dispatch"]["inputs"]["apple_signing_mode"]
        self.assertEqual(mode["default"], "unsigned")
        self.assertEqual(mode["options"], ["unsigned", "developer-id"])
        self.assertEqual(self.job_example["env"]["APPLE_SIGNING_MODE"], "${{ inputs.apple_signing_mode || 'unsigned' }}")

    def test_example_secrets_are_scoped_only_to_explicit_main_signing_step(self):
        job = self.job_example
        self.assertNotIn("secrets.", json.dumps(job["env"]))
        secret_steps = []
        for step in job["steps"]:
            if "secrets." in json.dumps(step.get("env", {})):
                secret_steps.append(step)
                self.assertIn("env.APPLE_SIGNING_MODE == 'developer-id'", step["if"])
                self.assertIn("github.ref == 'refs/heads/main'", step["if"])
        self.assertEqual(len(secret_steps), 1)
        self.assertNotIn("continue-on-error", json.dumps(job))
        unsigned = next(step for step in job["steps"] if step.get("if") == "env.APPLE_SIGNING_MODE == 'unsigned'")
        self.assertNotIn("env", unsigned)
        self.assertEqual(unsigned["run"].splitlines(), [
            'cargo tauri build --target "$TARGET" --bundles app --config src-tauri/tauri.release.json',
            'bash scripts/sign-macos.sh seal "$CARGO_TARGET_DIR/$TARGET/release/bundle/macos/Kollab.app"',
        ])
        cleanup = next(step for step in job["steps"] if step.get("run", "").endswith(" cleanup"))
        self.assertEqual(cleanup["if"], "always() && env.APPLE_SIGNING_MODE == 'developer-id'")

    def test_example_mode_guard_rejects_untrusted_refs_and_unknown_modes(self):
        guard = self.job_example["steps"][0]["run"]
        for mode, ref, succeeds in (("unsigned", "refs/tags/v1.2.3", True),
                                    ("developer-id", "refs/heads/main", True),
                                    ("developer-id", "refs/heads/feature", False),
                                    ("automatic", "refs/heads/main", False)):
            with self.subTest(mode=mode, ref=ref):
                result = subprocess.run(["/bin/bash", "-e", "-c", guard], capture_output=True,
                                        env={"APPLE_SIGNING_MODE": mode, "GITHUB_REF": ref})
                self.assertEqual(result.returncode == 0, succeeds)

    def test_workflow_and_example_run_blocks_have_valid_shell_syntax(self):
        steps = self.workflow["jobs"]["validate"]["steps"] + self.job_example["steps"]
        for step in steps:
            if "run" in step:
                with self.subTest(name=step["name"]):
                    result = subprocess.run(["/bin/bash", "-n"], input=step["run"], text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_tauri_config_remains_usable_without_signing_settings(self):
        config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text())
        self.assertNotIn("externalBin", config["bundle"])
        # The kollab runtime is sign-macos.sh's to add and sign, never Tauri's to copy.
        self.assertNotIn("files", config["bundle"]["macOS"])
        self.assertNotIn("signingIdentity", config["bundle"]["macOS"])


if __name__ == "__main__":
    unittest.main()
