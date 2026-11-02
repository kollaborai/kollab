# Hub and coordination tools

Hub handlers require the Hub plugin and its services; registry presence alone does not prove a handler or peer is available. Hub sends can run work in another agent. WorkSlots and TaskLedger cards are separate objects; verify the identifier returned by each operation.

## Permission and reading this catalog

Registry risk and confirmation flags below are metadata, not a promise that a
prompt will or will not appear. The risk assessor reads those fields, while the
permission manager chooses whether to prompt from risk, tool type, mode and
prior approvals; the flag alone does not settle that decision. Trusted/blocked
rules, terminal command patterns, hooks, voice guards and service checks also
matter. Follow the actual decision and the user's authorization. Source:
`packages/kollabor-agent/src/kollabor_agent/permissions/risk_assessor.py`,
`packages/kollabor-agent/src/kollabor_agent/permissions/manager.py`.

### `claims`

Purpose: List the claims held in this process's change-feed view.
Inputs: Required: none. Optional: `identity`.
Result: List of active claims.
Effects: Reads the process-local claim map; it is not a cross-process lock check.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`, `plugins/hub/change_feed.py`.

### `crystal_delete`

Purpose: Delete a vault entry (logged for audit).
Inputs: Required: `entry_id`. Optional: `reason`.
Result: Deletion confirmation.
Effects: Deletes a persistent vault entry; deletion is logged.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `crystal_edit`

Purpose: Update a vault entry's body and/or metadata.
Inputs: Required: `entry_id`, `body`. Optional: `summary`, `keywords`.
Result: Confirmation of edit.
Effects: Updates persistent vault content and/or metadata.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `crystal_list`

Purpose: List vault entries with pagination.
Inputs: Required: none. Optional: `limit`, `offset`.
Result: Paginated list of vault entries.
Effects: Reads a page of vault entries.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `crystal_read`

Purpose: Read full entry body by ID.
Inputs: Required: `entry_id`. Optional: none.
Result: Full vault entry body.
Effects: Reads one vault entry.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `crystal_search`

Purpose: Search vault entries by keyword.
Inputs: Required: `query`. Optional: `limit`.
Result: Matching vault entries.
Effects: Reads matching vault entries.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `feed_file`

Purpose: View changes for a specific file.
Inputs: Required: `path`. Optional: none.
Result: Change history for the file.
Effects: Reads file-change events for one path.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `feed_recent`

Purpose: View recent file changes.
Inputs: Required: none. Optional: `limit`.
Result: Recent file changes.
Effects: Reads recent file-change events.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `file_changed`

Purpose: Notify peers about a file change.
Inputs: Required: `path`. Optional: none.
Result: Change notification sent.
Effects: Publishes a file-change notification to peers.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `file_unwatch`

Purpose: Stop watching a file for changes.
Inputs: Required: `path`. Optional: none.
Result: Watch removed.
Effects: Removes a file-change watch.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `file_watch`

Purpose: Watch a file for changes.
Inputs: Required: `path`. Optional: none.
Result: Watch registered.
Effects: Registers a file-change watch.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_agents`

Purpose: List all online agents.
Inputs: Required: none. Optional: none.
Result: List of online agents with their status.
Effects: Reads the online agent roster.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_broadcast`

Purpose: Broadcast an announcement to all peers.
Inputs: Required: `message`. Optional: `force`.
Result: Broadcast delivery confirmation.
Effects: Sends an announcement to peers.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_capture`

Purpose: View an agent's recent activity as a readable transcript by default, credentials redacted. Works for agent@device on this network's devices, never for a contact's agents.
Inputs: Required: `name`. Optional: `lines`, `format`, `raw`.
Result: Recent bounded transcript (default), plain text, or raw terminal lines.
Effects: Reads an agent transcript; raw terminal capture can be unredacted.
Permission: registry risk `high`, confirmation flag `true`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_claim`

Purpose: Claim a work item from the queue.
Inputs: Required: none. Optional: `claim_id`.
Result: Claimed work item details.
Effects: Claims a queued work item.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_cron_add`

Purpose: Schedule a recurring message to yourself or to another agent.
Inputs: Required: `message`, `interval`. Optional: `to`.
Result: Cron job ID.
Effects: Creates a recurring job which sends messages at the chosen interval.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_cron_delete`

Purpose: Delete a scheduled cron job.
Inputs: Required: `job_id`. Optional: none.
Result: Deletion confirmation.
Effects: Removes the selected recurring job.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_cron_list`

