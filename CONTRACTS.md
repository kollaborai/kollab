# Issue #621 launcher contracts

Status: frozen with the coordinator against the issue acceptance criteria.

## Scope and ownership

- Lapis owns launcher behavior, the Web launch UI, `/hub org` and `/hub orgs`,
  CLI launcher flags, and launcher/spec documentation in the authorized
  `issue-621-launcher-ui` worktree.
- Aquamarine owns catalog data, bundled agents, skills, and playbooks. Do not
  edit those content files in this lane.
- This contract governs the boundary shared by the catalog, core launcher,
  Web, TUI, CLI, and reviewers. The separate catalog questions below remain
  with the catalog owner/coordinator.

## Frozen behavior

1. Selection uses the persona's `name` (the hub identity), not `display_name`.
   `members` is an optional allowlist; `exclude` is an optional denylist. They
   are mutually exclusive. Unknown or duplicate names are invalid (HTTP 400
   for the API and a clear validation error in CLI/TUI). With neither field,
   preserve today's full-team launch behavior. `excluded` is the list of team
   members omitted by selection, in authored team order; already-running
   members remain `skipped`, not `excluded`.

2. `lead` is an optional selected persona name. Without an override, the
   authored team lead remains the lead. If selection removes the authored
   lead, a selected replacement must be provided; never silently promote a
   different member. A lead override changes lead-first ordering and the
   mission recipient; it does not rewrite authored reporting edges beyond the
   re-parenting rule below.

3. Build a non-mutating effective team before ordering, prompting, or
   launching. Drop excluded members. For each retained member, walk its
   authored `reports_to` chain until the nearest retained ancestor is found;
   use that ancestor as the effective `reports_to`, or `null` if none remains.
   The replacement lead is validated against the retained set. Generate each
   `KOLLAB_LAUNCH_BRIEF` from this effective team so reporting lines and direct
   reports never name removed members and re-parented members name their new
   manager. Team launch order is lead first, then effective hierarchy depth,
   retaining authored order within a level.

4. `mission` is optional and is sent only to the effective lead. If that lead
   is already running, do not send a second mission; report that fact through
   the existing launch notes. `workspace` in the API and `project_dir` in the
   launcher select the target folder, project roster/team library, and Hub
   scope. `--dir` must provide the same folder scoping for CLI launches.

5. All entry points use the same selection semantics: API
   `members`/`exclude`/`lead`/`mission`; CLI `--only` maps to `members`,
   `--without` maps to `exclude`, and `--dir` maps to the folder. The issue's
   non-interactive CLI contract has no `--lead` flag, so an explicit selection
   that removes the authored lead fails clearly instead of silently promoting
   someone. Web and TUI can submit an explicitly selected lead. API responses
   retain `started`, `skipped`, and `failed`, and add `excluded`. Requests that
   omit new fields retain the existing behavior and response meaning.

## Entry-point requirements

- Web and TUI preserve the authored hierarchy while toggling. Turning off a
  manager must offer either cascading exclusion of reports or retaining them
  with nearest-enabled-ancestor re-parenting. The lead cannot be removed
  without selecting a replacement.
- Web member selection persists by team and folder, with Reset restoring the
  default selection. The launch sheet shows folder, profile, enabled members,
  and mission; Agents and New Session share the same launcher.
- `/hub orgs` lists team source, member count, and outline. `/hub org <team>`
  provides the interactive picker, defaults its folder to cwd, and includes a
  mission field. CLI supports the existing mission positional argument plus
  `--dir`, `--without`, and `--only`.

## Unresolved catalog decisions (not owned by this lane)

- Bundle-all versus installable catalog. The issue's proposal is bundle all
  and group/filter the Teams list; do not change catalog delivery policy here.
- Whether playbooks name specific MCP servers. The connector-neutral proposal
  remains unconfirmed; do not author catalog/playbook content here.
