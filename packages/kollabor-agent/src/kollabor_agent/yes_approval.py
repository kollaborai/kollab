"""Tool calls that run on the user's own yes instead of an approval prompt.

A voice turn never waits on a prompt nobody can click. A call it may not run
unasked (the voice guard's ``confirmation_required``, or a permission prompt)
comes back "not run yet" and is kept here, and the model asks for the OK in its
reply. A yes answers only the agent's last reply, once it is finished: a short
yes lets each call that reply asked for run once, a short no drops them, and
anything else is a normal turn that leaves nothing for a later yes. A yes said
while the reply is still being written or spoken (a talk-over) answers nothing,
and a voice ask stops counting after ASK_OPEN_SECONDS.

A typed turn's approval prompt that its turn leaves open (a Stop, 5 minutes with
no answer) is parked here too: its card answers it, and the user's typed yes or
no closes that card.
"""

from __future__ import annotations

import json
import logging
import re
import time
from contextvars import ContextVar
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

NEEDS_YES = (
    "Not run yet: this needs the user's OK. In your reply, say in a few plain words "
    "what you want to run and why, and ask for a yes. Their yes runs it."
)
# A turn the user did not say (the voice greeting) asks for nothing.
NOT_IN_HIDDEN_TURN = (
    "Not run: nothing that needs the user's OK runs before they have said anything."
)
ASK_OPEN_SECONDS = 300.0  # a voice ask nobody answered in 5 minutes stops counting

# How the call running now may get the user's OK, for PermissionManager: "yes"
# (their yes already approved it) or "ask" (a voice turn: never wait on a prompt).
call_approval: ContextVar[str | None] = ContextVar("call_approval", default=None)

_YES_HEAD = r"(?:yes|yeah|yep|yup|sure|ok|okay|approved?(?: it)?|go ahead|do it|go for it)"
_YES = re.compile(rf"{_YES_HEAD}(?: (?:{_YES_HEAD}|please))*")
_NO_HEAD = r"(?:no|nope|nah|don't|do not|cancel|deny|never mind)"
_NO = re.compile(rf"{_NO_HEAD}(?: (?:{_NO_HEAD}|thanks|thank you|do it|do that|it|that))*")


def human_answer(text: str) -> str | None:
    """"yes" or "no" when the whole message is a short answer, else None."""
    words = " ".join(re.sub(r"[^a-z']+", " ", text.lower().replace("’", "'")).split())
    if _YES.fullmatch(words):
        return "yes"
    if _NO.fullmatch(words):
        return "no"
    return None