Purpose: List scheduled cron jobs.
Inputs: Required: none. Optional: none.
Result: List of active cron jobs.
Effects: Reads recurring jobs.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_dispatch_verify`

Purpose: Verify a worker's reported completion after independently checking the assigned worktree, commit, and acceptance evidence. Only the original assigner can verify a dispatch.
Inputs: Required: `dispatch_id`, `note`. Optional: none.
Result: Verification state for the durable dispatch.
Effects: Records verification for a dispatch after independent review; only its original assigner may verify it.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_msg`

Purpose: Send a message to a local agent, all local agents, or an `agent@device` from the roster. Remote task messages are subject to the exact human grant and authenticated thread rules in the schema.
Inputs: Required: `to`, `message`. Optional: `wait`, `force`, `thread_id`, `reply_to`, `kind`, `task_id`, `worktree`, `commit`.
Result: Delivery confirmation.
Effects: Sends a message that can cause another agent to act; `wait=true` ends the turn only after a successful delivery.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_queue`

Purpose: Add a work item to the shared queue.
Inputs: Required: `description`. Optional: none.
Result: Work item ID and confirmation.
Effects: Adds a WorkSlot and may also create a separate TaskCard with a textual slot checkpoint when the identity and task ledger are available; the ledger write can fail independently.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_spawn`

Purpose: Spawn a Hub agent by a pool identity, an agent type, or an identity with a type override.
Inputs: Required: `name`, `task`. Optional: `llm`, `type`.
Result: Confirmation with resolved identity and agent type. e.g. 'Created agent nephrite (agent type: coder)'
Effects: Starts an agent process with the requested task and identity/type.
Permission: registry risk `medium`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_status`

Purpose: Get current hub status — roster and coordinator info.
Inputs: Required: none. Optional: none.
Result: Hub status with roster of online agents.
Effects: Reads the Hub roster and status.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_stop`

Purpose: Stop a specific agent or all agents.
Inputs: Required: `target`. Optional: none.
Result: Confirmation that agent(s) were stopped.
Effects: Stops the named agent or all agents; this terminates work.
Permission: registry risk `high`, confirmation flag `true`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_vault`

Purpose: Get an agent's vault summary.
Inputs: Required: none. Optional: `name`.
Result: Vault summary for the requested agent.
Effects: Reads a vault summary.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_vaults`

Purpose: List all agent vaults.
Inputs: Required: none. Optional: none.
Result: List of all vaults with entry counts.
Effects: Reads vault listings and entry counts.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `hub_work`

Purpose: View current work queue and assignments.
Inputs: Required: none. Optional: none.
Result: Current work queue state.
Effects: Reads the current work queue and task assignments.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `lane_claim`

Purpose: Record an advisory claim for a file path in this process's change-feed view.
Inputs: Required: `path`. Optional: `task`.
Result: Claim confirmation or conflict warning; the handler reports a conflict as a successful tool result, so inspect its output.
Effects: Updates a process-local claim map and attempts to persist it; it does not lock the filesystem or coordinate independent processes atomically.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`, `plugins/hub/change_feed.py`.

### `lane_release`

Purpose: Release this identity's advisory path claim in the local change-feed view.
Inputs: Required: `path`. Optional: none.
Result: The handler says the path was released, even when the underlying map says it was absent or owned by another identity.
Effects: Removes this identity's matching process-local claim when present and attempts to persist the map; it does not unlock a filesystem lock.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`, `plugins/hub/change_feed.py`.

### `scratchpad`

Purpose: Overwrite scratchpad with new content. Survives context compaction.
Inputs: Required: `content`. Optional: none.
Result: Confirmation that scratchpad was updated.
Effects: Replaces the scratchpad content, which persists across compaction.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/scratchpad.py`, `plugins/hub/plugin.py`.

### `scratchpad_append`

Purpose: Append content to the existing scratchpad.
Inputs: Required: `content`. Optional: none.
Result: Confirmation that content was appended.
Effects: Appends to the scratchpad.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/scratchpad.py`, `plugins/hub/plugin.py`.

### `scratchpad_clear`

