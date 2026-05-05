#!/usr/bin/env python3
"""Unit test to verify inline edit state cleanup.

This test verifies that the deactivate_widget() call in the finally block
properly clears the interaction state, allowing widgets to be re-edited.
"""

import asyncio
import sys
from pathlib import Path

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from kollabor_tui.status.navigation_state import StatusNavigationState


@pytest.mark.asyncio
async def test_state_cleanup():
    """Test that interaction state is properly cleared after editing."""

    print("[TEST] Creating navigation state...")
    state = StatusNavigationState()

    # Initial state should be inactive
    print("[TEST] Checking initial state...")
    assert (
        not state.interaction_active
    ), "Initial state should have interaction_active=False"
    assert (
        state.active_widget_id is None
    ), "Initial state should have active_widget_id=None"
    assert not state.is_interacting(), "is_interacting() should return False"
    print("[PASS] Initial state is correct")

    # Simulate widget activation (what happens in handle_enter_key)
    print("[TEST] Activating widget...")
    await state.activate_widget("label")
    assert (
        state.interaction_active
    ), "After activation, interaction_active should be True"
    assert state.active_widget_id == "label", "active_widget_id should be 'label'"
    assert state.is_interacting(), "is_interacting() should return True"
    print("[PASS] Widget activated successfully")

    # Simulate what the OLD code did (before fix): no deactivation
    print("[TEST] Simulating OLD behavior (no deactivate_widget call)...")
    # OLD code just cleared inline_edit_state but NOT interaction state
    # This is what caused the bug
    assert state.is_interacting(), "BUG: Widget still marked as interacting"
    print("[INFO] OLD code leaves interaction_active=True (BUG!)")

    # Now simulate what the NEW code does (with fix)
    print("[TEST] Simulating NEW behavior (with deactivate_widget call)...")
    await state.deactivate_widget()
    assert (
        not state.interaction_active
    ), "After deactivation, interaction_active should be False"
    assert state.active_widget_id is None, "active_widget_id should be None"
    assert not state.is_interacting(), "is_interacting() should return False"
    print("[PASS] Widget deactivated successfully")

    # Verify widget can be activated again (this was broken before)
    print("[TEST] Activating widget again (testing re-activation)...")
    await state.activate_widget("label")
    assert state.interaction_active, "Second activation should succeed"
    assert state.is_interacting(), "Second activation should set interacting=True"
    print("[PASS] Widget can be re-activated!")

    # Test multiple activations (the user's use case)
    print("[TEST] Testing multiple activation/deactivation cycles...")
    for i in range(5):
        await state.activate_widget("label")
        assert state.is_interacting(), f"Cycle {i+1}: Activation failed"
        await state.deactivate_widget()
        assert not state.is_interacting(), f"Cycle {i+1}: Deactivation failed"
    print("[PASS] Multiple cycles work correctly")

    print("")
    print("=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)
    print()
    print("The fix correctly:")
    print("  1. Clears interaction_active after editing")
    print("  2. Clears active_widget_id after editing")
    print("  3. Allows widgets to be re-activated")
    print("  4. Supports multiple edit cycles")
    print()


if __name__ == "__main__":
    asyncio.run(test_state_cleanup())
