---
name: kollab-development
description: "Use only when modifying the Kollab app repository or when Kollab maintenance is explicitly requested, including Luna agent assignments. Covers code, commands, configuration, networking, deployment packaging, specs, and docs for the current development implementation. Does not apply to unrelated coding tasks or projects merely opened in Kollab."
---

# Kollab development

## Scope and product direction

These are repository maintenance instructions, independent of the model running
them. Apply them when changing Kollab itself. Resolve paths below from the Kollab
repository root. Follow the current user request and higher-priority instructions
when they differ from this skill.

Kollab is under active development, with no established user migration or legacy
support commitment. Published development packages do not justify speculative
compatibility layers. Follow explicit release requests and preserve actual stored
state and running peer contracts; establish compatibility obligations from real
consumers or the maintainer, rather than inventing them.

- Change the current implementation directly. Do not create `V2`, `next`,
  `legacy`, alternate services, or duplicate specs to avoid updating existing code.
- Do not invent backward compatibility, migration layers, feature flags, dual
  writes, old endpoint support, or multiple supported releases for imaginary users.
  Trace actual callers and stored state before deciding what must be preserved.
- Keep the complete requested outcome. Do not silently defer required scaling,
  security, recovery, or operational behavior to a future version or an MVP.
  If something remains incomplete, identify the concrete gap and continue the work.
- Kollab is one app that can run interactively or as a service. Use its existing
  entrypoints and lifecycle to manage required sidecars; do not create a separate
  relay product or duplicate implementation in the website repository.
- Keep self-hosting open source and usable without a paid control plane or a
  mandatory hosted account. Check dependency licenses and real operating costs;
  free software does not imply free servers.
- Wire/schema version fields, signed revisions, package metadata, and recorded
  historical paths have meaning. Do not rename them just to erase the string `v1`
  or `v2`. Change a real protocol contract deliberately and update both ends.

## Before editing

1. Read root `AGENTS.md`, relevant `CLAUDE.md` sections, and any instructions nearer
   the affected files. Inspect `git status --short` and the scoped diff. Identify
   the requested behavior, existing edits, source owner, and evidence needed.
2. Prefer the code graph for definitions and call paths. If its index is absent,
   index this checkout. If the tool is unavailable, say so once and use focused
   `rg` searches and exact source reads. Do not repeatedly wait on a broken tool.
3. Trace producer, consumers, configuration, and runtime entrypoint. Check the
   active process/source artifact when diagnosing deployed behavior. Old docs,
   memory, a different checkout, or a helper's conclusion are leads, not proof.
4. Read the current feature spec and name the concrete contract being changed.
   Amend that spec in place alongside implementation. Record actual unresolved
   decisions without inventing another version of the design.
5. For DNS, `/connect`, relay, A2A, or deployment work, read
   [relay-change-guide.md](references/relay-change-guide.md) before editing.

## Release worktree and checkout preservation

For any release request, read `docs/release-process.md` and prepare it in a
dedicated worktree created from the fetched target commit. Record the active
checkout's branch, HEAD, and staged/unstaged/untracked state first. If it is dirty,
do not edit or stage release files there. Do not modify, reset, stash, clean,
restore, or branch-switch the user's active checkout to make release preparation
convenient.

Stage an explicit list of release-owned files only. Review staged paths and the
full staged diff before committing; never use broad staging in a shared checkout.
Merge the release-prep PR through the normal CI gate, and tag only the verified
merged commit after checking package versions and changelog parity. After merge
or publish, leave the original checkout as-is; do not auto-sync or clean it.
Reconcile local changes only when explicitly requested, after comparing each
local staged, unstaged, and untracked change with upstream and verifying that
reconciliation preserves all of them.

## Put the change in its existing owner

- `kollabor/`: app startup, command plumbing, LLM orchestration and state wiring.
- `packages/kollabor-agent/`: reusable agent/tool runtime.
- `packages/kollabor-ai/`: providers, profiles, prompts and conversations.
- `packages/kollabor-tui/`: terminal input, widgets and rendering.
- `packages/kollabor-events/` and `packages/kollabor-plugins/`: shared hook/plugin APIs.
- `plugins/`: concrete features; Hub networking belongs under `plugins/hub/`.
- `bundles/agents/` and `bundles/skills/`: prompts, metadata and reusable instructions.

Reuse hooks, the command registry, config accessors and existing lifecycle methods.
Clean up owned tasks and connections on shutdown. TUI changes go through
`MessageDisplayCoordinator`, the design system, and terminal-state helpers; do not
write directly to renderer internals or print into an active TUI. Read the relevant
existing implementation before copying a prompt's illustrative code.

## Change the whole contract, without collecting unrelated work

- Update all actual callers, both protocol ends, command help, examples and the
  canonical spec when the contract changes. Search for the old names and paths.
- Replace obsolete code in the affected feature when the requested change makes
  it unnecessary. Preserve unrelated edits and identify exact removal targets.
- Do not reset, stash, switch branches, overwrite concurrent work, broadly stage,
  commit, push or deploy merely because implementation is complete. Honor the
  user's existing authorization for those operations.
- Keep private keys, identity pins, signed revision counters, invitations and
  credentials outside generated source artifacts. Unreleased code can still have
  valuable persistent state; never reset identity as a shortcut.
- Use existing checks relevant to the changed boundary. Follow the session's test
  authorization; do not add/run tests when the session forbids them. State plainly
  what was inspected, executed, skipped, or remains unverified. Compilation, a unit
  result, live behavior, and deployment are separate claims.

## Agent assignments and reporting

The bundled `kollabor` maintainer assigns and auto-loads this skill. Kollab preserves
existing user agent files during seeding: when installing into an existing setup,
inspect the resolved local/global agent metadata. It needs this name in both
`skills` and `default_skills` (or `skills: ["*"]` plus the default). Merge only those
entries and preserve custom prompts/settings. Higher-priority local skills can
override bundled/global content; confirm the selected path. An already-running
agent is not updated by editing these files. On a fresh activation, explicit use is
`kollab --agent kollabor --skill kollab-development` once the metadata is assigned.

Give a Luna agent a concrete assignment, not just “fix the relay.” Include:

```text
Read bundles/skills/kollab-development/SKILL.md first.
objective: the complete requested behavior and observable result
checkout: exact path; branch/status and relevant existing changes
ownership: files/contracts this agent owns; other agents' concurrent ownership
constraints: one current development implementation; no speculative compatibility
source: entrypoint, producers/consumers, canonical spec, relevant evidence
authorization: what edits, checks, deployment or cleanup are already authorized
completion: required behavior, evidence to return, remaining gaps
```

Split independent work only where interfaces and file ownership are clear. Helpers
must return actual paths/diffs and evidence, not claims that their work “should”
function. The coordinating agent inspects integration and resolves conflicting
edits. A running agent or a planned step is not a completed result.

Finish with: changed behavior and files; evidence and its limits; remaining work;
exact commit/deploy state if relevant. Keep the user informed with concrete results.
Do not claim scale from configurable limits or call unfinished requirements done.
