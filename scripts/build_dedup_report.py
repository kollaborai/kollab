#!/usr/bin/env python3
"""Render outputs/dedup-report.md from the triage workflow results.

Reads /tmp/triage_results.json (the workflow's .result payload). Reproducible:
re-run any time to regenerate the report from the same triage data.
"""
import json
import os

res = json.load(open("/tmp/triage_results.json"))
r = res["results"]
cl = {c["id"]: c for c in res["clusters"]}
by_id = {x["id"]: x for x in r}

# Clusters #4/#5/#19/#20 all live in the SAME forked file pair
# (conversations_altview.py vs conversations_plugin.py). A sibling agent
# (#4) found one is "a verbatim port" of the other. They must be decided as a
# UNIT (is the legacy fullscreen file still reachable?), not consolidated
# function-by-function -- so they get their own section and are pulled out of
# both the auto-fix list and the generic report buckets.
CONV_TWINS = {4, 5, 19, 20}

# Clusters #0/1/2/6/7/8/10/16/17/24/25/32 are ~12 byte-identical functions shared
# by clean_renderer.py and message_renderer.py -- one entire renderer forked from
# the other, NOT 12 independent dupes. Same systematic-duplication pattern as the
# conversations twins, just bigger. Grouped so the report surfaces the ONE
# decision (extract shared _tool_detail.py vs retire a dead renderer) instead of
# 12 fragments. Still render-hot-path -> report only.
RENDER_TWINS = {0, 1, 2, 6, 7, 8, 10, 16, 17, 24, 25, 32}

TWINS = CONV_TWINS | RENDER_TWINS

# Only genuinely-mechanical merges auto-fix.
# #29: byte-identical path helpers in the same dir, one private caller each.
# #31: a dead wrapper (hub_bridge) + a thin wrapper with one same-file caller
#      (project_scope); canonical encode_project_path already imported in both.
# #5 and #9 were re-scoped OUT after close reading: #5 adds new public package API
# (design call) and is part of the conversations twins; #9 merges into the
# risky_other #14 cluster and changes behavior in model.py/login.py -- a refactor,
# not a dedup. Both reserved for human review.
AUTOFIX = {29, 31}


def members_str(cid):
    return "\n".join(
        f"  - `{m['file']}:{m['line']}` `{m['name']}()`" for m in cl[cid]["members"]
    )


def names(cid):
    return ", ".join(sorted({m["name"] for m in cl[cid]["members"]}))


lines = []
A = lines.append

A("# Duplicate-Function Scour Report\n")
A(
    "_Generated 2026-05-28 via AST detector (`scripts/find_dupes.py`) + "
    "46-agent triage workflow (`scripts/dedup_workflow.js`)._\n"
)

A("## Summary\n")
xfile = sum(1 for c in res["clusters"] if c["cross_file"])
A(f"- **{res['total']}** exact-body clusters detected ({xfile} cross-file)")
A(f"- **{res['real_dupes']}** genuine copy-paste duplicates")
A(f"- **{len(AUTOFIX)}** auto-fixed (pure utility, no render/hub path, mechanical merge)")
A(
    f"- **{len(RENDER_TWINS)}** functions are `clean_renderer.py`/`message_renderer.py` "
    "forked twins — one extract-shared-module decision (see below)"
)
A(f"- **{len(CONV_TWINS)}** conversations-twin functions — decide as a unit (see below)")
A(
    f"- **{sum(1 for x in r if x['verdict']=='intentional')}** intentional "
    "(framework overrides, same-file aliases) — left alone"
)
A(
    f"- **{sum(1 for x in r if x['verdict']=='not_dupe')}** coincidental matches "
    "(identical body, different contract) — left alone\n"
)
A(
    "Detection is mechanical (AST body-hash, docstrings/whitespace/var-names "
    "normalized) so it is deterministic and catches cross-file duplicates that "
    "subsystem-siloed reading would miss. Each cluster was then triaged by an "
    "agent that read the actual function bodies and call sites.\n"
)
A(
    "**The conservative auto-fix count is the pipeline working as designed**, not "
    "underdelivering: 14 real dupes live in the render hot path (CLAUDE.md forbids "
    "auto-edits there), and the rest carry import-cycle or design decisions reserved "
    "for a human. The report is the deliverable; the safe automated changes are two "
    "trivial path/encode helper merges.\n"
)
A("---\n")

