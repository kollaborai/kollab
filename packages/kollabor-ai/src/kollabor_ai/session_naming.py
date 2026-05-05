"""Session naming utilities for generating memorable session names.

Combines Tech/Code and Mythical/Epic themes for unique, memorable names.
"""

import random
from datetime import datetime
from typing import Optional

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


def generate_session_name(
    theme: Optional[str] = None, include_timestamp: bool = True
) -> str:
    """Generate a memorable session name.

    Args:
        theme: 'tech', 'mythic', or None for random mix
        include_timestamp: Whether to prepend timestamp

    Returns:
        Generated session name like '2512111430-quantum-spark'
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

    name = f"{prefix}-{suffix}"

    if include_timestamp:
        # Prepend timestamp (YYMMDDHHMM) for sorting and uniqueness
        timestamp = datetime.now().strftime("%y%m%d%H%M")
        name = f"{timestamp}-{name}"

    return name


def generate_branch_name() -> str:
    """Generate a name for a branched session.

    Returns:
        Fresh session name like '2512111430-quantum-spark'
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
