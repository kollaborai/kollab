import io

from plugins.terminal_plugin import TmuxPlugin
from plugins.agent_orchestrator.ring_buffer import RingBuffer


class _FakeStream(io.BytesIO):
    def __init__(self, data=b""):
        super().__init__(data)
        self.close_count = 0

    def close(self):
        self.close_count += 1
        super().close()


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStream()
        self.stdout = _FakeStream(b"hello\n")
        self.stderr = _FakeStream()


def test_pump_output_closes_all_popen_pipes_on_natural_exit():
    proc = _FakeProc()
    ring = RingBuffer()

    TmuxPlugin._pump_output(proc, ring, "fake")

    assert ring.get_last(1) == ["hello"]
    assert proc.stdin.close_count == 1
    assert proc.stdout.close_count == 1
    assert proc.stderr.close_count == 1
