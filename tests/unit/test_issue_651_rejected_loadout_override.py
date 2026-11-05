"""Issue #651: a rejected loadout's model must not come back on the next plain start.

When ``LocalStateService.set_active_profile`` activates an ACP loadout that
starts fine, then rejects the activation (here: the ``--effort`` it was given
is not offered) and no previous harness was running, the rollback used to
leave ``_loadout_override`` set. ``_restore_options`` reads that
override for the same profile, so the next plain start applied the rejected
loadout's model.

The orchestrator is the real ``ACPOrchestrator`` restore logic; only process
start/stop is faked, with the same override bookkeeping as ``_start_owned``.
"""

from unittest.mock import patch

import pytest

from kollabor.state.local import LocalStateService
from kollabor_ai.loadout_manager import Loadout
from tests.unit.test_acp_effort_restore import _options, _orchestrator
from tests.unit.test_issue_594_attach_resolve_before_close import (
    _FakeProfileManager,
    _profile,
    _state,
)


def _wire_start(orch, options):
    """Fake process start/stop that keeps ``_start_owned``'s override rules.

    A restarted harness comes back on its own defaults (model "sonnet").
    """
    orch.started = []

    async def close():
        options[0]["currentValue"] = "sonnet"

    async def start(profile, *, resume_conversation=None, loadout=None):
        orch.started.append((profile.name, loadout))
        if loadout is not None:
            orch._loadout_override = (profile.name, loadout[0], loadout[1])
        elif orch._loadout_override and orch._loadout_override[0] != profile.name:
            orch._loadout_override = None
        await orch._restore_options(profile)

    orch.close, orch.start = close, start
    orch.advertised_config_options = lambda: [dict(o) for o in options]
    orch._session_options = {}
    orch._loadout_override = None


@pytest.mark.asyncio
async def test_rejected_loadout_with_no_previous_harness_is_not_reapplied(tmp_path):
    options = _options()  # offers haiku and sonnet; effort high/xhigh/max
    orch = _orchestrator(tmp_path, options)
    _wire_start(orch, options)
    acp_target = _profile("claude-code", "acp")
    acp_target.model, acp_target.effort = "sonnet", ""
    pm = _FakeProfileManager([acp_target, _profile("default", "anthropic")], "default")
    state = _state(pm, orch)
    loadout = Loadout(name="fast", provider_profile="claude-code", model="haiku")

    with patch("kollabor_ai.loadout_manager.LoadoutManager") as lm_cls:
        lm_cls.return_value.resolve.return_value = (loadout, [])
        with pytest.raises(ValueError, match="does not offer effort"):
            await state.set_active_profile("fast", effort="ultra")

    assert pm.set_calls == []  # nothing persisted: the activation was rejected
    orch._runner.calls.clear()
    await orch.start(acp_target)  # a plain start of the same profile

    assert ("model", "haiku") not in orch._runner.calls
    assert options[0]["currentValue"] == "sonnet"  # the profile's own model
    assert orch._loadout_override is None
