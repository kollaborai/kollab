---
title: "Hub Bridge Verification Gap Audit"
doc_type: audit-record
created: 2026-04-20
modified: 2026-05-04
status: historical
---
# Hub Bridge Verification Gap Audit

This record preserves the useful verification scope from an earlier live test
worksheet without retaining raw session notes, local runtime details, or
provider-specific failure artifacts.

## Scope

The verification pass targeted:

- context-service hub broadcasts
- peer context visibility
- file-ledger divergence warnings
- environment notification delivery
- waiting-agent wake behavior
- notification peek and clear commands

## Checks To Preserve

### Hub Bridge

- agent A reads a file and broadcasts the ledger update
- agent B receives the broadcast without duplicating its own vault entry
- agent A edits and re-reads a file while agent B receives a divergence warning
- peer-context queries return the expected peer file entries
- context filters isolate entries for the requested peer
- waiting agents do not wake on passive ledger broadcasts

### Environment Notifications

- approval-mode changes enqueue the expected environment notification
- agent join events are visible to the active agent
- MCP connection events summarize the connected server and tool count
- compaction events report the compacted revision and removed message count
- notification peek is non-draining
- notification clear drains the queue and reports the count
- wake headers summarize idle duration and queued event count

## Release Guidance

These checks should be run as tmux/runtime smoke tests when changes touch the
hub bridge, context service, environment notifications, waiting behavior, or
agent wake scheduling. Prefer raw JSONL evidence over terminal pane text for
assertions.
