from datetime import datetime


def test_engine_log_path_uses_project_logs_dir(monkeypatch, tmp_path):
    import kollabor_engine.__main__ as engine_main

    monkeypatch.chdir(tmp_path)

    log_path = engine_main._build_engine_log_path(
        datetime(2026, 5, 7, 14, 32)
    )

    assert log_path == (
        tmp_path.home()
        / ".kollab"
        / "projects"
        / str(tmp_path).strip("/").replace("/", "_")
        / "logs"
        / "kollab-engine-20260507-1432.log"
    )
