"""Effort after a model change: found by category, never a fixed ID; an effort the new model does not
offer falls back to the highest it offers; a dropped effort option is named, never silent."""

import pytest

from tests.unit.test_acp_effort_restore import _options, _orchestrator


def _capture(orch):
    notices = []
    orch._show_notice = notices.append
    return notices


@pytest.mark.asyncio
async def test_effort_pick_survives_model_change_when_the_effort_id_is_renamed(tmp_path):
    options = _options()
    options[1]["id"] = "reasoning_budget"
    orch = _orchestrator(tmp_path, options)
    notices = _capture(orch)
    await orch.set_config_option("reasoning_budget", "max")
    real_set = orch._runner.set_config_option

    async def reset_and_rename(config_id, value):
        result = await real_set(config_id, value)
        if config_id == "model":
            options[1]["currentValue"] = "xhigh"
            options[1]["id"] = "effort"
        return result

    orch._runner.set_config_option = reset_and_rename
    await orch.set_config_option("model", "haiku")
    assert options[1]["currentValue"] == "max"
    assert notices == []


@pytest.mark.asyncio
async def test_missing_effort_option_after_model_change_is_noticed_not_silent(tmp_path):
    options = _options()
    orch = _orchestrator(tmp_path, options)
    notices = _capture(orch)
    await orch.set_config_option("effort", "max")
    real_set = orch._runner.set_config_option

    async def drop_effort(config_id, value):
        result = await real_set(config_id, value)
        if config_id == "model":
            options.pop(1)
        return result

    orch._runner.set_config_option = drop_effort
    await orch.set_config_option("model", "haiku")
    assert notices == ["effort max was not kept: the new model advertises no effort option"]


@pytest.mark.asyncio
async def test_unoffered_effort_applies_highest_offered_with_one_notice(tmp_path):
    options = _options()
    orch = _orchestrator(tmp_path, options)
    notices = _capture(orch)
    await orch.set_config_option("effort", "max")
    real_set = orch._runner.set_config_option

    async def narrow_on_model(config_id, value):
        result = await real_set(config_id, value)
        if config_id == "model":
            options[1]["options"] = [{"value": "high"}, {"value": "xhigh"}]
            options[1]["currentValue"] = "xhigh"
        return result

    orch._runner.set_config_option = narrow_on_model
    await orch.set_config_option("model", "haiku")
    assert options[1]["currentValue"] == "xhigh"
    assert notices == ["effort max is not offered by the new model; using xhigh, the highest it offers"]


@pytest.mark.asyncio
async def test_model_change_without_category_still_reapplies_effort(tmp_path):
    options = _options()
    options[0].pop("category")
    orch = _orchestrator(tmp_path, options)
    notices = _capture(orch)
    await orch.set_config_option("effort", "max")
    real_set = orch._runner.set_config_option

    async def reset_on_model(config_id, value):
        result = await real_set(config_id, value)
        if config_id == "model":
            options[1]["currentValue"] = "xhigh"
        return result

    orch._runner.set_config_option = reset_on_model
    await orch.set_config_option("model", "haiku")
    assert options[1]["currentValue"] == "max"
    assert notices == []
