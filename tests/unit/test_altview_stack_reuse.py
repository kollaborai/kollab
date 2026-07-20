"""Regression: one-shot AltView flows must not re-enter a stale cached session.

Bug: running ``/setup``, cancelling with Esc, then running ``/setup`` again
printed "setup did not complete" instead of re-opening the wizard (and ``/model``
silently reported "cancelled" on its second run).

Root cause: ``AltViewStackManager.push()`` cached sessions by name and, on the
next call, re-entered the *previous* view instance (via ``on_resume()``, still
holding old result flags) while the command handler read result flags off the
*fresh* view it had just built and passed in. The two instances diverged, so the
handler always saw pristine (all-False) flags after the first run.

``reuse=False`` makes ``push()`` run exactly the instance it was handed and never
cache it. These tests pin both the old semantics (reuse=True re-enters the cache)
and the fix (reuse=False runs the passed view every time).
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from kollabor_tui.altview.session import AltViewSession
from kollabor_tui.altview.stack_manager import AltViewStackManager


def _run(coro):
    return asyncio.run(coro)


class _FakeView:
    """Minimal stand-in accepted by AltViewSession + push()."""

    target_fps = 30.0
    metadata = None


class _FakeEventBus:
    def __init__(self):
        self.emit_with_hooks = AsyncMock()

    def get_service(self, *_args, **_kwargs):
        return None


class AltViewStackReuseTest(unittest.TestCase):
    def _make_manager(self, ran):
        mgr = AltViewStackManager(_FakeEventBus(), None)
        # Neutralise main-UI hibernation/restore so push() exercises only its
        # session-lifecycle bookkeeping.
        mgr._resolve_main_render_loop = lambda: None
        mgr._resolve_scheduler = lambda: None

        async def _fake_pop(session=None, **_kwargs):
            if session is not None:
                ran.append(session.altview)
                if session in mgr._stack:
                    mgr._stack.remove(session)

        mgr._pop_current = _fake_pop
        return mgr

    def test_reuse_true_reenters_cached_view(self):
        """Old semantics: a 2nd push with the same name re-enters the 1st view."""
        ran = []
        mgr = self._make_manager(ran)
        a, b = _FakeView(), _FakeView()

        with patch.object(AltViewSession, "enter", AsyncMock()), patch.object(
            AltViewSession, "run_loop", AsyncMock()
        ), patch.object(AltViewSession, "destroy", AsyncMock()):
            _run(mgr.push(a, "view", reuse=True))
            _run(mgr.push(b, "view", reuse=True))

        # The cached session wrapping `a` is re-entered; `b` is ignored.
        self.assertEqual(ran, [a, a])

    def test_reuse_false_runs_the_view_it_was_given(self):
        """Fixed semantics: each push runs exactly the instance handed to it."""
        ran = []
        mgr = self._make_manager(ran)
        a, b = _FakeView(), _FakeView()

        with patch.object(AltViewSession, "enter", AsyncMock()), patch.object(
            AltViewSession, "run_loop", AsyncMock()
        ), patch.object(AltViewSession, "destroy", AsyncMock()):
            _run(mgr.push(a, "setup", reuse=False))
            # One-shot session must not linger in the registry between runs.
            self.assertNotIn("setup", mgr._session_registry)
            _run(mgr.push(b, "setup", reuse=False))

        self.assertEqual(ran, [a, b])


if __name__ == "__main__":
    unittest.main()
