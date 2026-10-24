"""Lines the voice classifier says itself, so speech never meets silence.

The classifier picks one by what it decided and what the words ask for: an
acknowledgement when speech goes to the agent, a direct answer to a mic check,
thanks or a bare greeting (the agent still gets those words, with the answer,
and may add more or hold off), a question when it is unsure
the speech was for the agent, and a progress line while the agent works.
"""

from __future__ import annotations

import random
import re
from difflib import SequenceMatcher

LINES = {
    # Speech goes to the agent: something at once, the answer follows.
    "question": (
        "Let me check on that.",
        "One moment, let me look.",
        "Good question, give me a second.",
        "Let me find out.",
    ),
    "task": (
        "On it.",
        "Sure, starting on that now.",
        "Okay, give me a moment.",
        "Got it, working on it.",
    ),
    "request": ("One moment.", "Just a second.", "Okay, let me think about that."),
    "confirmed": ("Okay, on it.", "Got it, one moment."),
    # Answered at once; the agent still gets the words and may add more.
    "mic_check": ("Yes, I can hear you.", "I hear you loud and clear."),
    "thanks": ("You're welcome.", "Anytime."),
    "greeting": ("Hi, I'm listening.", "Yes? I'm here."),
    # Unsure the speech was meant for the agent. "Yes" always means it was.
    "clarify": ("Sorry, was that for me?", "Were you talking to me?"),
    "clarify_background": (
        "Was that for me? It sounded like the TV.",
        "Sorry, was that for me? I think something is playing.",
    ),
    # The agent is still working on its answer.
    "still_working": (
        "Still working on it.",
        "Almost there, still checking.",
        "Bear with me, still looking into it.",
    ),
}

_WORD = re.compile(r"[a-z0-9']+")
_GREETING = {"hey", "hi", "hello", "yo", "ok", "okay", "hiya", "morning", "evening"}
_QUESTION = {
    *("what how why when where who which whose is are was were am do does did".split()),
    *("can could would will should shall may have has".split()),
}
# Asking for information gets "let me check"; asking for an action gets "on it".
_LOOKUP = set(
    "tell show explain find look check search read review summarize list".split()
)
_ACTION = set(
    "run fix make add open build start stop create write update deploy test send "
    "delete remove install commit push pull merge restart close rename clean "
    "change set turn put move play call message try use go".split()
)
_MIC_CHECK = re.compile(
    r"\b(can|could|do) you hear me\b|\bare you (there|listening|awake)\b"
    r"|\b(are you|is this( thing)?) (on|working)$|\bmic check\b"
    r"|^(mic )?(testing|test)( (testing|test|one|two|three|\d+))*$",
    re.I,
)
_THANKS = re.compile(
    r"^(thanks|thank you|cheers|much appreciated|appreciate it|great,? thanks)\b",
    re.I,
)
_FOR_YOU = re.compile(r"\b(for you|to you|talking to you)\b", re.I)
_NOT_YOU = re.compile(r"\bnot (for |to )?you\b", re.I)
# "You can ignore that", "just ignore it"; never "don't ignore it".
_IGNORE = re.compile(r"(?<!don't )(?<!don’t )(?<!do not )\bignore\b", re.I)
# "That was my wife", "it was the TV": it names who else was talking.
_SOMEONE_ELSE = re.compile(r"\b(that|it) was (my|the|a|an|his|her|their|our)\b", re.I)
_BACKGROUND = re.compile(
    r"\b(tv|television|movie|youtube|video|podcast|watching|someone else|"
    r"somebody else|on the phone|talking to (him|her|them|my))\b",
    re.I,
)
_NO = re.compile(
    r"^(no|nope|nah|never ?mind|wasn't|it wasn't)\b", re.I
)
_YES = re.compile(
    r"^(yes|yeah|yep|yup|yea|ya|sure|correct|right|uh[- ]?huh|mm[- ]?hmm|"
    r"of course|absolutely|definitely|it was|i was|that was)\b",
    re.I,
)


def pick(kind: str, previous: str | None = None) -> str:
    """One line of this kind, never the same as the previous one."""
    choices = [line for line in LINES[kind] if line != previous] or LINES[kind]
    return random.choice(choices)


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("’", "'"))


def _is_name(words: list[str], name: str) -> bool:
    # Whisper can split or slightly misspell a name: "aqua marine".
    spoken = "".join(words)
    return bool(spoken) and SequenceMatcher(None, spoken, name).ratio() >= 0.85


def address(text: str, names) -> tuple[bool, list[str]]:
    """Whether the speaker calls the agent by name, and the words without it.

    Only the vocative counts: the name first ("Aquamarine, ..."), after a
    greeting ("hey Kollab ..."), or last ("..., aquamarine?"). A name in the
    middle of a sentence is talk about the agent, not to it.
    """
    words = _words(text)
    for name in ("".join(_words(n)) for n in names):
        if not name:
            continue
        for size in (1, 2):
            for start in (0, 1):
                if start and (not words or words[0] not in _GREETING):
                    continue
                if _is_name(words[start : start + size], name):
                    return True, words[start + size :]
            if len(words) > size and _is_name(words[-size:], name):
                return True, words[:-size]
    return False, words


def kind_of(text: str, names=()) -> str:
    """mic_check, thanks, greeting, question, task or request."""
    _, words = address(text, names)
    while words and words[0] in _GREETING:
        words = words[1:]
    body = " ".join(words)
    # Only when that is all they said: "can you hear me? run the tests" is a task.
    for kind, pattern in (("mic_check", _MIC_CHECK), ("thanks", _THANKS)):
        if pattern.search(body) and len(_words(pattern.sub(" ", body))) <= 2:
            return kind
    if not words:
        return "greeting"
    first, second = words[0], words[1] if len(words) > 1 else ""
    if first == "please":
        first, second = second, words[2] if len(words) > 2 else ""
    if first in {"can", "could", "would", "will"} and second == "you":
        verb = words[words.index("you") + 1] if words[-1] != "you" else ""
        return "task" if verb in _ACTION else "question"
    if first in _ACTION:
        return "task"
    if first in _LOOKUP or first in _QUESTION or text.rstrip().endswith("?"):
        return "question"
    return "request"


def answer(text: str) -> str | None:
    """The reply to "was that for me?": "yes", "no", or None for other speech."""
    text = text.strip()
    if _NOT_YOU.search(text) or _IGNORE.search(text):
        return "no"
    if _FOR_YOU.search(text):
        return "yes"
    if _BACKGROUND.search(text) or _NO.match(text) or _SOMEONE_ELSE.search(text):
        return "no"
    if _YES.match(text):
        return "yes"
    return None
