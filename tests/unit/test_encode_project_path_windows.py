"""Windows project-folder encoding for encode_project_path (#439).

str(Path("C:\\Users\\x").resolve()) keeps the "C:" drive, and joining that
name under projects/ makes Windows read "C:" as a drive-relative prefix:
the daemon's folder becomes projects\\_Users_x while readers look for
C_Users_x and LocalDirectory never finds the agent. The encoder must drop
the drive colon; POSIX output must stay byte-identical.
"""

import sys
import unittest
from pathlib import Path, PureWindowsPath

sys.path.append(str(Path(__file__).parent.parent.parent))

from kollabor_config.config_utils import decode_project_path, encode_project_path


class _ResolvedProject:
    """Object whose resolve() reports a fixed path, runnable on any platform.

    resolve() is pinned because the host may canonicalize paths (macOS
    firmlinks map /home to /System/Volumes/Data/home), which would test the
    host's resolver instead of the encoder's transformation.
    """

    def __init__(self, path):
        self._path = path

    def resolve(self):
        return self._path


class TestEncodeProjectPathWindows(unittest.TestCase):
    def test_drive_colon_is_dropped(self):
        project = _ResolvedProject(PureWindowsPath("C:\\Users\\dev\\project"))
        self.assertEqual(encode_project_path(project), "C_Users_dev_project")

    def test_encoded_windows_name_decodes_back(self):
        encoded = encode_project_path(_ResolvedProject(PureWindowsPath("C:\\Users\\dev\\project")))
        self.assertEqual(str(decode_project_path(encoded)), "C:\\Users\\dev\\project")

    def test_posix_encoding_is_unchanged(self):
        project = _ResolvedProject(Path("/home/user/dev/x"))
        self.assertEqual(encode_project_path(project), "home_user_dev_x")

    def test_posix_real_paths_stay_byte_identical(self):
        # POSIX paths have an empty drive, so the output must equal the
        # previous transformation of str(resolve()) on real paths too.
        for real in (Path.cwd(), Path.home()):
            expected = str(real.resolve()).replace("/", "_").replace("\\", "_")
            while expected.startswith("_"):
                expected = expected[1:]
            self.assertEqual(encode_project_path(real), expected)


if __name__ == "__main__":
    unittest.main()
