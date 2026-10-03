"""The hub_vault trender passes the TaskLedger to rebirth context (#129).

Without it, a restarted agent rendering `<trender type="hub_vault" />` never
saw its active cards and asked its peers for its own assignments.
"""

from __future__ import annotations

from types import SimpleNamespace

from kollabor_ai.prompt_renderer import PromptRenderer


def test_hub_vault_render_passes_the_task_ledger():
    seen = {}

    def get_rebirth_context(**kwargs):
        seen.update(kwargs)
        return "rebirth"

    ledger = object()
    hub = SimpleNamespace(
        get_vault=lambda: SimpleNamespace(get_rebirth_context=get_rebirth_context),
        get_crystal_store=lambda: None,
        _task_ledger=ledger,
    )
    bus = SimpleNamespace(get_service=lambda name: hub if name == "hub_plugin" else None)

    assert PromptRenderer(event_bus=bus)._render_hub_vault() == "rebirth"
    assert seen["task_ledger"] is ledger
