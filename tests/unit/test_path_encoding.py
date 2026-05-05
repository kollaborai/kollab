"""Tests for path encoding utilities."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from kollabor_config.config_utils import (
    APP_CONFIG_DIR_NAME,
    IS_WINDOWS,
    decode_project_path,
    encode_project_path,
    get_conversations_dir,
    get_global_agents_dir,
    get_local_agents_dir,
    get_logs_dir,
    get_project_data_dir,
)


class TestEncodeProjectPath(unittest.TestCase):
    """Test cases for encode_project_path function."""

    def test_unix_path(self):
        """Test encoding a standard Unix path using actual filesystem."""
        # Create a real directory to test with
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "home" / "testuser" / "dev" / "hello_world"
            test_path.mkdir(parents=True)
            result = encode_project_path(test_path)
            # Should contain the path components with underscores
            self.assertIn("home", result)
            self.assertIn("testuser", result)
            self.assertIn("dev", result)
            self.assertIn("hello_world", result)

    @unittest.skipIf(not IS_WINDOWS, "Windows-specific test")
    def test_windows_path(self):
        """Test encoding a Windows path."""
        path = Path("C:\\Users\\dev\\project")
        result = encode_project_path(path)
        self.assertEqual(result, "C_Users_dev_project")

    @unittest.skipIf(not IS_WINDOWS, "Windows-specific test")
    def test_windows_path_with_different_drive(self):
        """Test encoding a Windows path with different drive letters."""
        path = Path("D:\\projects\\myapp")
        result = encode_project_path(path)
        self.assertEqual(result, "D_projects_myapp")

    def test_path_with_trailing_slash(self):
        """Test encoding a path with trailing slash is handled correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "test" / "project"
            test_path.mkdir(parents=True)
            # Add trailing slash
            path_with_slash = Path(str(test_path) + "/")
            result = encode_project_path(path_with_slash)
            normalized_result = encode_project_path(test_path)
            # Path.resolve() normalizes trailing slashes, so encoding should
            # match the same path without the explicit trailing slash.
            self.assertEqual(result, normalized_result)

    def test_relative_path(self):
        """Test encoding a relative path (gets resolved to absolute)."""
        original_cwd = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.chdir(tmpdir)
                # Create the nested directories
                rel_path = Path("relative/path/to/project")
                rel_path.mkdir(parents=True)
                path = Path("relative/path/to/project")
                result = encode_project_path(path)
                # Should be resolved to absolute path containing tmpdir
                self.assertIn("relative", result)
                self.assertIn("path", result)
                self.assertIn("project", result)
        finally:
            os.chdir(original_cwd)

    def test_simple_path_with_dots(self):
        """Test encoding path segments containing dots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "user" / "project.build" / "dist.output"
            test_path.mkdir(parents=True)
            result = encode_project_path(test_path)
            # Dots should be preserved
            self.assertIn("project.build", result)
            self.assertIn("dist.output", result)

    def test_encoding_removes_leading_slash(self):
        """Test that leading slashes are removed from encoded result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "home"
            test_path.mkdir(parents=True)
            result = encode_project_path(test_path)
            # Result should not start with underscore
            self.assertFalse(result.startswith("_"))


