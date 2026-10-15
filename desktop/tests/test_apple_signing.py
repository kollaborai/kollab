"""Run without the application/pytest environment; Apple tools are fixture executables."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "desktop/scripts/package-macos.sh"
WORKFLOW = ROOT / ".github/workflows/desktop-macos-signing.yml"
TARGET = "aarch64-apple-darwin"

MOCK_TOOL = r'''
import json, os, pathlib, sys
tool, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["MOCK_LOG"], "a") as stream:
    stream.write(json.dumps([tool, args]) + "\n")
state = pathlib.Path(os.environ["APPLE_SIGNING_STATE_DIR"])
app = pathlib.Path(os.environ["CARGO_TARGET_DIR"]) / os.environ.get("MOCK_TARGET", "aarch64-apple-darwin") / "release/bundle/macos/Kollab.app"
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
    assert not any(os.environ.get(name) for name in ("APPLE_CERTIFICATE", "APPLE_CERTIFICATE_PASSWORD", "APPLE_ID", "APPLE_PASSWORD", "APPLE_API_KEY", "APPLE_API_ISSUER", "APPLE_API_PRIVATE_KEY", "APPLE_API_KEY_PATH"))
    expected = "Developer ID Application: Fixture (FIXTURETEAM)" if os.environ.get("MOCK_SIGNED") else "-"
    assert os.environ["APPLE_SIGNING_IDENTITY"] == expected
    (app / "Contents/MacOS").mkdir(parents=True, exist_ok=True)
    (app / "stapled").unlink(missing_ok=True)
    if not os.environ.get("MOCK_NO_SIDECAR"):
        (app / "Contents/MacOS/kollab").write_text("fixture sidecar")
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


class PackagingContractTests(unittest.TestCase):
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
        self.state = self.scratch / "kollab-apple-signing-fixture"
        self.artifacts = self.scratch / "artifacts"
        # Exclude the developer's venv and keychain state from all fixture tools.
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("APPLE_")}
        self.env.update(
            PATH=str(tools) + ":/usr/bin:/bin:/usr/sbin:/sbin",
            PYTHONDONTWRITEBYTECODE="1",
            RUNNER_TEMP=str(self.scratch),
            APPLE_SIGNING_STATE_DIR=str(self.state),
            APPLE_ARTIFACT_DIR=str(self.artifacts),
            CARGO_TARGET_DIR=str(self.scratch / "cargo-target"),
            MOCK_LOG=str(self.scratch / "tools.jsonl"),
        )

    def signed(self, api=False):
        self.env.update(
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
        return subprocess.run(["/bin/bash", str(SCRIPT), *args], cwd=ROOT, env=self.env, capture_output=True, text=True)

    def calls(self):
        path = self.scratch / "tools.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_failed_without_archive(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(list(self.artifacts.glob("*.zip")))
        self.assertFalse(self.state.exists(), "owned signing state must be cleaned on failure")

    def test_unsigned_uses_no_apple_services_or_signing_state(self):
        result = self.run_script("package", "unsigned", TARGET)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({call[0] for call in self.calls()}, {"cargo", "ditto"})
        self.assertFalse(self.state.exists())
        self.assertTrue((self.artifacts / "Kollab-macos-aarch64-unsigned.zip.sha256").is_file())

    def test_unsigned_ignores_inherited_apple_credentials(self):
        self.signed()
        self.env.pop("MOCK_SIGNED")
        result = self.run_script("package", "unsigned", TARGET)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("security", {call[0] for call in self.calls()})

    def test_intel_uses_the_existing_target_and_a_distinct_archive(self):
        self.env["MOCK_TARGET"] = "x86_64-apple-darwin"
        result = self.run_script("package", "unsigned", "x86_64-apple-darwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.artifacts / "Kollab-macos-x86_64-unsigned.zip").is_file())

    def test_requested_signing_without_secrets_fails_before_build(self):
        result = self.run_script("preflight", "developer-id")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("APPLE_CERTIFICATE", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_partial_or_ambiguous_authentication_fails_closed(self):
        self.signed()
        self.env["APPLE_API_KEY"] = "fixture-key"
        self.assertNotEqual(self.run_script("preflight", "developer-id").returncode, 0)
        self.env.update(APPLE_API_ISSUER="issuer", APPLE_API_PRIVATE_KEY="fixture-private-key")
        self.assertNotEqual(self.run_script("preflight", "developer-id").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_development_certificate_is_rejected(self):
        self.signed()
        self.env["APPLE_SIGNING_IDENTITY"] = "Apple Development: Fixture"
        self.assertNotEqual(self.run_script("preflight", "developer-id").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_signed_archive_is_created_after_acceptance_and_stapling(self):
        self.signed()
        result = self.run_script("package", "developer-id", TARGET)
        self.assertEqual(result.returncode, 0, result.stderr)
        archive = self.artifacts / "Kollab-macos-aarch64-developer-id.zip"
        self.assertEqual(archive.read_text(), "stapled")
        calls = self.calls()
        self.assertIn(["security", ["list-keychains", "-d", "user", "-s", "/fixture/login with spaces.keychain-db", "/fixture/extra.keychain-db"]], calls)
        self.assertIn("spctl", {call[0] for call in calls})
        self.assertFalse(self.state.exists())
        self.assertNotIn("fixture-p12-password", result.stdout + result.stderr)
        self.assertNotIn("fixture-app-specific-password", result.stdout + result.stderr)

    def test_api_authentication_uses_an_ephemeral_key_file(self):
        self.signed(api=True)
        result = self.run_script("package", "developer-id", TARGET)
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
                self.assert_failed_without_archive(self.run_script("package", "developer-id", TARGET))
                self.assertIn(expected, [(tool, args[0]) for tool, args in self.calls()[start:]])
                self.assertIn(["security", ["list-keychains", "-d", "user", "-s", "/fixture/login with spaces.keychain-db", "/fixture/extra.keychain-db"]], self.calls()[start:])
                self.env.pop(flag)

    def test_nonaccepted_notary_status_never_becomes_a_distributable(self):
        self.signed()
        for status in ("Invalid", "In Progress"):
            with self.subTest(status=status):
                self.env["MOCK_NOTARY_STATUS"] = status
                self.assert_failed_without_archive(self.run_script("package", "developer-id", TARGET))

    def test_wrong_team_or_missing_hardened_runtime_fails(self):
        self.signed()
        for flag in ("MOCK_BAD_TEAM", "MOCK_NO_RUNTIME", "MOCK_NO_TIMESTAMP"):
            with self.subTest(flag=flag):
                self.env[flag] = "1"
                self.assert_failed_without_archive(self.run_script("package", "developer-id", TARGET))
                self.env.pop(flag)

    def test_missing_sidecar_fails_before_archiving(self):
        self.env["MOCK_NO_SIDECAR"] = "1"
        self.assert_failed_without_archive(self.run_script("package", "unsigned", TARGET))

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

    def test_cleanup_failure_removes_plaintext_secrets_and_can_be_retried(self):
        self.signed()
        self.env.update(MOCK_CARGO_FAIL="1", MOCK_CLEANUP_FAIL="1")
        result = self.run_script("package", "developer-id", TARGET)
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
        self.assert_failed_without_archive(self.run_script("package", "developer-id", TARGET))
        self.assertFalse(any(tool == "security" and args[0] == "import" for tool, args in self.calls()))


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ruby's standard YAML parser keeps this test independent of pytest/PyYAML.
        cls.workflow = json.loads(subprocess.check_output([
            "ruby", "-rjson", "-ryaml", "-e", "puts JSON.generate(YAML.load_file(ARGV.fetch(0)))", str(WORKFLOW)
        ], text=True))

    def test_unsigned_is_the_default_and_prs_only_validate(self):
        self.assertEqual(self.workflow["on"]["workflow_dispatch"]["inputs"]["mode"]["default"], "unsigned")
        self.assertEqual(self.workflow["jobs"]["package"]["if"], "github.event_name == 'workflow_dispatch'")
        self.assertNotIn("pull_request_target", self.workflow["on"])
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_secrets_are_scoped_only_to_explicit_main_signing_steps(self):
        job = self.workflow["jobs"]["package"]
        self.assertNotIn("secrets.", json.dumps(job["env"]))
        for step in job["steps"]:
            if "secrets." in json.dumps(step.get("env", {})):
                self.assertIn("inputs.mode == 'developer-id'", step["if"])
                self.assertIn("github.ref == 'refs/heads/main'", step["if"])
        cleanup = next(step for step in job["steps"] if step.get("run", "").endswith(" cleanup"))
        self.assertEqual(cleanup["if"], "always()")

    def test_existing_macos_targets_sidecar_and_asset_names_stay_compatible(self):
        matrix = self.workflow["jobs"]["package"]["strategy"]["matrix"]["include"]
        self.assertEqual({item["target"] for item in matrix}, {"aarch64-apple-darwin", "x86_64-apple-darwin"})
        source = WORKFLOW.read_text()
        self.assertIn('shasum -a 256 -c "kollab-macos-$ARCH.sha256"', source)
        self.assertIn('desktop/src-tauri/binaries/kollab-$TARGET', source)
        config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text())
        self.assertEqual(config["bundle"]["externalBin"], ["binaries/kollab"])
        self.assertNotIn("signingIdentity", config["bundle"]["macOS"])


if __name__ == "__main__":
    unittest.main()