Purpose: Wipe the scratchpad contents.
Inputs: Required: none. Optional: none.
Result: Confirmation that scratchpad was cleared.
Effects: Clears the scratchpad.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/scratchpad.py`, `plugins/hub/plugin.py`.

### `scratchpad_get`

Purpose: Read the current scratchpad contents.
Inputs: Required: none. Optional: none.
Result: Current scratchpad contents, or message that scratchpad is empty.
Effects: Reads the scratchpad.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/scratchpad.py`, `plugins/hub/plugin.py`.

### `state_update`

Purpose: Update your agent state visible to peers.
Inputs: Required: `state`. Optional: none.
Result: State updated.
Effects: Changes the agent activity state visible to peers.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

### `task_approve`

Purpose: Approve a task (used by reviewing agents).
Inputs: Required: `task_id`. Optional: `notes`.
Result: Task approved.
Effects: Records a QA approval in the ledger and may notify the assignee.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/task.py`, `plugins/hub/plugin.py`.

### `task_checkpoint`

Purpose: Report progress update on a task.
Inputs: Required: `task_id`, `progress`. Optional: none.
Result: Progress update acknowledged.
Effects: Adds a progress checkpoint to the task ledger.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/task.py`, `plugins/hub/plugin.py`.

### `task_complete`

Purpose: Submit the assigned or claimed task for QA; the Hub handler calls `request_qa` and notifies the reviewer when possible.
Inputs: Required: `task_id`, `summary`. Optional: none.
Result: QA-request result or an error; final approval is a separate action.
Effects: Requests QA in the task ledger and may send the reviewer a message; it does not itself establish final approval.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/task.py`, `plugins/hub/plugin.py`.

### `task_reject`

Purpose: Reject a task (used by reviewing agents).
Inputs: Required: `task_id`. Optional: `reason`.
Result: Task rejected.
Effects: Records a QA rejection in the ledger and may notify the assignee to rework it.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/task.py`, `plugins/hub/plugin.py`.

### `vault_write`

Purpose: Save an insight to persistent vault memory.
Inputs: Required: `content`. Optional: `keywords`.
Result: Unique vault entry ID (e.g. crys-001).
Effects: Adds persistent content to the agent vault.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`, `plugins/hub/plugin.py`.

## Hub plugin XML-only tools

These handlers are registered by the Hub plugin when both pipeline services
are present. They have no separate central native schema at this revision.
The permission manager applies the current tool-type rules and hooks; a
missing registry entry does not mean unrestricted execution.

### `hub_reply`

Purpose: Reply to a peer in the active thread as a shorthand for `hub_msg`.
Inputs: XML `to`, message body, and optional `wait`.
Result: Delivery confirmation or an error.
Effects: Sends a message and can end the turn after successful delivery when `wait` is true.
Permission: Current pipeline policy and remote trust rules apply.
Source: `plugins/hub/plugin.py`.

### `hub_restart`

Purpose: Restart the current Hub agent under the same identity.
Inputs: No arguments; use the self-closing or empty XML tag.
Result: Restart scheduled, or an error if Hub identity is unavailable.
Effects: Shuts down and replaces the current process image; active work is interrupted.
Permission: Current pipeline policy applies; restart requires user authorization.
Source: `plugins/hub/plugin.py`.

### `task_snooze`

Purpose: Quiet reminders for an active task while leaving it active.
Inputs: XML `id`; optional `minutes`, default 30.
Result: Remaining quiet time, or an error if the task is missing or terminal.
Effects: Updates the task ledger's snooze state; the task stays in the prompt.
Permission: Current pipeline policy and task-ledger checks apply.
Source: `plugins/hub/plugin.py`.

### `global_vault_write`

Purpose: Save a shared insight to the cross-project global vault.
Inputs: XML body content and optional comma-separated `keywords`.
Result: Saved entry ID and summary, or a message that the vault is unavailable or content is empty.
Effects: Adds a persistent global crystal entry and may append a vault stream event.
Permission: Current pipeline policy applies; saving shared information requires appropriate authorization.
Source: `plugins/hub/plugin.py`.

### `hub_ask_ctx`

Purpose: Request a peer's context summary through the context bridge.
Inputs: XML `peer` and optional `filter`.
Result: Context summary or a missing-peer/disabled-bridge error.
Effects: Reads shared peer context; requires the Hub context bridge to be enabled.
Permission: Current pipeline policy and bridge access rules apply.
Source: `plugins/hub/plugin.py`.
