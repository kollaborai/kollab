"""Trusted entry points that can submit human-authored chat input."""

from enum import StrEnum


class UserInputSource(StrEnum):
    """Application paths that may emit the human USER_INPUT event."""

    TUI = "user"
    STATE_RPC = "state_rpc"
    CLI_INITIAL = "cli_initial"
    PIPE = "pipe"
    HUB_ATTACHMENT = "hub_attachment"
    # A hub message from a person (`kollab --hub msg`, a human sender) handed to an
    # ACP agent: theirs, but not an answer typed or said live.
    HUB_MESSAGE = "hub_message"


HUMAN_USER_INPUT_SOURCES = frozenset(source.value for source in UserInputSource)

# A person answering live: typed in the TUI or an attached window, sent from the web
# composer, or said by voice (state RPC carries the LiveKit room, "voice" the /voice
# plugin). Only their short yes runs what the agent asked to run; a hub peer
# ("agent"), the CLI's first message, a pipe or a source not listed here never does.
YES_SOURCES = frozenset(
    {UserInputSource.TUI.value, UserInputSource.HUB_ATTACHMENT.value, UserInputSource.STATE_RPC.value, "voice"}
)
