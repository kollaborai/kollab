# Adversarial cross-agent review

Policy for an independent review whose goal is to break a PR's fix, not to
confirm it (#726, extending #715, #717 and #721). It is off by default.

The policy lives in `kollabor/review/adversarial.py`. It does no I/O: the
caller passes the clock and stores the `ReviewLedger` (`to_dict` /
`from_dict`). Product surface: agent policy engine; no hub job, ruleset or
required-status change is part of it (those need separate owner sign-off).
An agent prompt that drives it is not shipped yet.

## Config

```json
"adversarial_review": {
  "enabled": false,
  "round_caps": {"low": 1, "medium": 2, "high": 3, "critical": 3},
  "cooldown": 0,
  "reviewer_pool": []
}
```

- `enabled`: nothing happens until this is exactly `true`.
- `round_caps`: rounds allowed per highest finding severity seen on the PR.
  Each cap is an integer of at least 1; an unknown severity is an error.
- `cooldown`: seconds required between two reviews of the same PR.
- `reviewer_pool`: agent names the caller may pick from.

Repository, check names and agent names are never hardcoded; they come from
this section or from the caller.

## Rules

- `decide()` returns `skip` (disabled, or this head SHA already reviewed),
  `wait` (cooldown, with `retry_after`), `escalate` (round cap reached; the
  owner decides, and it stays escalated) or `review`.
- `record_review()` counts a round only when the review reports a new finding
  or one that was fixed and came back. Still-open known findings do not use a
  round. The highest severity seen sets the cap.
- `assign_reviewer()` never returns the author, anyone in the author's lane or
  anyone who fixed a finding; with nobody left it returns `None`.
- `render_verdict()` writes head SHA, base SHA, commands run and PASS/FAIL.
  Reviewer identity and model are private audit evidence and are not in it.

Each finding still needs a red regression test before its fix (#721).
