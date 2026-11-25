"""Short-lived staging worker; managed and terminated by PluginJobs."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    from kollabor.version import get_runtime_version
    from kollabor_plugins.packages import ManifestReviewLimitError, stage_git_source

    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Staging request is too large")
        request = json.loads(raw)
        stage = Path(request["stage"])
        result = stage_git_source(request["source"], stage, get_runtime_version())
        preview = {}
        if request.get("update") is not None:
            from kollabor_engine.plugin_jobs import PluginJobs

            update = request["update"]
            preview = PluginJobs._update_preview(
                {"target_id": update["target_id"], "expected_record": update["expected_record"], "base": stage.parent},
                result,
            )
        response = json.dumps({"ok": True, "result": result, "preview": preview}, ensure_ascii=True).encode("ascii")
        if len(response) > 1024 * 1024:
            raise ValueError("Staging result is too large")
        sys.stdout.buffer.write(response)
        return 0
    except ManifestReviewLimitError:
        return 2
    except Exception:
        # Git output and exception text can contain private source credentials.
        sys.stdout.buffer.write(b'{"ok":false}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