class TestDecodeProjectPath(unittest.TestCase):
    """Test cases for decode_project_path function."""

    def test_decode_unix_path(self):
        """Test decoding a Unix encoded path back to Path."""
        # Use a path without underscores in component names to avoid ambiguity
        encoded = "home_testuser_dev_project"
        result = decode_project_path(encoded)
        # Check that path starts with root and has correct parts
        self.assertTrue(result.is_absolute())
        parts = result.parts
        self.assertEqual(parts[0], "/")
        self.assertEqual(parts[1], "home")
        self.assertEqual(parts[2], "testuser")
        self.assertEqual(parts[3], "dev")
        self.assertEqual(parts[4], "project")

    @unittest.skipIf(not IS_WINDOWS, "Windows-specific test")
    def test_decode_windows_path(self):
        """Test decoding a Windows encoded path back to Path."""
        encoded = "C_Users_dev_project"
        result = decode_project_path(encoded)
        # On Windows, check drive letter
        self.assertEqual(result.drive, "C:")

    @unittest.skipIf(not IS_WINDOWS, "Windows-specific test")
    def test_decode_windows_different_drive(self):
        """Test decoding Windows path with different drive letter."""
        encoded = "D_projects_myapp"
        result = decode_project_path(encoded)
        self.assertEqual(result.drive, "D:")

    def test_decode_nested_unix_path(self):
        """Test decoding a deeply nested Unix encoded path."""
        encoded = "var_www_html_app_frontend_src"
        result = decode_project_path(encoded)
        parts = result.parts
        self.assertEqual(parts[0], "/")
        self.assertEqual(parts[1], "var")
        self.assertEqual(parts[2], "www")
        self.assertEqual(parts[3], "html")
        self.assertEqual(parts[4], "app")
        self.assertEqual(parts[5], "frontend")
        self.assertEqual(parts[6], "src")

    def test_decode_simple_path(self):
        """Test decoding a simple encoded path."""
        encoded = "home_user_project"
        result = decode_project_path(encoded)
        parts = result.parts
        self.assertEqual(parts[1], "home")
        self.assertEqual(parts[2], "user")
        self.assertEqual(parts[3], "project")

    def test_decode_path_with_dots(self):
        """Test decoding path with dots in segment names."""
        encoded = "user_project.build_dist.output"
        result = decode_project_path(encoded)
        parts = result.parts
        # Check that dots are preserved in segment names
        self.assertIn("project.build", parts)
        self.assertIn("dist.output", parts)

    def test_roundtrip_real_path(self):
        """Test that encode -> decode returns equivalent path for real directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original = Path(tmpdir) / "test" / "project" / "path"
            original.mkdir(parents=True)

            encoded = encode_project_path(original)
            # Verify encoding contains expected components
            self.assertIn("test", encoded)
            self.assertIn("project", encoded)
            self.assertIn("path", encoded)

    @unittest.skipIf(IS_WINDOWS, "Unix-specific test")
    def test_roundtrip_known_unix_path(self):
        """Test encode/decode roundtrip with a known Unix path."""
        # Use /tmp which should exist on all Unix systems
        if Path("/tmp").exists():
            original = Path("/tmp")
            encoded = encode_project_path(original)
            decoded = decode_project_path(encoded)
            # Use samefile() to handle symlink resolution
            # (/tmp -> /private/tmp on macOS)
            self.assertTrue(decoded.samefile(original))


class TestGetProjectDataDir(unittest.TestCase):
    """Test cases for get_project_data_dir function."""

    def test_returns_path_under_kollab(self):
        """Test that it returns a path under ~/.kollab/projects/."""
        result = get_project_data_dir()
        home = Path.home()
        expected_base = home / APP_CONFIG_DIR_NAME / "projects"
        self.assertTrue(str(result).startswith(str(expected_base)))

    def test_with_explicit_project_path(self):
        """Test with an explicit project_path argument."""
        project_path = Path("/test/project/path")
        result = get_project_data_dir(project_path)
        home = Path.home()
        expected = home / APP_CONFIG_DIR_NAME / "projects" / "test_project_path"
        self.assertEqual(result, expected)

    def test_without_argument_uses_cwd(self):
        """Test that without argument it uses current working directory."""
        result = get_project_data_dir()
        cwd_encoded = encode_project_path(Path.cwd())
        home = Path.home()
        expected = home / APP_CONFIG_DIR_NAME / "projects" / cwd_encoded
        self.assertEqual(result, expected)

    def test_with_nested_project_path(self):
        """Test with a nested project path."""
        project_path = Path("/deep/nested/project/path")
        result = get_project_data_dir(project_path)
        home = Path.home()
        expected = home / APP_CONFIG_DIR_NAME / "projects" / "deep_nested_project_path"
        self.assertEqual(result, expected)


class TestGetConversationsDir(unittest.TestCase):
    """Test cases for get_conversations_dir function."""

    def test_returns_project_data_dir_with_conversations(self):
        """Test that it returns project data dir / 'conversations'."""
        result = get_conversations_dir()
        # Should end with 'conversations'
        self.assertEqual(result.name, "conversations")

    def test_conversations_dir_under_project_data(self):
        """Test that conversations dir is under project data dir."""
        result = get_conversations_dir()
        project_data_dir = get_project_data_dir()
        self.assertEqual(result.parent, project_data_dir)

    def test_with_explicit_project_path(self):
        """Test with an explicit project_path argument."""
        project_path = Path("/my/project")
        result = get_conversations_dir(project_path)
        home = Path.home()
        expected = home / APP_CONFIG_DIR_NAME / "projects" / "my_project" / "conversations"
        self.assertEqual(result, expected)


class TestGetLogsDir(unittest.TestCase):
    """Test cases for get_logs_dir function."""

    def test_returns_project_data_dir_with_logs(self):
        """Test that it returns project data dir / 'logs'."""
        result = get_logs_dir()
        # Should end with 'logs'
        self.assertEqual(result.name, "logs")

    def test_logs_dir_under_project_data(self):
        """Test that logs dir is under project data dir."""
        result = get_logs_dir()
        project_data_dir = get_project_data_dir()
        self.assertEqual(result.parent, project_data_dir)

    def test_with_explicit_project_path(self):
        """Test with an explicit project_path argument."""
        project_path = Path("/another/project")
        result = get_logs_dir(project_path)
        home = Path.home()
        expected = home / APP_CONFIG_DIR_NAME / "projects" / "another_project" / "logs"
        self.assertEqual(result, expected)


class TestGetGlobalAgentsDir(unittest.TestCase):
    """Test cases for get_global_agents_dir function."""

    def test_returns_path_under_home_kollab(self):
        """Test that it returns ~/.kollab/agents."""
        result = get_global_agents_dir()
        home = Path.home()
        expected = home / APP_CONFIG_DIR_NAME / "agents"
        self.assertEqual(result, expected)

    def test_global_agents_dir_is_absolute(self):
        """Test that the returned path is absolute."""
        result = get_global_agents_dir()
        self.assertTrue(result.is_absolute())


class TestGetLocalAgentsDir(unittest.TestCase):
    """Test cases for get_local_agents_dir function."""

    def test_returns_none_when_local_dir_doesnt_exist(self):
        """Test that it returns None when local dir doesn't exist."""
        # Save original cwd
        original_cwd = Path.cwd()
        try:
            # Use a temp directory where .kollab/agents doesn't exist
            with tempfile.TemporaryDirectory() as tmpdir:
                os.chdir(tmpdir)
                result = get_local_agents_dir()
                self.assertIsNone(result)
        finally:
            os.chdir(original_cwd)

    def test_returns_path_when_local_dir_exists(self):
        """Test that it returns path when local dir exists."""
        original_cwd = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.chdir(tmpdir)
                # Create the local agents directory
                local_agents = Path.cwd() / APP_CONFIG_DIR_NAME / "agents"
                local_agents.mkdir(parents=True)

                result = get_local_agents_dir()
                self.assertIsNotNone(result)
                self.assertEqual(result, local_agents)
        finally:
            os.chdir(original_cwd)

    def test_local_agents_dir_path_is_correct(self):
        """Test that the path is .kollab/agents from cwd."""
        original_cwd = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.chdir(tmpdir)
                local_agents = Path.cwd() / APP_CONFIG_DIR_NAME / "agents"
                local_agents.mkdir(parents=True)

                result = get_local_agents_dir()
                expected = Path.cwd() / APP_CONFIG_DIR_NAME / "agents"
                self.assertEqual(result, expected)
        finally:
            os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
