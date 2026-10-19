# Kollab + Mentiko + dsk: merge plan

Status: proposal, 2026-10-18. Nothing built yet.

## The shape: one product, three services

```
     people + work                       engine                          agent runtime
┌───────────────────────────┐ webhook ┌────────────────────────┐ HTTP+SSE ┌────────────────────────┐
│ dsk                       │ ──────▶ │ mentiko                │ ───────▶ │ kollab engine          │
│ users, workspaces, teams  │         │ chains, runs, attempts │          │ sessions, typed events │
│ projects, issues, cycles  │ ◀────── │ schedules, decisions   │ ◀─────── │ hub mesh, vault memory │
│ docs, comments, inbox     │ REST+key│ exec workspaces, PTY   │ turn_    │ LLM loadouts           │
└───────────────────────────┘         └────────────────────────┘ complete └────────────────────────┘
      ▲ OIDC login: dsk is the identity provider for mentiko
      ▲ kollab agents read and write their issues through the dsk MCP server
```

## Who owns what

| Thing | Owner | Others keep |
|---|---|---|
| Users, workspaces, teams, roles, invites | dsk | mentiko caches them on login |
| Projects, issues (= tasks), cycles, docs, comments, notifications | dsk | mentiko stores the issue id only |
| Agent identity (assignee, mentions, comments) | dsk bot user (`users.is_bot` exists) | mentiko profile and kollab persona point at it |
| Agent definition (CLI, model, prompt, skills) | mentiko agent profile; kollab bundle when the CLI is kollab | none |
| Chains, runs, attempts, schedules, decisions, approvals, artifacts, secrets, exec workspaces | mentiko | dsk shows status + a link |
| Agent sessions, conversation history, loadouts, hub mesh, vaults | kollab | mentiko holds the session id per attempt |

## Why three services, not one repo

- Licenses are not a reason: every commit in all three repos is Marco's, so he can align
  them (see License). Once mentiko and dsk share a license, code moves between them freely.
- Stacks: dsk is Next.js + Postgres (Drizzle, pg-boss); mentiko is Next.js + SQLite/files
  + a bash/node runner; kollab is a Python CLI shipped to PyPI and as binaries.
- What people feel as "one product" is one login, one task list, one Run button, one
  domain. All four come from the seams below. None needs code to move.
- Revisit a single repo after the Front door phase if it still hurts.

## License

Decided 2026-10-18: all three are Apache-2.0.

| Repo | Before | Now |
|---|---|---|
| kollab | MIT (63 PyPI releases, which stay MIT) | Apache-2.0 |
| mentiko | Apache-2.0 | Apache-2.0 |
| dsk | AGPL-3.0-or-later | Apache-2.0 |

- Every commit in the three repos is Marco's, so the relicense needed no one else's consent.
- Add a CLA before merging the first outside pull request, so relicensing stays possible.

## Seam 1: dsk ⇄ mentiko

In (dsk → mentiko): signed webhook (`X-Dsk-Signature-256`, already built) to
`POST /api/integrations/dsk/webhook` on mentiko.

- `issue.updated` where the assignee becomes an agent bot user: run that agent's default chain on the issue.
- New dsk automation action `RUN_CHAIN` beside `SET_STATUS | SET_PRIORITY | SET_ASSIGNEE | ADD_LABEL`
  (`src/lib/automation-executor.ts`): rules like "label `needs-research` → run chain research".
- `comment.created` on an issue whose run waits on a human: answers the gate and resumes.

Out (mentiko → dsk): dsk REST with a workspace-scoped bot API key (`api_keys`, scope `write`).

- Run started: state In Progress.
- Run needs a human: state Blocked + a comment with the question.
- Run finished: In Review (or Done) + summary comment + run link; artifacts become attachments.
- Decisions and `generate_tasks`: create dsk issues through the new `dsk` task provider.
- Every write sends `If-Match` with the issue's ETag (`issue:<id>:<version>`) and an
  `Idempotency-Key: run:<runId>:<step>` (dsk has `idempotency_records`).

## Seam 2: mentiko → kollab

- New runner-v2 adapter `kollab-engine`: `POST /sessions`, `POST /{id}/message`, read `GET /{id}/events` (SSE).
- Event mapping: `tool_start`/`tool_result` → activity; `permission_request` → mentiko approval gate;
  `question_gate` → decision; `turn_complete` → attempt complete; `error` → attempt failed.
- This replaces screen-scraping the `ready ·` line for kollab agents. It is the typed
  completion signal the runner-v2 migration is missing. The PTY adapter stays for
  Claude Code, Codex and the other CLIs.
- Each session starts with the dsk MCP server, that agent's bot API key, and toolset
  profile `engineer` (`dsk/openapi/agent-profiles.yaml`).

## Identity

