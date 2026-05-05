"""
Integration tests comparing LEGACY and EXPLICIT tool accumulation modes.

Tests ensure both modes produce identical results:
- LEGACY mode: add_delta() buffers, get_completed_tools() returns tools
- EXPLICIT mode: add_delta() returns completed tools immediately

Feature flag: core.llm.use_explicit_tool_accumulation

Target: 180+ lines of comparison tests with 70%+ coverage.
"""

import pytest

from kollabor_ai.providers.transformers import ToolCallAccumulator


class TestToolAccumulatorModes:
    """Compare LEGACY and EXPLICIT accumulation modes."""

    @staticmethod
    def accumulate_legacy(deltas):
        """Helper: accumulate in LEGACY mode."""
        acc = ToolCallAccumulator(legacy_mode=True)
        for delta in deltas:
            acc.add_delta(**delta)
        return acc.get_completed_tools()

    @staticmethod
    def accumulate_explicit(deltas):
        """Helper: accumulate in EXPLICIT mode."""
        acc = ToolCallAccumulator(legacy_mode=False)
        completed = []
        for delta in deltas:
            result = acc.add_delta(**delta)
            if result:
                completed.extend(result)
        return completed

    def test_single_tool_both_modes_identical(self):
        """Test single tool completion produces identical results."""
        deltas = [
            {"tool_call_id": "call_1", "name": "search", "arguments_delta": '{"q": "'},
            {"tool_call_id": "call_1", "name": None, "arguments_delta": 'test"}'},
        ]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 1
        assert legacy_tools[0].id == explicit_tools[0].id == "call_1"
        assert legacy_tools[0].name == explicit_tools[0].name == "search"
        assert legacy_tools[0].input == explicit_tools[0].input == {"q": "test"}

    def test_multiple_tools_both_modes_identical(self):
        """Test multiple simultaneous tools produce identical results."""
        deltas = [
            {"tool_call_id": "call_1", "name": "search", "arguments_delta": '{"q": "'},
            {"tool_call_id": "call_2", "name": "calc", "arguments_delta": '{"x": 1,'},
            {"tool_call_id": "call_1", "name": None, "arguments_delta": 'test"}'},
            {"tool_call_id": "call_2", "name": None, "arguments_delta": ' "y": 2}'},
        ]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 2

        # Sort by ID for comparison
        legacy_sorted = sorted(legacy_tools, key=lambda t: t.id)
        explicit_sorted = sorted(explicit_tools, key=lambda t: t.id)

        for lt, et in zip(legacy_sorted, explicit_sorted):
            assert lt.id == et.id
            assert lt.name == et.name
            assert lt.input == et.input

    def test_incremental_json_both_modes_identical(self):
        """Test JSON split across many chunks produces identical results."""
        json_chunks = [
            '{"que',
            'ry": "',
            "test",
            '", ',
            '"lim',
            'it": ',
            "10}",
        ]

        deltas = [
            {"tool_call_id": "call_1", "name": "search", "arguments_delta": chunk}
            for chunk in json_chunks
        ]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 1
        assert (
            legacy_tools[0].input
            == explicit_tools[0].input
            == {
                "query": "test",
                "limit": 10,
            }
        )

    def test_name_before_arguments_both_modes_identical(self):
        """Test name arriving before arguments produces identical results."""
        deltas = [
            {"tool_call_id": "call_1", "name": "test", "arguments_delta": None},
            {"tool_call_id": "call_1", "name": None, "arguments_delta": '{"key": "'},
            {"tool_call_id": "call_1", "name": None, "arguments_delta": 'value"}'},
        ]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 1
        assert legacy_tools[0].name == explicit_tools[0].name == "test"

    def test_arguments_before_name_both_modes_identical(self):
        """Test arguments arriving before name produces identical results."""
        deltas = [
            {"tool_call_id": "call_1", "name": None, "arguments_delta": '{"key": "'},
            {"tool_call_id": "call_1", "name": "test", "arguments_delta": None},
            {"tool_call_id": "call_1", "name": None, "arguments_delta": 'value"}'},
        ]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 1

    def test_incomplete_json_both_modes_identical(self):
        """Test incomplete JSON produces identical empty results."""
        deltas = [
            {
                "tool_call_id": "call_1",
                "name": "test",
                "arguments_delta": '{"incomplete"',
            }
        ]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 0

    def test_malformed_json_both_modes_identical(self):
        """Test malformed JSON produces identical empty results."""
        deltas = [
            {"tool_call_id": "call_1", "name": "test", "arguments_delta": "{invalid}"}
        ]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 0

    def test_empty_arguments_both_modes_identical(self):
        """Test empty JSON object produces identical results."""
        deltas = [{"tool_call_id": "call_1", "name": "test", "arguments_delta": "{}"}]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 1
        assert legacy_tools[0].input == explicit_tools[0].input == {}

    def test_special_characters_both_modes_identical(self):
        """Test special characters in JSON produce identical results."""
        json_str = r'{"text": "Hello\n\t\"World\"", "regex": "\\d+"}'
        deltas = [
            {"tool_call_id": "call_1", "name": "test", "arguments_delta": json_str}
        ]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 1
        assert legacy_tools[0].input == explicit_tools[0].input

    def test_unicode_both_modes_identical(self):
        """Test Unicode characters produce identical results."""
        json_str = '{"emoji": "😀🎉", "chinese": "你好"}'
        deltas = [
            {"tool_call_id": "call_1", "name": "test", "arguments_delta": json_str}
        ]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 1
        assert legacy_tools[0].input == explicit_tools[0].input

    def test_large_payload_both_modes_identical(self):
        """Test large JSON payload produces identical results."""
        import json

        large_obj = {f"key_{i}": f"value_{i}" * 10 for i in range(100)}
        json_str = json.dumps(large_obj)

        # Split into chunks
        chunk_size = 100
        deltas = []
        remaining = json_str
        while remaining:
            chunk = remaining[:chunk_size]
            deltas.append(
                {"tool_call_id": "call_1", "name": "test", "arguments_delta": chunk}
            )
            remaining = remaining[chunk_size:]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 1
        assert legacy_tools[0].input == explicit_tools[0].input == large_obj

    def test_reset_both_modes_work(self):
        """Test reset works correctly in both modes."""
        deltas = [
            {"tool_call_id": "call_1", "name": "test", "arguments_delta": '{"key": "'}
        ]

        # LEGACY mode reset
        acc_legacy = ToolCallAccumulator(legacy_mode=True)
        acc_legacy.add_delta(**deltas[0])
        assert len(acc_legacy.get_completed_tools()) == 0
        acc_legacy.reset()
        assert len(acc_legacy.get_completed_tools()) == 0

        # EXPLICIT mode reset
        acc_explicit = ToolCallAccumulator(legacy_mode=False)
        acc_explicit.add_delta(**deltas[0])
        assert len(acc_explicit.get_completed_tools()) == 0
        acc_explicit.reset()
        assert len(acc_explicit.get_completed_tools()) == 0

    def test_get_buffer_status_both_modes(self):
        """Test get_buffer_status works in both modes."""
        deltas = [
            {
                "tool_call_id": "call_1",
                "name": "test",
                "arguments_delta": '{"incomplete"',
            }
        ]

        # LEGACY mode
        acc_legacy = ToolCallAccumulator(legacy_mode=True)
        acc_legacy.add_delta(**deltas[0])
        status_legacy = acc_legacy.get_buffer_status()
        assert "call_1" in status_legacy
        assert status_legacy["call_1"]["name"] == "test"
        assert status_legacy["call_1"]["parseable"] is False
        assert status_legacy["call_1"]["returned"] is False  # LEGACY doesn't track

        # EXPLICIT mode
        acc_explicit = ToolCallAccumulator(legacy_mode=False)
        acc_explicit.add_delta(**deltas[0])
        status_explicit = acc_explicit.get_buffer_status()
        assert "call_1" in status_explicit
        assert status_explicit["call_1"]["name"] == "test"
        assert status_explicit["call_1"]["parseable"] is False
        assert status_explicit["call_1"]["returned"] is False  # Not returned yet

    def test_explicit_mode_returns_tools_immediately(self):
        """Test EXPLICIT mode returns tools as soon as they're complete."""
        deltas = [
            {"tool_call_id": "call_1", "name": "search", "arguments_delta": '{"q": "'},
            {"tool_call_id": "call_2", "name": "calc", "arguments_delta": '{"x": 1}'},
            {"tool_call_id": "call_1", "name": None, "arguments_delta": 'test"}'},
        ]

        acc = ToolCallAccumulator(legacy_mode=False)
        tools_after_each_delta = []

        for delta in deltas:
            result = acc.add_delta(**delta)
            tools_after_each_delta.append(len(result) if result else 0)

        # Only call_2 is complete after delta 2
        # Both complete after delta 3
        assert tools_after_each_delta == [0, 1, 1]

    def test_explicit_mode_get_completed_tools_returns_remaining(self):
        """Test EXPLICIT mode get_completed_tools returns unreturned tools."""
        deltas = [
            {"tool_call_id": "call_1", "name": "search", "arguments_delta": '{"q": "'}
        ]

        acc = ToolCallAccumulator(legacy_mode=False)
        acc.add_delta(**deltas[0])

        # get_completed_tools should return empty (incomplete)
        assert len(acc.get_completed_tools()) == 0

        # Complete the tool WITHOUT capturing the return value
        result = acc.add_delta(
            tool_call_id="call_1", name=None, arguments_delta='test"}'
        )
        # EXPLICIT mode returns the completed tool immediately
        assert result is not None and len(result) == 1

        # get_completed_tools should now return empty (already returned)
        tools = acc.get_completed_tools()
        assert len(tools) == 0

        # Test that calling get_completed_tools again returns empty
        assert len(acc.get_completed_tools()) == 0

    def test_legacy_mode_returns_none_from_add_delta(self):
        """Test LEGACY mode add_delta always returns None."""
        deltas = [
            {"tool_call_id": "call_1", "name": "search", "arguments_delta": '{"x": 1}'}
        ]

        acc = ToolCallAccumulator(legacy_mode=True)
        for delta in deltas:
            result = acc.add_delta(**delta)
            assert result is None

        # Must use get_completed_tools
        tools = acc.get_completed_tools()
        assert len(tools) == 1

    def test_split_json_boundaries_both_modes_identical(self):
        """Test awkward JSON split boundaries produce identical results."""
        chunks = ['{"k', 'ey1": "value1", "key2": "value2"}']

        deltas = [
            {"tool_call_id": "call_1", "name": "test", "arguments_delta": chunk}
            for chunk in chunks
        ]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 1
        assert legacy_tools[0].input == explicit_tools[0].input

    def test_three_tools_simultaneous_both_modes_identical(self):
        """Test three simultaneous tools produce identical results."""
        deltas = [
            {"tool_call_id": "call_1", "name": "search", "arguments_delta": '{"q": "'},
            {"tool_call_id": "call_2", "name": "calc", "arguments_delta": '{"x": 1,'},
            {"tool_call_id": "call_3", "name": "fetch", "arguments_delta": '{"url": "'},
            {"tool_call_id": "call_1", "name": None, "arguments_delta": 'test"}'},
            {"tool_call_id": "call_2", "name": None, "arguments_delta": ' "y": 2}'},
            {
                "tool_call_id": "call_3",
                "name": None,
                "arguments_delta": 'http://example"}',
            },
        ]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 3

        # Sort by ID for comparison
        legacy_sorted = sorted(legacy_tools, key=lambda t: t.id)
        explicit_sorted = sorted(explicit_tools, key=lambda t: t.id)

        for lt, et in zip(legacy_sorted, explicit_sorted):
            assert lt.id == et.id
            assert lt.name == et.name
            assert lt.input == et.input


