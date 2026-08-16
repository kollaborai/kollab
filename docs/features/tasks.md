# Task System Architecture

The hub has two task management systems that serve different purposes.

## System 1: Task Ledger (compaction-proof persistence)

Location: plugins/hub/task_ledger.py

TaskCards are JSON files on disk. They get injected into agent context
on every turn, so they survive compaction. Use these for long-running
multi-agent coordination where you need task state to persist across
turns.

Tools (registered in plugins/hub/plugin.py):

- task_checkpoint — report progress on a task
- task_snooze — quiet reminders without changing task status
- task_complete — mark a task done
- task_approve — approve a task (reviewing agents)
- task_reject — reject a task (reviewing agents)

## System 2: Work Queue (lightweight dispatch)

Location: plugins/hub/plugin.py (inline)

A simple FIFO queue for dispatching work items to agents. Items are
claimed by agents and tracked in memory. Use these for one-shot tasks
that don't need compaction-proof persistence.

Tools:

- hub_queue — add a work item to the shared queue
- hub_claim — claim a work item (first come first served)
- hub_work — view current queue and assignments

## When to Use Which

- Multi-step agent coordination → Task Ledger
- Tracking deploy phases across turns → Task Ledger
- Quick one-shot dispatch → Work Queue
- Assigning independent work items → Work Queue

## Example Usage

Assign a tracked task:
  `<hub_task_assign>directive here</hub_task_assign>`

Checkpoint progress:
  `<hub_task_checkpoint id="abc123">progress note</hub_task_checkpoint>`

Quiet reminders while continuing the work:
  `<task_snooze id="abc123" minutes="30"/>`

Complete a task:
  `<hub_task_complete id="abc123">summary of work done</hub_task_complete>`

Add to queue:
  `<hub_queue>description of work item</hub_queue>`

Claim next item:
  `<hub_claim>item-id</hub_claim>`

View queue:
  `<hub_work/>`

## TaskCard Format

TaskCards live in the hub session directory. Each card has:

- id — unique task identifier
- description — what needs doing
- status — active / standby / paused / done / failed / qa_review / closed
- assignee — agent identity working on it
- history — list of checkpoint updates
- snoozed_until — reminder suppression deadline, when set

Agents read task state directly from disk, so it survives context
compaction and session restarts within the same hub session.

## Reminder Behavior

Active tasks can generate cron reminders and checkpoint nudges. If an agent is
still working but needs quiet, `task_snooze` suppresses those reminders for the
requested interval without changing the task status or removing the task from
the system prompt. A single snooze is capped at one hour and does not reset the
task's cron time-to-live clock.

Use a checkpoint to report progress, `task_snooze` to defer reminders, and
`task_complete` to request completion/QA. Snoozing is not a substitute for
finishing or closing the task.
