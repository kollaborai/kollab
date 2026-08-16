"""Skip-if-unchanged flushing in the fullscreen renderer.

The renderer flushes a frame only when it differs from the previous one: an
identical frame (a timer-driven view sitting idle) writes nothing, which kills
the idle flicker. A frame that *did* change is written in full -- its buffer
starts with clear_screen's \\033[2J -- so it always repaints the whole screen
and can never leave stale rows behind (no ghosting on stage/input transitions).

These tests capture stdout around end_frame() and assert the emitted bytes.
"""

import asyncio
import contextlib
import io
import unittest

from kollabor_tui.fullscreen.renderer import FullScreenRenderer

ESC = "\x1b"
CLEAR_ALL = f"{ESC}[2J"  # whole-screen erase -- present on every *painted* frame


def _renderer():
    r = FullScreenRenderer()
    r.terminal_width = 80
    r.terminal_height = 24
    return r


def _flush(r):
    """Capture whatever end_frame() writes to stdout."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r.end_frame()
    return buf.getvalue()


def _frame(r, cells):
    """Draw a frame: clear_screen then write_at each (x, y, text); return output."""
    r.begin_frame()
    r.clear_screen()
    for x, y, text in cells:
        r.write_at(x, y, text)
    return _flush(r)


class FullscreenRendererSkipTest(unittest.TestCase):
    def test_first_frame_paints_in_full(self):
        r = _renderer()
        out = _frame(r, [(2, 0, "hello"), (2, 1, "world")])

        self.assertIn(CLEAR_ALL, out)  # full clear + repaint
        self.assertIn("hello", out)
        self.assertIn("world", out)

    def test_identical_frame_is_skipped(self):
        r = _renderer()
        _frame(r, [(2, 0, "hello"), (2, 1, "world")])
        out = _frame(r, [(2, 0, "hello"), (2, 1, "world")])

        self.assertEqual(out, "")  # nothing changed -> nothing written (no flicker)

    def test_changed_frame_repaints_whole_screen(self):
        # The anti-ghosting guarantee: any change re-clears the whole screen,
        # so old content (e.g. a previous endpoint URL or step row) is wiped.
        r = _renderer()
        _frame(r, [(2, 0, "hello"), (2, 1, "world")])
        out = _frame(r, [(2, 0, "hello"), (2, 1, "WORLD")])

        self.assertIn(CLEAR_ALL, out)  # full \033[2J clear, not a partial update
        self.assertIn("WORLD", out)
        self.assertIn("hello", out)  # whole frame re-sent (row 1 included)

    def test_empty_frame_is_a_noop(self):
        r = _renderer()
        _frame(r, [(2, 0, "hello")])  # seed
        r.begin_frame()  # draw nothing
        out = _flush(r)

        self.assertEqual(out, "")  # no write, screen preserved

    def test_invalidate_forces_repaint(self):
        r = _renderer()
        _frame(r, [(2, 0, "hello")])
        r.invalidate_render_cache()
        out = _frame(r, [(2, 0, "hello")])  # identical content, cache dropped

        self.assertIn("hello", out)  # repainted despite being unchanged
        self.assertIn(CLEAR_ALL, out)


class SetupWizardIdleRepaintTest(unittest.TestCase):
    """End-to-end: the real setup wizard sitting idle must stop repainting.

    This is the flicker fix on the actual complained-about view -- at 12fps the
    provider stage is static, so every frame after the first must emit zero
    bytes (while a first frame paints in full).
    """

    def test_idle_wizard_emits_nothing_after_first_paint(self):
        from plugins.altview.setup_altview import SetupAltView

        view = SetupAltView()
        r = _renderer()
        r.terminal_width, r.terminal_height = 120, 40
        loop = asyncio.new_event_loop()

        def render_once():
            buf = io.StringIO()
            r.begin_frame()
            loop.run_until_complete(view.render_frame(0.0))
            with contextlib.redirect_stdout(buf):
                r.end_frame()
            return buf.getvalue()

        try:
            loop.run_until_complete(view.on_enter(r))  # sets renderer + STAGE_PROVIDER
            first = render_once()
            second = render_once()
            third = render_once()
        finally:
            loop.close()

        self.assertTrue(loop.is_closed())
        self.assertNotEqual(first, "")  # first frame paints the wizard
        self.assertIn(CLEAR_ALL, first)  # ...in full (whole-screen clear)
        self.assertEqual(second, "")  # idle -> nothing repainted (no flicker)
        self.assertEqual(third, "")


if __name__ == "__main__":
    unittest.main()