class TestToolAccumulatorModeIntegration:
    """Test mode switching and configuration."""

    def test_legacy_mode_default(self):
        """Test LEGACY mode is default for backward compatibility."""
        acc = ToolCallAccumulator()
        assert acc.legacy_mode is True

    def test_explicit_mode_configurable(self):
        """Test EXPLICIT mode can be configured."""
        acc = ToolCallAccumulator(legacy_mode=False)
        assert acc.legacy_mode is False

    def test_mode_does_not_change_accumulation_logic(self):
        """Test both modes use the same accumulation logic."""
        deltas = [
            {"tool_call_id": "call_1", "name": "test", "arguments_delta": '{"key": "'}
        ]

        acc_legacy = ToolCallAccumulator(legacy_mode=True)
        acc_explicit = ToolCallAccumulator(legacy_mode=False)

        acc_legacy.add_delta(**deltas[0])
        acc_explicit.add_delta(**deltas[0])

        # Buffer status should be identical
        status_legacy = acc_legacy.get_buffer_status()
        status_explicit = acc_explicit.get_buffer_status()

        assert (
            status_legacy["call_1"]["buffer_length"]
            == status_explicit["call_1"]["buffer_length"]
        )
        assert status_legacy["call_1"]["name"] == status_explicit["call_1"]["name"]


