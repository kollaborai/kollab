# Productization policy

Every Kollab enhancement ships as a product surface, not as repository-specific
glue. A product surface is one of: a **skill**, an **extension**, an **agent**,
or a **plugin**. Each surface is:

- **Opt-in and default off.** It has a config toggle that defaults to off. Nothing
  runs until a user turns it on.
- **Repo-agnostic.** Repository owner and name, check names, reviewer and agent
  identities, schedules, and paths come from config or provider discovery. No
  value is hardcoded to `kollaborai/kollab`.
- **Documented.** Its toggles and enable steps are written down next to the surface.
- **Honest about Kollab-internal parts.** Anything that only makes sense inside
  the Kollab repository is flagged explicitly as Kollab-internal.

This policy is tracked in #731. Items in the mapping below are proposals until
their own issues land.

## Checklist

Copy this into the PR body for any change that adds an enhancement. Check an item
only with evidence. A specific N/A reason is allowed; a blank box is not.

- [ ] Names its product surface: skill, extension, agent, or plugin.
- [ ] Has an opt-in config toggle that defaults to off (name the key in the surface's docs).
- [ ] Reads repo, owner, check names, identities, schedules, and paths from config or provider discovery; no literal `kollaborai/kollab` or personal host, device, or user names.
- [ ] Documents the toggles and the enable steps.
- [ ] Flags anything inherently Kollab-internal.
- [ ] Hub job registration, ruleset, or required-status changes have recorded owner sign-off (these are not covered by a plain PR).
- [ ] A failing regression test was written and seen to fail before the fix (#721).

## Mapping: #711-#730

"Kollab-internal" means the item fixes or tests Kollab itself and is not a
reusable surface. Those items are listed so they are not silently dropped.

| Issue | Work | Product surface | Kind | Notes |
|-------|------|-----------------|------|-------|
| #711 | psutil enumeration fuzz coverage | none (repo test) | Kollab-internal | Test of existing code; not an enhancement. |
| #712 | contract tests for event sequences | none (repo test) | Kollab-internal | Test of existing code. |
| #713 | lifecycle tests with real objects | none (repo test) | Kollab-internal | Test of existing code. |
| #714 | concurrency tests for the failure path | none (repo test) | Kollab-internal | Test of existing code. |
| #715 | independent clean-room review gate | skill (review gate) | repo-agnostic | Reviewer identity from config. |
| #716 | PR heads current with main before review | skill (PR gate) | repo-agnostic | Base branch and remote from config. |
| #717 | CI green is not a pass without pinned-head review | skill (PR gate) | repo-agnostic | Ruleset or required-status change needs owner sign-off. |
| #718 | stale-PR sweeper (inactive 5+ days) | agent or plugin (scheduled job) | repo-agnostic | Threshold and schedule in config. |
| #719 | duplicate and overlapping PR detection | skill | repo-agnostic | Read-only report by default. |
| #720 | structured error type and code end to end | none (product fix) | Kollab-internal | Core runtime fix, not a surface. |
| #721 | regression test first, per review finding | skill (process rule) | repo-agnostic | Applies to the SDLC docs; see the checklist above. |
| #722 | auto-merge on green CI plus pinned-head review | plugin (PR gate) | repo-agnostic | Merge authority and ruleset changes need owner sign-off. |
| #723 | token and cost tracking per agent per lane | plugin or extension (usage) | repo-agnostic | Off by default; storage path from config. |
| #724 | definition-of-done check before review | skill (PR gate) | repo-agnostic | Checklist source from `docs/productization.md` or config. |
| #725 | promotion gate for shared checkouts | plugin | Kollab-internal | Tied to Kollab's sandbox layout; flagged as internal. |
| #726 | adversarial cross-agent review with round caps | agent | repo-agnostic | Agent identities and round caps from config. Policy engine in `kollabor/review/adversarial.py`, off by default; see `docs/features/adversarial-review.md`. |
| #727 | hub event hooks to wake agents | plugin (hooks) | repo-agnostic | Hub job and wake registration needs owner sign-off. |
| #728 | recurring hub jobs (refresh, sweep, KPI rebuild) | plugin (hub jobs) | repo-agnostic | Hub job registration needs owner sign-off. |
| #729 | PR throughput KPIs with rolling rate | extension or plugin (KPI) | repo-agnostic | Target rate from config. |
| #730 | retrospective page generator | skill | repo-agnostic | Date window from config or arguments. |

## Enabling a surface

1. Find the surface's own docs for its toggle key and enable steps.
2. Set the toggle in config. The default is off, so nothing changes until you do.
3. Set repo, owner, check names, and identities in config, or let provider
   discovery fill them in.
