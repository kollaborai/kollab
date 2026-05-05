---
title: "wait_for_user Loop Fix Implementation Record"
doc_type: implementation-record
created: 2026-04-18
modified: 2026-05-04
status: historical
---
# wait_for_user Loop Fix Implementation Record

This record captures the fix for an agent-loop regression where agents could be
re-invoked repeatedly after emitting `<wait_for_user />`.

## Summary

Agents parked by `<wait_for_user />` could enter repeated follow-up turns because
two runtime paths independently re-triggered continuation work:

- repeated `TRIGGER_LLM_CONTINUE` events stacked background retry tasks while the
  coordinator was already processing
- hub nudge evaluation forced continuation even when an agent had intentionally
  entered the waiting state

The fix coalesced retry scheduling and made nudges passive.

## Root Cause

### Retry Stacking

`kollabor/llm/message_handler.py` scheduled a new background retry each time a
continuation event arrived while `coord.is_processing` was true. During active
hub traffic, multiple peer messages could create multiple pending retries. When
processing finished, each retry attempted another continuation turn.

### Forced Nudge Continuation

`plugins/hub/plugin.py` allowed nudge evaluation to set `force_continue=True`.
That changed queue bookkeeping so the loop continued despite the agent being in
the waiting state.

## Fix

- Added a `_retry_pending` gate so continuation retries coalesce while one retry
  is already queued.
- Removed forced continuation from hub nudges.
- Kept nudges as passive injected messages that ride along with the next natural
  turn.
- Removed the dead consecutive-nudge continuation counter.

## Files Changed

- `kollabor/llm/message_handler.py`
- `plugins/hub/plugin.py`
- `tests/unit/llm/test_message_handler.py`

## Related Runtime Contract

Native tool calls need an explicit queue-processor override when tool results
are appended to history. XML-only completion detection cannot see native
provider tool blocks, so `queue_processor.py` keeps the turn open when native
tools are present unless question-gate behavior is active.

## Follow-Up Verification

Future changes in this area should verify:

- `<wait_for_user />` parks without repeated self-invocation
- hub peer messages coalesce into one pending continuation
- passive nudge messages do not wake waiting agents by themselves
- native tool results are observed by the model on the next turn
- scheduled hub messages wake the intended agent only when the normal wake
  conditions are satisfied