class TestToolAccumulatorErrorHandling:
    """Test error handling is identical in both modes."""

    def test_none_tool_call_id_is_dropped_in_both_modes(self):
        """Test None tool_call_id is dropped gracefully in both modes."""
        legacy_result = ToolCallAccumulator(legacy_mode=True).add_delta(
            tool_call_id=None, name="test", arguments_delta="{}"
        )
        explicit_result = ToolCallAccumulator(legacy_mode=False).add_delta(
            tool_call_id=None, name="test", arguments_delta="{}"
        )

        assert legacy_result is None
        assert explicit_result is None

    def test_name_change_warning_in_both_modes(self):
        """Test name change warning occurs in both modes."""
        acc_legacy = ToolCallAccumulator(legacy_mode=True)
        acc_explicit = ToolCallAccumulator(legacy_mode=False)

        # Both should log warning but not crash
        acc_legacy.add_delta(tool_call_id="call_1", name="test1", arguments_delta=None)
        acc_legacy.add_delta(tool_call_id="call_1", name="test2", arguments_delta=None)

        acc_explicit.add_delta(
            tool_call_id="call_1", name="test1", arguments_delta=None
        )
        acc_explicit.add_delta(
            tool_call_id="call_1", name="test2", arguments_delta=None
        )

        # Both should have first name (consistency check)
        assert acc_legacy.get_buffer_status()["call_1"]["name"] == "test1"
        assert acc_explicit.get_buffer_status()["call_1"]["name"] == "test1"


