"""Trusted entry points that can submit human-authored chat input."""

from enum import StrEnum


class UserInputSource(StrEnum):
    """Application paths that may emit the human USER_INPUT event."""

    TUI = "user"
    STATE_RPC = "state_rpc"
    CLI_INITIAL = "cli_initial"
    PIPE = "pipe"


HUMAN_USER_INPUT_SOURCES = frozenset(source.value for source in UserInputSource)