def _call(tool_data: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    raw_kind = str(tool_data.get("type", "")).lower().replace("-", "_")
    kind = "terminal" if raw_kind == "git" else raw_kind
    # An MCP or plugin tool's own name: two tools never share a yes.
    name = str(tool_data.get("name") or "")
    if name.lower().replace("-", "_") in (kind, raw_kind):
        name = ""
    # A native call's arguments, an MCP call's, or an XML call's on top.
    args = next(
        (value for value in (tool_data.get("input"), tool_data.get("arguments")) if isinstance(value, Mapping)),
        None,
    )
    if args is None:
        args = {
            key: value
            for key, value in tool_data.items()
            if key not in ("id", "raw", "name", "type", "arguments", "input")
            and not str(key).startswith("_")
        }
    fields = {key: value for key, value in args.items() if value not in (None, "", False)}
    if isinstance(fields.get("command"), str):
        fields["command"] = fields["command"].strip()  # quotes stay: a shell runs them
    return kind, name, fields


def approval_key(tool_data: Mapping[str, Any]) -> str:
    """The call itself: the same tool and arguments, whatever its call id."""
    return json.dumps(_call(tool_data), sort_keys=True, default=str)


def _describe(tool_data: Mapping[str, Any]) -> str:
    kind, name, fields = _call(tool_data)
    if isinstance(fields.get("command"), str):
        return fields["command"]
    return f"{name or kind} {json.dumps(fields, sort_keys=True, default=str)[:300]}"


class YesApprovals:
    """The calls the agent's last reply asked the user to OK, and the ones their yes approved."""

    def __init__(self) -> None:
        # The reply being written: approval key -> (what it runs, its voice turn).
        self._replying: dict[str, tuple[str, str | None]] = {}
        self._asked: dict[str, str] = {}  # the last finished reply's: approval key -> what it runs
        self._asked_until = 0.0  # when a voice ask stops counting (0: a typed one never does)
        self._cut: set[str] = set()  # voice turns a talk-over cut short
        self._approved: set[str] = set()
        self._note = ""
        self._conversation: str | None = None
        self._cards: dict[str, str] = {}  # parked prompt's call id -> approval key
        # Closes a parked prompt's card: (call id, approve / deny / None cancel).
        self.close_card: Callable[[str, bool | None], None] | None = None

    def ask(self, tool_data: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> None:
        """Keep a call that needs the user's OK for the reply being written."""
        turn = None
        if isinstance(context, Mapping) and context.get("source") == "livekit_voice":
            if context.get("voice_hidden") is True:
                return  # the greeting: the user has not said anything yet
            turn = str(context.get("turn_id") or "")
        self._replying[approval_key(tool_data)] = (_describe(tool_data), turn)

    def on_reply_done(self) -> None:
        """The agent's reply is finished: what it asked for, or nothing, is what a yes answers now."""
        asks = {key: ask for key, ask in self._replying.items() if ask[1] not in self._cut}
        self._replying.clear()
        self._cut.clear()
        self._asked = {key: what for key, (what, _) in asks.items()}
        spoken = any(turn is not None for _, turn in asks.values())
        self._asked_until = time.monotonic() + ASK_OPEN_SECONDS if spoken else 0.0

    def reply_cut(self, turn: str) -> None:
        """A talk-over cut a voice reply short: the user never heard its ask whole."""
        self._cut.add(turn)  # on_reply_done drops its asks, late ones too
        self._asked.clear()

    def take(self, tool_data: Mapping[str, Any]) -> bool:
        """True once for a call the user's yes approved."""
        if not self._approved:
            return False
        key = approval_key(tool_data)
        if key not in self._approved:
            return False
        self._approved.discard(key)
        return True

    def park(self, tool_data: Mapping[str, Any]) -> None:
        """Its approval prompt stays open past its turn: its card answers it too."""
        self._cards[str(tool_data.get("id"))] = approval_key(tool_data)

    def answered_on_card(self, tool_data: Mapping[str, Any], allowed: bool | None) -> str:
        """A parked prompt was answered on its card: what to run now, or "".

        Allow approves that call once and Deny drops the ask; a prompt that closed
        unanswered (None) leaves it open. A card the user's typed answer already
        closed does nothing.
        """
        key = self._cards.pop(str(tool_data.get("id")), None)
        if key is None or allowed is None:
            return ""
        self._asked.pop(key, None)
        if not allowed:
            return ""
        self._approved.add(key)
        return _describe(tool_data)

    def _close_cards(self, keys: set[str], allowed: bool | None) -> None:
        for card, key in list(self._cards.items()):
            if key in keys:
                del self._cards[card]
                if self.close_card is not None:
                    self.close_card(card, allowed)

    def on_human_input(self, text: str, conversation: str | None = None, *, talk_over: bool = False) -> None:
        """The user's own message (never another agent's or the model's).

        ``talk_over``: they started saying it while the agent was still talking.
        """
        self._approved.clear()
        self._note = ""
        self._replying.clear()  # said before that reply was done: it answers none of it
        if conversation != self._conversation:
            if self._conversation is not None:
                # /resume or a new chat: what the agent asked is not in this one.
                self._asked.clear()
                self._close_cards(set(self._cards.values()), None)
            self._conversation = conversation
        if self._asked_until and time.monotonic() > self._asked_until:
            self._asked.clear()
        answer = human_answer(text) if self._asked and not talk_over else None
        if answer == "yes":
            self._approved = set(self._asked)
            calls = "\n".join(f"- {what}" for what in self._asked.values())
            self._note = (
                f"The user just said yes to what you asked to run:\n{calls}\n"
                "Run exactly those now, then tell them what you found."
            )
        if answer:
            logger.info(f"The user's {answer} answered {len(self._asked)} asked call(s)")
            self._close_cards(set(self._asked), answer == "yes")
        self._asked.clear()  # a yes answers only the reply just before it; its card stays open

    def take_note(self) -> str:
        """The one-shot note that tells the model what the user's yes approved."""
        note, self._note = self._note, ""
        return note
