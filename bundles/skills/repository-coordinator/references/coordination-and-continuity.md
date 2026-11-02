# Coordination and continuity

Read when work spans owners, tracked items, change requests, checkouts, or multiple turns.

## Ownership and checkpoints

For each accepted work item, record its source issue, change request, or authorized task; exact outcome; acknowledged owner and available role; write scope; checkout and ref where applicable; starting revision; checkpoint cadence; acceptance evidence; and dependencies. A planned assignment is not a claim; an assignee is not an accepted owner until acknowledged. Keep one owner per write path and arrange a handoff before changing ownership. A coordinator or integrator is a transferable role, not a global identity.

Use one agreed, discoverable project record per repository for integrator authority, authorized scope, refs/evidence, and the next action. An existing tracker or project record can serve; no new service is required. Before any conditional transfer, first obtain and record verifiable stop evidence for the prior integrator (for example, a stopped process/session in the authoritative roster or an explicit stop acknowledgment confirmed against that roster). Only after that evidence is checked may the record transfer authority to the new owner and state the effective time and scope. A planned transfer or an unverified stop does not grant write authority. If the record, stop evidence, or ownership is missing, conflicting, stale, or unreachable, stay read-only until resolved. A timer, local lock, work slot, or separate queue alone does not establish repository-wide exclusivity.

Delegate only when authorized and file/contract boundaries are clear. Give each worker exact paths, source artifacts to inspect, allowed operations, tests/evidence, and a stop condition. Keep source-review ownership separate from implementation. Do not describe a role skill or agent as loaded or a review as passed without session or artifact evidence.

Honor the requested model and reasoning effort. Verify the running session's actual model from available metadata before agent routing or model-sensitive review; if unavailable, disclose that rather than substituting silently. Keep each checkpoint short and evidence-based: complete, in progress, or blocked with the concrete reason and next owner/action.

## Safe continuation

Before risky branch, merge, or cleanup operations, record local/remote refs and privately preserve unique commits plus staged, unstaged, and untracked bytes where applicable. Verify the refs, binary patches/copies, and hashes before continuing. If exact preservation is uncertain, stop that operation and resolve ownership or preservation first. Never overwrite concurrent work or use broad rollback commands. Existing review checkouts and branches remain until exact cleanup targets are authorized.

For direct task handoffs and compaction, preserve a stable task ID where one exists, exact checkout/path and starting revision, file ownership, authorization, acceptance check, evidence, next gate, and remaining action in an accessible project record. Identify who takes over the integrator role if it transfers. When resuming, compare reported refs and dirty-file state with the actual checkout and re-read current task and host state; a worker's summary is evidence to inspect, not verification. Keep incomplete work pending and unverified rather than rewriting its state as done.

## Evidence and communication

Maintain a dated, bounded evidence ledger in the project's available state store. Distinguish task/issue/board state, source review, checks, runtime acceptance, and user authorization. Recompute counts from complete source data, including pagination where applicable; record the query and `as of` time, and refresh after material changes or before writes. Keep machine-readable state, rendered summaries, and exports consistent; link each material conclusion to the source record or diff.

Send progress updates at useful checkpoints: what changed, what was verified, the next gate, and any blocker. Public comments, review submissions, mentions, emails, and other external messages stay as drafts unless the user explicitly authorizes sending. Never put secrets or private security details in public artifacts.