A("## Auto-fixed (applied)\n")
A(
    "Pure utility, not in the render hot path or hub flow, provably value-preserving. "
    "Applied on the working tree and verified: `ruff` clean, `py_compile` clean, "
    "full unit suite shows zero new failures (set-diff against baseline).\n"
)
APPLIED_NOTES = {
    29: (
        "**Applied:** deleted `_current_package_root` from `auto_update.py`, added "
        "`_current_source_root` to the existing `from .git_update import ...` line, "
        "repointed the one caller. Both helpers were byte-identical and both files "
        "live in `kollabor/updates/`, so `parents[2]` resolves to the same root."
    ),
    31: (
        "**Applied:** `_encode_project_path` in `hub_bridge.py` was dead (zero callers) "
        "— deleted it and dropped the now-unused `encode_project_path` from its import "
        "block. `_encode_path` in `project_scope.py` had one same-file caller "
        "(`resolve_project_id`) — repointed it to the already-imported "
        "`encode_project_path` and deleted the wrapper. No new cross-package "
        "dependency: both files already imported the canonical function."
    ),
}
for cid in sorted(AUTOFIX):
    x = by_id[cid]
    A(f"### Cluster #{cid} — `{names(cid)}` ✔ fixed\n")
    A(members_str(cid))
    A(f"\n**Canonical home:** {x['canonical']}\n")
    A(f"**Why safe:** {x['rationale']}\n")
    A(f"**Call sites:** {x['callers_note']}\n")
    A(APPLIED_NOTES[cid] + "\n")
A("---\n")

A("## Renderer twins — one extract-shared-module decision (biggest finding)\n")
A(
    "`packages/kollabor-tui/.../clean_renderer.py` and "
    "`packages/kollabor-tui/.../message_renderer.py` share **"
    f"{len(RENDER_TWINS)} clustered functions** (11 byte-identical, 1 cosmetically "
    "diverged: `_truncate_plain`) — an entire tool-detail rendering helper family was "
    "forked between the two renderers. A 13th family member, `_tool_badge_color`, also "
    "diverged cosmetically and so wasn't auto-clustered. This is **one systematic "
    f"duplication, not {len(RENDER_TWINS)} independent dupes**: one extract decision "
    "resolves all of them at once.\n"
)
A(
    "**A follow-up investigation (read-only agent) confirmed both renderers are LIVE** "
    "— `CleanRenderer` is the config default (`application.py:385`), "
    "`ModernMessageRenderer` is the coordinator constructor fallback "
    "(`message_coordinator.py:109`) plus a config option, and `SimpleRenderer` already "
    "imports 4 of these helpers FROM `clean_renderer`. So retiring a renderer is a "
    "product decision, out of scope. **Recommendation: extract the live helpers into a "
    "neutral `packages/kollabor-tui/src/kollabor_tui/_tool_detail.py`** that all three "
    "renderers import (canonical = clean's copies; it's the live default). Footprint is "
    "tiny: `re`, `shlex`, `Optional`, and `T`/`solid_fg` from `design_system`.\n"
)
A(
    "**Three dead copies to delete in the same pass** (0 call sites, 0 external "
    "importers — guaranteed safe): `_tool_symbol` in clean_renderer (`:236`), "
    "`_tool_symbol` in message_renderer (`:171`), and a vestigial `@staticmethod "
    "_truncate_plain` in `message_renderer.py:559` (all bare `_truncate_plain(...)` "
    "calls resolve to the module-level fn at `:186`, never the static method). "
    "11/13 are byte-identical; `_truncate_plain` and `_tool_badge_color` differ only "
    "cosmetically (docstring word / `in {x}` vs `== x`).\n"
)
A(
    "**REPORT ONLY** — these files are the CLAUDE.md render hot path. The extract must "
    "be tmux-verified before/after (render a tool_call + tool_result + agent_message "
    "under clean, then under modern, diff captures → must be byte-identical), so it is "
    "a human/agent task with verification, not an auto-fix.\n"
)
A("Functions in the pair:\n")
for cid in sorted(RENDER_TWINS):
    x = by_id[cid]
    copies = cl[cid]["count"]
    extra = " (+1 intra-file copy)" if copies > 2 else ""
    A(f"- **#{cid} `{names(cid)}`**{extra} — _{x['verdict']}_")
A("")
A("---\n")