- dsk becomes the OIDC provider (Better Auth `oidcProvider` plugin; the `oauth_applications`
  table exists). Mentiko becomes a client (Better Auth `genericOAuth`). Mentiko signup goes off.
- Tenancy: dsk workspace ↔ mentiko org, 1:1. dsk project ↔ mentiko project only where a
  chain needs project-scoped files.
- Roles map 1:1: both already use owner / admin / member / guest.
- Kollab stays local (`engine.token`); mentiko talks to it server-side only.

## Phases

Three lanes run in parallel. Lanes A and C touch different files in mentiko.

### Lane A: tasks

**A1. dsk task provider in mentiko** (2–3 days)
- Add `web/lib/task-provider/dsk.ts` (same shape as `linear.ts`, calling dsk REST); add `"dsk"` to `types.ts` and `index.ts`.
- Done when: `generate_tasks` from a mentiko decision creates issues in a live dsk workspace, visible in the dsk UI.

**A2. dsk triggers mentiko runs** (4–5 days)
- dsk: webhook to mentiko; `RUN_CHAIN` automation action; seed one bot user per agent.
- mentiko: webhook route + HMAC check; mirror the dsk issue as a native task with
  `metadata.dsk_issue_id` so the existing auto-run and chain-binding code runs unchanged; write-back client.
- Done when: assigning a dsk issue to an agent runs a chain live, and the issue moves
  In Progress → In Review with a summary comment and run link.

**A3. Shrink mentiko tasks** (1 week, after A2 and B1)
- dsk owns title, description, status, assignee, labels, dependencies. `tasks.db` keeps only
  execution state keyed by dsk issue id (chain binding, attempts, gates).
- Importer: existing `tasks.db` rows → dsk issues (through dsk import jobs).
- Mentiko task pages become links into dsk; delete the duplicated task UI.
- Done when: no task content is written to `tasks.db`, and every mentiko screen that listed tasks shows dsk issues.

### Lane B: identity

**B1. Single sign-on** (3–5 days)
- dsk OIDC provider; mentiko `genericOAuth` client; workspace → org and role sync on login; mentiko signup off.
- Done when: log in once at dsk, open mentiko, land in the matching org with the same role.

### Lane C: runtime

**C1. kollab-engine runner adapter in mentiko** (1–2 weeks)
- Server-side engine client (the current one, `web/lib/ai-engine/kollabor-engine-client.ts`,
  is browser-only); adapter in `web/lib/runner-v2/`; event mapping from Seam 2.
- Done when: a 3-step chain of kollab agents completes live with zero screen-scrape
  readiness checks and typed attempts end to end.

**C2. kollab agents work in dsk** (2 days)
- Mentiko starts each kollab session with the dsk MCP server and the agent's bot key.
- Done when: an agent comments on and moves its own issue through MCP, shown in dsk as the bot user.

### Join: front door (1 week, after A3, B1, C1)

**D1. One domain, one top bar**
- Path routing: dsk at `/`, mentiko at `/engine`; shared top bar and design tokens.
- dsk issue inspector gets a Runs panel: status, live log link, Re-run.
- Done when: from one dsk issue you start, watch and finish a run without typing a second URL; checked at 390px and 820px.

Total: about 4–6 weeks with the lanes in parallel.

## What gets deleted

- mentiko: user signup and password login, org member management UI; task content
  columns and task CRUD UI (after A3); the linear / notion / jira / monday providers once
  dsk is the default (outside-tracker sync can live in dsk later).
- dsk: `agents` and `agent_skills` tables. An agent is a bot user; its config lives in mentiko.
- kollab: nothing. The hub TaskLedger stays for agents running outside mentiko.

## Decisions taken (flip any)

1. Three services behind one login, not a code merge.
2. dsk is the identity provider over OIDC, not a shared database.
3. Agents are dsk bot users.
4. Mentiko keeps its execution store; dsk stores only status, comments and links, never run internals.
5. A2 mirrors dsk issues into native tasks first so auto-run ships unchanged; A3 slims it after.

## Open for Marco

1. Mentiko without dsk: require dsk (ship it as a service in mentiko's docker-compose)
   or keep native tasks as a standalone fallback?
   Recommended: require dsk. A fallback doubles the task code forever.
2. Live mentiko tenants: migrate their `tasks.db` in A3, or freeze them on the current version?
3. Brand at the front door: which name sits at `/`?

## Risks

- Lost webhook → stale issue state. dsk already logs outbound deliveries and supports
  redelivery; mentiko adds a reconciler that re-reads open bindings every few minutes.
- Human and agent edit the same issue. ETag on every write; on 412, re-read and comment instead of overwriting.
- The kollab engine runs per machine. SSH and Docker exec workspaces need kollab installed
  there; the PTY adapter is the fallback.
