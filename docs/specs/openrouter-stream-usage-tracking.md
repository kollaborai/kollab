---
title: OpenRouter Streaming Usage / Token Tracking
created: 2026-07-20
status: completed
author: maintainers
updated: 2026-07-20
---

streamed OpenRouter responses must record their token usage into
`session_stats` so the status widget shows real token / cost / cache numbers.
today the usage chunk is silently dropped and the widget shows `0 tok` for
every OpenRouter session, even successful ones.


## problem

symptom:

- the stats widget shows `N msg | 0 tok | $0.00` for OpenRouter even after many
  successful streamed turns.
- cache-read (`cache_read`) never appears either.
- the non-streaming path and Anthropic are unaffected -- it looks
  OpenRouter-specific.

two separate things get conflated here, keep them apart:

- (a) when every call ERRORS (bad key, 404 model), `0 tok` is *expected* --
  failed calls return no usage. that is not this bug.
- (b) even a fully SUCCESSFUL streamed OpenRouter call reports `0 tok`. that is
  this bug.


## root cause

runtime-confirmed with a live deepseek stream
(`stream_options={"include_usage": True}`). the raw chunk sequence:

```
chunk#1: choices=1 finish_reason=None    delta.content="Hi there! How can" usage=None
chunk#2: choices=1 finish_reason='length' delta.content=""                 usage=None
chunk#3: choices=1 finish_reason='length' delta.content=""                 usage=total=11
```

OpenRouter delivers usage on a FINAL chunk that has `choices=[1]` whose
`delta.content == ""` (empty string, not `None`) with `usage` populated.

`OpenAIResponseTransformer.transform_openai_chunk`
(`packages/kollabor-ai/src/kollabor_ai/providers/transformers.py`):

- line 346 `if content is not None:` treats the empty string `""` as content
  and returns a text delta at `:347` -- BEFORE the usage-extraction block at
  `:374-393`. so the `usage` on that chunk is never read.
- separately, the empty-`choices` guard at `:329-330`
  (`... or not chunk["choices"]: return None`) drops the *other* usage shape --
  an OpenAI-style trailing chunk with `choices: []`. both shapes must be
  handled.

downstream consequence: `transform_openai_chunk` never yields a
`StreamingResponse` carrying `usage` -> `api_communication_service.py:688-745`
never sets `final_usage` -> `last_token_usage` falls to the zero fallback
(`:740`) -> `session_stats` tokens/cost/cache never update -> widget shows
`0 tok`.

context: commit `4d49b1c` routed OpenRouter stream deltas through this shared
transformer ("pass every delta ... straight through"), so this transformer is
now the single point where streamed usage must be captured.


## fix

surface usage before the content / empty-choices early-returns in
`transform_openai_chunk`, covering both shapes. insert near the top, after
`chunk` is known:

```python
# usage can ride a trailing chunk with empty choices (OpenAI) OR a final chunk
# whose delta content is "" (OpenRouter/deepseek). surface it before the
# content / empty-choices early-returns swallow the token accounting.
usage = chunk.get("usage") if chunk else None
choices = (chunk.get("choices") if chunk else None) or []
first = choices[0] if choices else {}
delta0 = first.get("delta", {}) or {}
if usage and usage.get("total_tokens") and not (
    delta0.get("content") or delta0.get("tool_calls")
):
    details = usage.get("prompt_tokens_details", {}) or {}
    return StreamingResponse(
        delta=TextDelta(content=""),
        usage=UsageInfo(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cache_read_tokens=details.get("cached_tokens", 0),
        ),
        is_final=True,
        finish_reason=first.get("finish_reason"),
        raw_chunk=chunk,
    )
```

the `not (content or tool_calls)` guard means a chunk carrying BOTH real content
and usage still streams its content normally; only usage-only / empty-content
chunks divert here.

optional hardening: after a call, look up
`GET https://openrouter.ai/api/v1/generation?id=<id>` for authoritative native
token counts and exact cost, independent of stream-chunk shape. this is the
most reliable source and side-steps per-model chunk quirks entirely.


## verification

- live harness (already run once): stream a completion with `include_usage`,
  run each raw chunk through `transform_openai_chunk`, assert a `UsageInfo` with
  nonzero `total_tokens` is yielded. pre-fix result: `0` yielded while the raw
  stream carried `total=11`; post-fix must match the raw usage chunk.
- unit (`tests/unit/`): feed the observed three-chunk sequence (content delta;
  `finish + "" + null usage`; `finish + "" + usage`) and assert usage is
  surfaced exactly once with the right totals + `cache_read_tokens`.
- tmux (`tests/tmux/specs/`): one successful OpenRouter turn -> status widget
  shows nonzero `tok`.

behavioral change to streaming -> MUST be verified against a live successful
OpenRouter stream, not only unit fixtures.


## files touched

- `packages/kollabor-ai/src/kollabor_ai/providers/transformers.py` (primary)
- `tests/unit/` -- new stream-usage test for `transform_openai_chunk`
- (optional) `openrouter_provider.py` + `session_stats` path if adopting the
  `/generation` lookup