A("## Conversations twins — finish the altview migration (UI bug, not just a dupe)\n")
A(
    "`plugins/altview/conversations_altview.py` and "
    "`plugins/fullscreen/conversations_plugin.py` are a forked file pair. The AST "
    "pass flagged **four** matching functions and each per-cluster agent (seeing only "
    "its slice) reached a different verdict. This is ONE systematic duplication — and "
    "investigating it surfaced a live UI bug.\n"
)
A(
    "**Verified facts (each grep/line-checked, not taken on agent word):**\n"
    "- **Fullscreen wins by registration order.** `application.py:438` initializes "
    "fullscreen commands BEFORE altview (`:441`). Fullscreen claims "
    "`conversations`/`convos`/`sessions` first, so `altview/command_integration.py:231` "
    "sees the command already registered and SKIPS. `ConversationsAltView` loads but no "
    "command routes to it.\n"
    "- **So the OLD, flickery fullscreen renderer is what runs on `/conversations`** — "
    "the AltView stack pauses the main render loop on push "
    "(`altview/stack_manager.py:157` `scheduler.pause()`), which is the flicker-"
    "prevention the newer architecture exists to provide. The shadowed path means users "
    "get the flicker the migration was meant to kill.\n"
    "- **The altview port is unfinished:** `conversations_altview.py:484` "
    "`get_resume_session()` has ZERO callers on the altview path "
    "(verified: def-only, no call in `plugins/altview/` or `kollabor/altview/`). Its "
    "only consumer is `fullscreen/command_integration.py:257`. So even if altview won "
    "the command today, selecting a session wouldn't resume — the glue was never wired.\n"
)
A(
    "**Recommendation: finish the migration forward, scoped to conversations.** "
    "AltView is the intended architecture (it pauses rendering to prevent flicker); the "
    "fix is NOT to delete it. Two parts, both required — neither works alone:\n"
    "1. **Wire altview's resume glue** — consume `get_resume_session()` in the altview "
    "pop path, mirroring `fullscreen/command_integration.py:257-351` (read the selected "
    "session, emit `ADD_MESSAGE` to actually resume).\n"
    "2. **Retire ONLY fullscreen's `conversations` registration** so altview wins. Do "
    "NOT flip the global `438`/`441` init order — that would change the winner for "
    "every fullscreen↔altview pair (matrix, example, login, mcp_wizard).\n"
    "Why not the two delete-shortcuts: deleting *fullscreen* alone fails over to the "
    "unwired altview port → broken resume. Deleting *altview* alone locks in the "
    "flicker permanently. Render hot path → needs a tmux acceptance test "
    "(`/conversations` → altview opens → select → resume fires) before it lands.\n"
)
A(
    "Divergence note: of the 4, only `set_app` and `get_resume_session` are truly "
    "byte-identical; `_render_footer` and `_get_display_name` differ cosmetically "
    "(comments / line-wrapping) with identical logic. The AST pass normalized those away.\n"
)
A("Functions in the pair:\n")
for cid in sorted(CONV_TWINS):
    x = by_id[cid]
    A(f"- **#{cid} `{names(cid)}`** — per-cluster verdict: _{x['verdict']}/{x['category']}_")
    for m in cl[cid]["members"]:
        A(f"    - `{m['file']}:{m['line']}`")
A("")
A("---\n")

A("## Real duplicates — REPORT ONLY (need human review / live verification)\n")
cats = {
    "render_path": "Render hot path (CLAUDE.md forbids auto-edits — behavioral changes need live tmux verification)",
    "hub_flow": "Hub message flow (CLAUDE.md forbids auto-edits — needs live verification)",
    "cross_package_contract": "Cross-package contract (merging creates/deepens a package dependency or risks an import cycle)",
    "risky_other": "Real dupe, non-trivial consolidation",
    "safe_util": "Utility dupe blocked from auto-fix (import-cycle direction or ambiguous home)",
}
for cat, desc in cats.items():
    items = [
        x
        for x in r
        if x["verdict"] == "real_dupe"
        and x["category"] == cat
        and x["id"] not in AUTOFIX
        and x["id"] not in TWINS
    ]
    if not items:
        continue
    A(f"### {desc}\n")
    for x in items:
        A(f"**#{x['id']} — `{names(x['id'])}`** ({cl[x['id']]['count']} copies)")
        A(members_str(x["id"]))
        A(f"\n_{x['rationale']}_\n")
        A(f"Proposed home: {x['canonical']}\n")
A("---\n")

A("## Dismissed (not real duplicates)\n")
A("### Intentional (framework overrides, same-file alias pairs, abstract hooks)\n")
for x in r:
    if x["verdict"] == "intentional" and x["id"] not in TWINS:
        A(f"- **#{x['id']} `{names(x['id'])}`** — {x['rationale']}")
A("\n### Coincidental (identical body, different semantic contract)\n")
for x in r:
    if x["verdict"] == "not_dupe" and x["id"] not in TWINS:
        A(f"- **#{x['id']} `{names(x['id'])}`** — {x['rationale']}")

os.makedirs("outputs", exist_ok=True)
out = "\n".join(lines)
open("outputs/dedup-report.md", "w").write(out)
print(f"wrote outputs/dedup-report.md ({len(out)} bytes)")
