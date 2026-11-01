"""Sibling cases of the effort-after-model-change fix: an effort with no category restores by its ID,
a loadout effort survives a later model change, grouped effort values are read by value, a refused
effort after a model change is noticed without raising, and the returned options show the reapplied value."""

import pytest

from tests.unit.test_acp_effort_restore import LOADOUT, _options, _orchestrator


def _capture(orch):
    notices = []
    orch._show_notice = notices.append
    return notices


@pytest.mark.asyncio
async def test_effort_without_category_restores_by_its_id(tmp_path):
    options = _options()
    options[1].pop("category")
    options[1]["currentValue"] = "xhigh"
    orch = _orchestrator(tmp_path, options)
    assert await orch._restore_options(LOADOUT) == []
    assert options[1]["currentValue"] == "max"


@pytest.mark.asyncio
async def test_loadout_effort_survives_a_later_model_change(tmp_path):
    options = _options()
    options[1]["currentValue"] = "xhigh"
    orch = _orchestrator(tmp_path, options)
    notices = _capture(orch)
    assert await orch._restore_options(LOADOUT) == []
    real_set = orch._runner.set_config_option

    async def reset_on_model(config_id, value):
        result = await real_set(config_id, value)
        if config_id == "model":
            options[1]["currentValue"] = "xhigh"
        return result

    orch._runner.set_config_option = reset_on_model
    await orch.set_config_option("model", "sonnet")
    assert options[1]["currentValue"] == "max"
    assert notices == []


@pytest.mark.asyncio
async def test_grouped_effort_values_are_read_by_value_not_as_none(tmp_path):
    options = _options()
    options[1]["options"] = [{"name": "Levels", "options": [{"value": "xhigh"}, {"value": "max"}]}]
    orch = _orchestrator(tmp_path, options)
    notices = _capture(orch)

    async def reset_on_model(config_id, value):
        if config_id == "model":
            options[0]["currentValue"] = value
            options[1]["currentValue"] = "xhigh"
        else:
            options[1]["currentValue"] = value
        return orch._runner.advertised_config_options()

    orch._runner.set_config_option = reset_on_model
    await orch.set_config_option("effort", "max")
    await orch.set_config_option("model", "haiku")
    assert options[1]["currentValue"] == "max"
    assert notices == []


@pytest.mark.asyncio
async def test_refused_effort_after_model_change_is_noticed_and_does_not_raise(tmp_path):
    options = _options()
    orch = _orchestrator(tmp_path, options)
    notices = _capture(orch)
    state = {"switched": False}

    async def refuse_after_model(config_id, value):
        if config_id == "model":
            options[0]["currentValue"] = value
            options[1]["currentValue"] = "xhigh"
            state["switched"] = True
        elif state["switched"]:
            raise RuntimeError("harness refused effort")
        else:
            options[1]["currentValue"] = value
        return orch._runner.advertised_config_options()

    orch._runner.set_config_option = refuse_after_model
    await orch.set_config_option("effort", "max")
    await orch.set_config_option("model", "haiku")
    assert options[0]["currentValue"] == "haiku"
    assert options[1]["currentValue"] == "xhigh"
    assert notices == ["effort max was refused by the new model: harness refused effort"]


@pytest.mark.asyncio
async def test_returned_options_show_the_effort_after_the_reapply(tmp_path):
    options = _options()
    orch = _orchestrator(tmp_path, options)
    _capture(orch)
    await orch.set_config_option("effort", "max")
    real_set = orch._runner.set_config_option

    async def reset_on_model(config_id, value):
        if config_id == "model":
            options[1]["currentValue"] = "xhigh"  # the reply to the model change already shows the reset
        return await real_set(config_id, value)

    orch._runner.set_config_option = reset_on_model
    returned = await orch.set_config_option("model", "haiku")
    assert next(o["currentValue"] for o in returned if o["id"] == "effort") == "max"
