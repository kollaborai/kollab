"""Adversarial cross-agent review policy (#726).

Decides whether a PR head gets an adversarial review, who may review it, and
when the owner must step in. The module does no I/O: the caller supplies the
clock and keeps the :class:`ReviewLedger` (``to_dict`` / ``from_dict``).
Repository, check names, agents and cooldowns all come from the
``adversarial_review`` config section; nothing here names a repository.

The feature is off until ``adversarial_review.enabled`` is true.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

SEVERITIES = ("low", "medium", "high", "critical")

#: Keep in step with ``ConfigLoader.get_base_config()["adversarial_review"]``
#: (a test pins the two together; the config package cannot import this one).
DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "round_caps": {"low": 1, "medium": 2, "high": 3, "critical": 3},
    "cooldown": 0,
    "reviewer_pool": [],
}

_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class AdversarialReviewConfig:
    enabled: bool
    round_caps: Dict[str, int]
    cooldown: float
    reviewer_pool: List[str]

    @classmethod
    def from_config(cls, raw: Optional[Mapping[str, Any]]) -> "AdversarialReviewConfig":
        """Build from the ``adversarial_review`` section; bad values raise."""
        raw = raw or {}
        caps = dict(DEFAULT_CONFIG["round_caps"])
        caps.update(raw.get("round_caps") or {})
        for severity, cap in caps.items():
            if severity not in SEVERITIES:
                raise ValueError(f"round_caps: unknown severity {severity!r}")
            if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
                raise ValueError(f"round_caps.{severity} must be an integer >= 1")
        cooldown = raw.get("cooldown", DEFAULT_CONFIG["cooldown"])
        if (
            isinstance(cooldown, bool)
            or not isinstance(cooldown, (int, float))
            or cooldown < 0
        ):
            raise ValueError("cooldown must be a number of seconds >= 0")
        return cls(
            enabled=raw.get("enabled", DEFAULT_CONFIG["enabled"]) is True,
            round_caps=caps,
            cooldown=cooldown,
            reviewer_pool=[str(a) for a in raw.get("reviewer_pool") or []],
        )


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str


@dataclass
class ReviewLedger:
    """What has happened on one PR. Plain data so callers can persist it."""

    reviewed_shas: List[str] = field(default_factory=list)
    rounds: int = 0
    severity: Optional[str] = None  # highest severity seen on this PR
    last_review_at: Optional[float] = None
    open_findings: List[str] = field(default_factory=list)
    resolved_findings: List[str] = field(default_factory=list)
    escalated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {k: list(v) if isinstance(v, list) else v for k, v in vars(self).items()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReviewLedger":
        return cls(**{k: data[k] for k in vars(cls()) if k in data})


@dataclass(frozen=True)
class Decision:
    action: str  # review | skip | wait | escalate
    reason: str
    retry_after: float = 0


@dataclass(frozen=True)
class ReviewOutcome:
    verdict: str  # PASS | FAIL
    new: List[str]
    regressed: List[str]
    reopened: bool


def _rank(severity: str) -> int:
    return SEVERITIES.index(severity)


def decide(
    config: AdversarialReviewConfig, ledger: ReviewLedger, head_sha: str, now: float
) -> Decision:
    """Should ``head_sha`` get an adversarial review right now?"""
    if not config.enabled:
        return Decision("skip", "adversarial review is disabled")
    if head_sha in ledger.reviewed_shas:
        return Decision("skip", f"head {head_sha[:8]} was already reviewed")
    if ledger.escalated:
        return Decision("escalate", "round cap reached; the owner decides")
    if ledger.rounds and ledger.severity is not None:
        cap = config.round_caps[ledger.severity]
        if ledger.rounds >= cap:
            ledger.escalated = True
            return Decision(
                "escalate",
                f"{ledger.rounds} rounds used, cap for {ledger.severity} is {cap}; "
                "the owner decides",
            )
    if ledger.last_review_at is not None:
        remaining = ledger.last_review_at + config.cooldown - now
        if remaining > 0:
            return Decision("wait", "cooldown between rounds", retry_after=remaining)
    return Decision("review", "head has not been reviewed")


def record_review(
    config: AdversarialReviewConfig,
    ledger: ReviewLedger,
    head_sha: str,
    findings: Iterable[Finding],
    now: float,
) -> ReviewOutcome:
    """Record a finished review of ``head_sha``.

    A review uses a round only when it reports a new finding or one that had
    been fixed and came back. Findings that are still open and unchanged do
    not reopen a round.
    """
    findings = list(findings)
    for f in findings:
        if f.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {f.severity!r} on finding {f.id!r}")
    seen = set(ledger.open_findings) | set(ledger.resolved_findings)
    new = [f.id for f in findings if f.id not in seen]
    regressed = [f.id for f in findings if f.id in ledger.resolved_findings]
    present = {f.id for f in findings}

    ledger.reviewed_shas.append(head_sha)
    ledger.last_review_at = now
    ledger.resolved_findings = [
        i
        for i in dict.fromkeys(ledger.resolved_findings + ledger.open_findings)
        if i not in present
    ]
    ledger.open_findings = list(dict.fromkeys(f.id for f in findings))
    for f in findings:
        if ledger.severity is None or _rank(f.severity) > _rank(ledger.severity):
            ledger.severity = f.severity
    reopened = bool(new or regressed)
    if reopened:
        ledger.rounds += 1
    return ReviewOutcome("FAIL" if findings else "PASS", new, regressed, reopened)


def assign_reviewer(
    pool: Iterable[str],
    author: str,
    lane: Iterable[str] = (),
    fixers: Iterable[str] = (),
) -> Optional[str]:
    """First pool member who is not the author, the lane or a fixer, else None."""
    barred = {a.casefold() for a in [author, *lane, *fixers]}
    for candidate in pool:
        if candidate.casefold() not in barred:
            return candidate
    return None


def render_verdict(
    head_sha: str, base_sha: str, commands: List[str], findings: Iterable[Finding]
) -> str:
    """The PR comment: pinned SHAs, commands run, PASS/FAIL, findings.

    Reviewer identity and model are private audit evidence and never appear.
    """
    for name, sha in (("head_sha", head_sha), ("base_sha", base_sha)):
        if not _SHA.fullmatch(sha or ""):
            raise ValueError(f"{name} must be a full 40-character commit SHA")
    if not commands:
        raise ValueError("a verdict must record the commands that were run")
    findings = list(findings)
    lines = [
        "Adversarial review verdict",
        f"Head: {head_sha}",
        f"Base: {base_sha}",
        "Commands run:",
        *[f"- `{c}`" for c in commands],
        f"Result: {'FAIL' if findings else 'PASS'}",
    ]
    if findings:
        lines.append("Findings:")
        lines.extend(f"- {f.id} ({f.severity})" for f in findings)
    return "\n".join(lines)
