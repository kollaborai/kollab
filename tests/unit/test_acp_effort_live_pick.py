"""A live effort pick on an uncategorized option with ID 'effort' is recorded, so a later model change keeps it."""

import pytest

from tests.unit.test_acp_effort_restore import _options, _orchestrator


@pytest.mark.asyncio
async def test_live_pick_on_uncategorized_effort_survives_model_change(tmp_path):
    options = _options()
    options[1].pop("category")
    orch = _orchestrator(tmp_path, options)
    notices = []
    orch._show_notice = notices.append
    await orch.set_config_option("effort", "max")
    real_set = orch._runner.set_config_option

    async def reset_on_model(config_id, value):
        if config_id == "model":
            options[1]["currentValue"] = "xhigh"
        return await real_set(config_id, value)

    orch._runner.set_config_option = reset_on_model
    await orch.set_config_option("model", "haiku")
    assert options[1]["currentValue"] == "max"
    assert notices == []
