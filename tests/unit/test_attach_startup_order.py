"""Attach-mode startup ordering invariants."""

import inspect

from kollabor.application import TerminalLLMChat


def test_attach_startup_ready_waits_for_proxy_hook_and_event_reader():
    source = inspect.getsource(TerminalLLMChat._initialize_attach_proxy)

    ready_index = source.rindex("self._startup_ready.set()")
    proxy_hook_index = source.index('name="attach_proxy_input"')
    event_reader_index = source.index(
        'self.create_background_task(_read_remote_events(), "attach_event_reader")'
    )

    assert proxy_hook_index < ready_index
    assert event_reader_index < ready_index