class TestToolAccumulatorPropertyBased:
    """Property-based tests for mode equivalence."""

    @pytest.mark.parametrize(
        "json_obj",
        [
            {"simple": "value"},
            {"nested": {"key": "value"}},
            {"list": [1, 2, 3]},
            {"mixed": {"str": "text", "num": 42, "bool": True}},
        ],
    )
    def test_property_both_modes_handle_any_json(self, json_obj):
        """Property: both modes handle any valid JSON identically."""
        import json

        json_str = json.dumps(json_obj)

        # Split into random chunks
        chunks = []
        remaining = json_str
        while remaining:
            chunk_size = max(1, len(remaining) // 2)
            chunks.append(remaining[:chunk_size])
            remaining = remaining[chunk_size:]

        deltas = [
            {"tool_call_id": "call_1", "name": "test", "arguments_delta": chunk}
            for chunk in chunks
        ]

        legacy_tools = self.accumulate_legacy(deltas)
        explicit_tools = self.accumulate_explicit(deltas)

        assert len(legacy_tools) == len(explicit_tools) == 1
        assert legacy_tools[0].input == explicit_tools[0].input == json_obj

    @staticmethod
    def accumulate_legacy(deltas):
        acc = ToolCallAccumulator(legacy_mode=True)
        for delta in deltas:
            acc.add_delta(**delta)
        return acc.get_completed_tools()

    @staticmethod
    def accumulate_explicit(deltas):
        acc = ToolCallAccumulator(legacy_mode=False)
        completed = []
        for delta in deltas:
            result = acc.add_delta(**delta)
            if result:
                completed.extend(result)
        return completed
