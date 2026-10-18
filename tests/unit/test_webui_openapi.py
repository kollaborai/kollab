"""The web UI's /openapi.json builds without a warning and still carries its title.

One proxy route answers five methods, so it had one operation id and FastAPI
warned "Duplicate Operation ID" four times on every start (the launcher and the
desktop app fetch /openapi.json to see that a Kollab web UI is running).
"""

from __future__ import annotations

import warnings

import pytest

pytest.importorskip("fastapi")


def test_openapi_has_unique_operation_ids_and_the_title_the_launcher_looks_for():
    from kollabor_webui.server import create_app

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        schema = create_app().openapi()

    assert schema["info"]["title"] == "Kollab WebUI"
    ids = [op["operationId"] for item in schema["paths"].values() for op in item.values()]
    assert len(ids) == len(set(ids))
