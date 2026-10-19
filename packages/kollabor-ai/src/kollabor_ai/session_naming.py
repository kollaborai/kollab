"""Session naming utilities for generating memorable session names.

Combines Tech/Code and Mythical/Epic themes for unique, memorable names.
"""

import hashlib
import random
import string
from datetime import datetime
from typing import Optional
from uuid import UUID

# Tech/Code themed words
TECH_PREFIXES = [
    "quantum",
    "neural",
    "cyber",
    "logic",
    "data",
    "pixel",
    "vector",
    "binary",
    "nano",
    "crypto",
    "hyper",
    "meta",
    "proto",
    "synth",
    "matrix",
    "nexus",
    "alpha",
    "omega",
    "sigma",
    "delta",
]

TECH_SUFFIXES = [
    "spark",
    "pulse",
    "forge",
    "stream",
    "core",
    "flux",
    "grid",
    "node",
    "link",
    "wave",
    "byte",
    "code",
    "loop",
    "stack",
    "cache",
    "sync",
    "burst",
    "drift",
    "surge",
    "arc",
]

# Mythical/Epic themed words
MYTHIC_PREFIXES = [
    "phoenix",
    "titan",
    "dragon",
    "kraken",
    "hydra",
    "griffin",
    "sphinx",
    "oracle",
    "atlas",
    "chronos",
    "void",
    "storm",
    "shadow",
    "frost",
    "ember",
    "raven",
    "wolf",
    "serpent",
    "valkyrie",
    "wraith",
]

MYTHIC_SUFFIXES = [
    "rise",
    "forge",
    "spark",
    "deep",
    "flame",
    "quest",
    "reign",
    "blade",
    "crown",
    "soul",
    "heart",
    "wing",
    "claw",
    "fang",
    "eye",
    "born",
    "fall",
    "strike",
    "call",
    "wake",
]


def session_display_name(session_id: str) -> str:
    """Return a readable label without replacing the session's API identity.

    Kollab-generated session IDs already contain a friendly slug. UUID-shaped
    IDs do not, so map them to a stable two-word label for user-facing lists.
    """
    value = str(session_id or "")
    candidate = value.removeprefix("sess_")
    slug = strip_session_decorations(candidate)
    if slug != candidate:
        return slug or "session"

    try:
        canonical_id = UUID(candidate).hex
    except ValueError:
        return value or "session"

    digest = hashlib.sha256(canonical_id.encode("ascii")).digest()
    prefix_index = int.from_bytes(digest[:4], "big") % len(TECH_PREFIXES)
    suffix_index = int.from_bytes(digest[4:8], "big") % len(TECH_SUFFIXES)
    return f"{TECH_PREFIXES[prefix_index]}-{TECH_SUFFIXES[suffix_index]}"


def _uniqueness_token() -> str:
    """Random 4-char token that always starts with a digit.

    Two names generated in the same minute collide without it (800 word
    combos), and a colliding resume/branch save_session() truncates the
    existing transcript. A leading digit keeps the token distinguishable
    from the word parts for display stripping.
    """
    chars = string.ascii_lowercase + string.digits
    return random.choice(string.digits) + "".join(random.choices(chars, k=3))


def strip_session_decorations(session_id: str) -> str:
    """Drop the timestamp prefix and uniqueness token from a session id.

    '2512111430-quantum-spark-7k2x' -> 'quantum-spark'. Ids without the
    generated shape are returned unchanged.
    """
    candidate = str(session_id or "")
    prefix, separator, rest = candidate.partition("-")
    if not (separator and len(prefix) == 10 and prefix.isdigit()):
        return candidate
    words = rest.rsplit("-", 1)
    if (
        len(words) == 2
        and len(words[1]) == 4
        and words[1][0].isdigit()
        and words[1].isalnum()
    ):
        return words[0]
    return rest


def generate_session_name(
    theme: Optional[str] = None, include_timestamp: bool = True
) -> str:
    """Generate a memorable session name.

    Args:
        theme: 'tech', 'mythic', or None for random mix
        include_timestamp: Whether to prepend timestamp

    Returns:
        Generated session name like '2512111430-quantum-spark-7k2x'

    The trailing token makes the name unique among names generated in the
    same minute; session files are keyed by it and a collision would
    truncate another conversation's transcript.
    """
    if theme == "tech":
        prefix = random.choice(TECH_PREFIXES)
        suffix = random.choice(TECH_SUFFIXES)
    elif theme == "mythic":
        prefix = random.choice(MYTHIC_PREFIXES)
        suffix = random.choice(MYTHIC_SUFFIXES)
    else:
        # Mix themes randomly
        if random.choice([True, False]):
            prefix = random.choice(TECH_PREFIXES)
            suffix = random.choice(TECH_SUFFIXES)
        else:
            prefix = random.choice(MYTHIC_PREFIXES)
            suffix = random.choice(MYTHIC_SUFFIXES)

    name = f"{prefix}-{suffix}-{_uniqueness_token()}"

    if include_timestamp:
        # Prepend timestamp (YYMMDDHHMM) for sorting
        timestamp = datetime.now().strftime("%y%m%d%H%M")
        name = f"{timestamp}-{name}"

    return name


def generate_branch_name() -> str:
    """Generate a name for a branched session.

    Returns:
        Fresh session name like '2512111430-quantum-spark-7k2x'
    """
    return generate_session_name()


def get_all_prefixes() -> list:
    """Get all available prefixes."""
    return TECH_PREFIXES + MYTHIC_PREFIXES


def get_all_suffixes() -> list:
    """Get all available suffixes."""
    return TECH_SUFFIXES + MYTHIC_SUFFIXES


# Quick test
if __name__ == "__main__":
    print("Tech themed:")
    for _ in range(5):
        print(f"  {generate_session_name('tech')}")

    print("\nMythic themed:")
    for _ in range(5):
        print(f"  {generate_session_name('mythic')}")

    print("\nMixed:")
    for _ in range(5):
        print(f"  {generate_session_name()}")

    print("\nBranch names:")
    for _ in range(3):
        print(f"  {generate_branch_name()}")
