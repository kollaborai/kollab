"""A venv's python is a symlink to the system interpreter: the sweep must still see kollab there."""

import os
import sys

from plugins.hub.network_halt import Install, Proc, _cli_args


def test_a_venv_kollab_is_found_although_its_exe_is_the_system_python(tmp_path):
    (tmp_path / "bin").mkdir()
    py = tmp_path / "bin" / "python"
    py.symlink_to(os.path.realpath(sys.executable))
    inst = Install(prefix=str(tmp_path.resolve()), scie="")
    mine = Proc(5, 1, [str(py), "-m", "kollabor_cli_main", "--detached"], {}, "/x", os.path.realpath(sys.executable))
    assert _cli_args(mine, inst) == ["--detached"]
    exe = os.path.realpath(sys.executable)
    decoy = Proc(6, 1, [exe, "-c", "import time"], {}, "/x", exe)
    assert _cli_args(decoy, inst) is None
