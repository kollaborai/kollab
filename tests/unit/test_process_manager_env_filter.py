"""Tests for SubprocessStrategy env filtering (security fix, issue #38 sibling).

BUG: SubprocessStrategy.spawn() did `env = os.environ.copy()` when
request.env was None, handing the entire parent environment — including
API keys, tokens, passwords — to every spawned subprocess.

FIX: apply _filter_env() on the copied environment before passing it to
subprocess.Popen so that sensitive variables are never inherited by default.
Callers that genuinely need a credential can pass it via SpawnRequest.env.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "packages" / "kollabor-agent" / "src"))

from kollabor_agent.process_manager import SubprocessStrategy, SpawnRequest


class TestFilterEnv(unittest.TestCase):
    """Unit tests for SubprocessStrategy._filter_env."""

    def _filter(self, env: dict) -> dict:
        return SubprocessStrategy._filter_env(env)

    def test_api_key_stripped(self):
        env = {"ANTHROPIC_API_KEY": "sk-ant-secret", "PATH": "/usr/bin"}
        result = self._filter(env)
        self.assertNotIn("ANTHROPIC_API_KEY", result)
        self.assertIn("PATH", result)

    def test_secret_stripped(self):
        env = {"BETTER_AUTH_SECRET": "topsecret", "HOME": "/home/user"}
        result = self._filter(env)
        self.assertNotIn("BETTER_AUTH_SECRET", result)
        self.assertIn("HOME", result)

    def test_token_stripped(self):
        env = {"GITHUB_TOKEN": "ghp_xxxx", "TERM": "xterm-256color"}
        result = self._filter(env)
        self.assertNotIn("GITHUB_TOKEN", result)
        self.assertIn("TERM", result)

    def test_password_stripped(self):
        env = {"DB_PASSWORD": "hunter2", "LANG": "en_US.UTF-8"}
        result = self._filter(env)
        self.assertNotIn("DB_PASSWORD", result)
        self.assertIn("LANG", result)

    def test_credential_stripped(self):
        env = {"AWS_CREDENTIAL": "AKIA...", "USER": "marco"}
        result = self._filter(env)
        self.assertNotIn("AWS_CREDENTIAL", result)
        self.assertIn("USER", result)

    def test_auth_stripped(self):
        env = {"BETTER_AUTH_URL": "https://example.com", "SHELL": "/bin/bash"}
        result = self._filter(env)
        self.assertNotIn("BETTER_AUTH_URL", result)
        self.assertIn("SHELL", result)

    def test_aws_prefix_stripped(self):
        env = {
            "AWS_ACCESS_KEY_ID": "AKIA...",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_DEFAULT_REGION": "us-east-1",
            "PATH": "/usr/bin",
        }
        result = self._filter(env)
        self.assertNotIn("AWS_ACCESS_KEY_ID", result)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", result)
        self.assertNotIn("AWS_DEFAULT_REGION", result)
        self.assertIn("PATH", result)

    def test_anthropic_prefix_stripped(self):
        env = {"ANTHROPIC_API_KEY": "sk-ant", "ANTHROPIC_BETA": "true", "HOME": "/"}
        result = self._filter(env)
        self.assertNotIn("ANTHROPIC_API_KEY", result)
        self.assertNotIn("ANTHROPIC_BETA", result)
        self.assertIn("HOME", result)

    def test_openai_prefix_stripped(self):
        env = {"OPENAI_API_KEY": "sk-openai", "EDITOR": "vim"}
        result = self._filter(env)
        self.assertNotIn("OPENAI_API_KEY", result)
        self.assertIn("EDITOR", result)

    def test_safe_vars_preserved(self):
        env = {
            "PATH": "/usr/bin:/usr/local/bin",
            "HOME": "/home/marco",
            "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color",
            "PYTHONPATH": "/some/path",
            "KOLLAB_HUB_DIR": "/tmp/hub",
        }
        result = self._filter(env)
        self.assertEqual(result, env)

    def test_case_insensitive_match(self):
        """Pattern matching is case-insensitive (upper-case comparison)."""
        env = {
            "anthropic_api_key": "lowercase-key",
            "My_Secret_Value": "value",
            "normal_var": "safe",
        }
        result = self._filter(env)
        self.assertNotIn("anthropic_api_key", result)
        self.assertNotIn("My_Secret_Value", result)
        self.assertIn("normal_var", result)

    def test_empty_env(self):
        self.assertEqual(self._filter({}), {})


class TestSpawnEnvFiltering(unittest.IsolatedAsyncioTestCase):
    """Integration: spawn() must NOT pass raw os.environ to child process."""

    async def test_spawn_with_none_env_filters_secrets(self):
        """When request.env is None, spawn() must filter secrets from os.environ."""
        strategy = SubprocessStrategy()

        injected_env = {}

        def _fake_popen(cmd, **kwargs):
            injected_env.update(kwargs.get("env", {}))
            mock_proc = MagicMock()
            mock_proc.pid = 42
            mock_proc.stdout = MagicMock()
            mock_proc.stdout.__iter__ = MagicMock(return_value=iter([]))
            mock_proc.stdin = MagicMock()
            return mock_proc

        secret_name = "ANTHROPIC_API_KEY"
        secret_val = "sk-ant-test-key"

        fake_environ = {
            secret_name: secret_val,
            "PATH": "/usr/bin",
            "HOME": "/home/user",
        }

        with (
            patch("subprocess.Popen", side_effect=_fake_popen),
            patch("threading.Thread"),
            patch.dict(os.environ, fake_environ, clear=True),
        ):
            request = SpawnRequest(name="test-agent", cmd=["echo", "hi"])
            result = await strategy.spawn(request)

        self.assertTrue(result.success)
        # The secret must NOT be in what Popen received
        self.assertNotIn(secret_name, injected_env,
                         f"{secret_name} leaked into spawned subprocess env")
        self.assertIn("PATH", injected_env, "PATH should survive filtering")

    async def test_spawn_with_explicit_env_is_not_filtered(self):
        """When caller passes request.env explicitly, it is used as-is.

        The caller owns those values and may need to pass credentials
        intentionally (e.g. a worker subprocess that needs a specific token).
        """
        strategy = SubprocessStrategy()
        injected_env = {}

        def _fake_popen(cmd, **kwargs):
            injected_env.update(kwargs.get("env", {}))
            mock_proc = MagicMock()
            mock_proc.pid = 42
            mock_proc.stdout = MagicMock()
            mock_proc.stdout.__iter__ = MagicMock(return_value=iter([]))
            mock_proc.stdin = MagicMock()
            return mock_proc

        explicit_env = {"MY_TOKEN": "explicit-token", "PATH": "/bin"}

        with (
            patch("subprocess.Popen", side_effect=_fake_popen),
            patch("threading.Thread"),
        ):
            request = SpawnRequest(
                name="credentialed-agent",
                cmd=["echo", "hi"],
                env=explicit_env,
            )
            result = await strategy.spawn(request)

        self.assertTrue(result.success)
        # Explicit env is passed through unchanged
        self.assertEqual(injected_env.get("MY_TOKEN"), "explicit-token")


if __name__ == "__main__":
    unittest.main()
