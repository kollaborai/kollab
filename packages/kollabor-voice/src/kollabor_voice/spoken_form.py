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
_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen"
    " fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
# A whole number standing alone: never part of a version, decimal, time, fraction,
# money, a grouped 12,000, or a token like x86, 1st, v2 or gpt-5.
_WHOLE_NUMBER = re.compile(r"(?<![\w.,:/$€£-])(?!0\d)\d{1,9}(?![\w:/%]|[.,]\d)")


def _number_words(n: int) -> str:
    """0 <= n < 10**9 in words: 374 -> "three hundred seventy-four"."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return f"{_ONES[hundreds]} hundred" + (
            f" {_number_words(rest)}" if rest else ""
        )
    size, name = (10**6, "million") if n >= 10**6 else (1000, "thousand")
    high, rest = divmod(n, size)
    return f"{_number_words(high)} {name}" + (f" {_number_words(rest)}" if rest else "")


def spoken_form(text: str) -> str:
    """What a TTS engine should receive for `text`; "" when nothing is left to say.

    Strips the voice context markers, tags, code and markdown, unescapes
    backslashes, says arrows as words, says whole numbers as words, and
    drops other symbols, and keeps punctuation on its word so no engine
    reads a stray dot as "period".
    """
    text = _VOICE_MARKER.sub(" ", text)
    for pattern, repl in _STEPS:
        text = pattern.sub(repl, text)
    text = _WHOLE_NUMBER.sub(lambda m: _number_words(int(m.group())), text)
    text = " ".join(text.split())
    text = re.sub(r"\s+([.,!?;:)])", r"\1", text)  # "save it ." -> "save it."
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"^[\s.,!?;:]+", "", text)
    return text if any(ch.isalnum() for ch in text) else ""
