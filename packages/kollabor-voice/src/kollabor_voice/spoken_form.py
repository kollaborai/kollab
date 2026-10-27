"""The one text-to-speech boundary: a reply in the form a voice can say.

Stdlib only: the engine and the isolated voice runtime both import it.
"""

from __future__ import annotations

import re

# The bracketed context lines voice_context adds to a voice turn; a model that
# echoes one back must not have it read aloud.
_VOICE_MARKER = re.compile(
    r"\[(?:you already said|you were saying|earlier microphone lines|voice mode on"
    r"|voice instructions)\b[^\]]*\]",
    re.I,
)
_STEPS = (
    (re.compile(r"```.*?(?:```|$)", re.S), " "),  # code blocks are never said
    (re.compile(r"</?[A-Za-z_][\w:-]*(?:\s[^<>]*)?/?>"), " "),  # field tags, html
    (re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"),  # a link keeps its words
    (re.compile(r"https?://\S+"), " link "),
    (re.compile(r"\\([^\w\s])"), r"\1"),  # markdown escapes: \. \- \( \*
    (re.compile(r"\\"), " "),
    (re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-+*]\s+|\d+[.)]\s+)", re.M), ""),
    (re.compile(r"(?<=[\w.)])\s*(?:→|⟶|⇒|->|=>)\s*(?=[\w(])"), " to "),
    (re.compile(r"(?<=[\w.)])\s*(?:←|<-)\s*(?=[\w(])"), " from "),
    (re.compile(r"(?<=\w)\s*&\s*(?=\w)"), " and "),
    (re.compile(r"(?<!\d)/|/(?!\d)"), " "),  # 3/4 stays a fraction
    (re.compile(r"\s+--?\s+|—|–"), ", "),
    (re.compile(r"(?<!\w)--(?=\w)"), ""),  # --flag
    (re.compile(r"\s=\s"), " equals "),
    (re.compile(r"(?:->|=>|<-|[*_`#~|<>^=\[\]{}→⟶⇒←])+"), " "),
)


def spoken_form(text: str) -> str:
    """What a TTS engine should receive for `text`; "" when nothing is left to say.

    Strips the voice context markers, tags, code and markdown, unescapes
    backslashes, says arrows as words and drops other symbols, and keeps
    punctuation on its word so no engine reads a stray dot as "period".
    """
    text = _VOICE_MARKER.sub(" ", text)
    for pattern, repl in _STEPS:
        text = pattern.sub(repl, text)
    text = " ".join(text.split())
    text = re.sub(r"\s+([.,!?;:)])", r"\1", text)  # "save it ." -> "save it."
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"^[\s.,!?;:]+", "", text)
    return text if any(ch.isalnum() for ch in text) else ""
