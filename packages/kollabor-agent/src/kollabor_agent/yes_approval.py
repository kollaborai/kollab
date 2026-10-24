"""Tool calls that run on the user's own yes instead of an approval prompt.

A voice turn never waits on a prompt nobody can click. A call it may not run
unasked (the voice guard's ``confirmation_required``, or a permission prompt)
comes back "not run yet" and is kept here, and the model asks for the OK in its
reply. The user's next message, typed or spoken, decides: a short yes lets each
call the agent last asked for run once, a short no drops them, and anything else
is a normal turn that leaves them open.

A typed turn's approval prompt that its turn leaves open (a Stop, 5 minutes with
no answer) is parked here too: its card answers it, and the user's typed yes or
no closes that card.
"""

from __future__ import annotations

import json
import re
import shlex
from contextvars import ContextVar
from typing import Any, Callable, Mapping

NEEDS_YES = (
    "Not run yet: this needs the user's OK. In your reply, say in a few plain words "
    "what you want to run and why, and ask for a yes. Their yes runs it."
)

# How the call running now may get the user's OK, for PermissionManager: "yes"
# (their yes already approved it) or "ask" (a voice turn: never wait on a prompt).
call_approval: ContextVar[str | None] = ContextVar("call_approval", default=None)

_YES_HEAD = r"(?:yes|yeah|yep|yup|sure|ok|okay|approved?(?: it)?|go ahead|do it|go for it)"
_YES = re.compile(rf"{_YES_HEAD}(?: (?:{_YES_HEAD}|please|thanks|thank you))*")
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


def _call(tool_data: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    kind = str(tool_data.get("type", "")).lower().replace("-", "_")
    kind = "terminal" if kind == "git" else kind
    args = tool_data.get("input")  # a native call's arguments; an XML call has them on top
    if not isinstance(args, Mapping):
        args = {
            key: value
            for key, value in tool_data.items()
            if key not in ("id", "raw", "name", "type", "arguments")
            and not str(key).startswith("_")
        }
    fields = {key: value for key, value in args.items() if value not in (None, "", False)}
    command = fields.get("command")
    if kind == "terminal" and isinstance(command, str):
        try:
            fields["command"] = shlex.join(shlex.split(command))
        except ValueError:
            pass
    return kind, fields


def approval_key(tool_data: Mapping[str, Any]) -> str:
    """The call itself: the same command or message, whatever its call id."""
    return json.dumps(_call(tool_data), sort_keys=True, default=str)


def _describe(tool_data: Mapping[str, Any]) -> str:
    kind, fields = _call(tool_data)
    if isinstance(fields.get("command"), str):
        return fields["command"]
    return f"{kind} {json.dumps(fields, sort_keys=True, default=str)[:300]}"


class YesApprovals:
    """The calls the agent last asked the user to OK, and the ones their yes approved."""

    def __init__(self) -> None:
        self._asked: dict[str, str] = {}  # approval key -> what it runs
        self._asked_in = 0
        self._approved: set[str] = set()
        self._note = ""
        self._turn = 0
        self._conversation: str | None = None
        self._cards: dict[str, str] = {}  # parked prompt's call id -> approval key
        # Closes a parked prompt's card: (call id, approve / deny / None cancel).
        self.close_card: Callable[[str, bool | None], None] | None = None

    def ask(self, tool_data: Mapping[str, Any]) -> None:
        """Keep a call that needs the user's OK; a later turn's ask replaces it."""
        if self._asked_in != self._turn:
            self._asked.clear()
            self._asked_in = self._turn
        self._asked[approval_key(tool_data)] = _describe(tool_data)

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

    def on_human_input(self, text: str, conversation: str | None = None) -> None:
        """The user's own message (never another agent's or the model's)."""
        self._turn += 1
        self._approved.clear()
        self._note = ""
        if conversation != self._conversation:
            if self._conversation is not None:
                # /resume or a new chat: what the agent asked is not in this one.
                self._asked.clear()
                self._close_cards(set(self._cards.values()), None)
            self._conversation = conversation
        answer = human_answer(text) if self._asked else None
        if answer == "yes":
            self._approved = set(self._asked)
            calls = "\n".join(f"- {what}" for what in self._asked.values())
            self._note = (
                f"The user just said yes to what you asked to run:\n{calls}\n"
                "Run exactly those now, then tell them what you found."
            )
        if answer:
            self._close_cards(set(self._asked), answer == "yes")
            self._asked.clear()

    def take_note(self) -> str:
        """The one-shot note that tells the model what the user's yes approved."""
        note, self._note = self._note, ""
        return note
