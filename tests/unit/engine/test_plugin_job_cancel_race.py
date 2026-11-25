"""A cancel that lands while a failed staging job cleans up keeps its cancelling state (#1028)."""

import asyncio
from types import SimpleNamespace

from kollabor_engine.plugin_jobs import PluginJobs


def test_failed_staging_does_not_overwrite_a_cancel_in_progress(tmp_path, monkeypatch):
    jobs = PluginJobs(SimpleNamespace(publish_change=lambda: None), None, None)

    async def failing_stage(*_args, **_kwargs):
        raise RuntimeError("clone failed")

    async def cleanup_while_cancel_arrives(job):
        job["status"] = "cancelling"  # cancel() runs during this await
        return True, False

    monkeypatch.setattr(jobs, "_run_staging", failing_stage)
    monkeypatch.setattr(jobs, "_run_cleanup", cleanup_while_cancel_arrives)
    job = {"action": "install", "requested_source": "https://example.invalid/repo", "base": tmp_path,
           "status": "staging"}
    asyncio.run(jobs._stage(job))
    assert job["status"] == "cancelling"
