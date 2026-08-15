# retry hardening plan

objective: make transient failures retry consistently, boundedly, and without replaying visible stream output.

invariant: every provider emits a typed error carrying status/headers when available; every retry owner has one bounded counter; failed stream attempts cannot publish duplicate content; dead MCP transports are immediately reconnectable.

scope:

- provider error mapping and Retry-After parsing
- OpenAI Responses, Anthropic, Gemini, and custom provider adapters
- shared LLM retry classification
- background-task retry lifecycle
- streaming callback ownership during retries
- MCP EOF connection state
- focused regression tests for each repaired seam

non-goals: context-budget policy changes, unrelated dirty docs/CLI work, SDK/deployment configuration, or committing the shared worktree.

contract: retry decisions use typed errors/status codes, Retry-After and Retry-After-Ms are parsed when present, provider-directed delays above the local cap fail fast, fallback retries use bounded exponential backoff with jitter, retries are bounded by the owner that schedules them, a stream is never retried after visible output has been published, and an EOF/reader failure makes an MCP connection uninitialized.

failure boundary: classify and preserve the provider failure at the adapter boundary; do not recover status or transport semantics downstream from formatted error text.

acceptance:

- 408/409/5xx and transport failures follow the intended retry policy; permanent 4xx errors do not; 425 is not retried without an early-data context
- malformed or HTTP-date Retry-After never escapes as ValueError
- custom and Gemini transport failures become retryable typed errors
- background retries execute a fresh coroutine factory and stop at the configured count
- a failed partial stream does not duplicate semantic/UI token output on retry
- MCP EOF causes the next call to reconnect

verification: focused provider, retry, background-task, streaming, and MCP tests; repository package-path test runs; static formatting/lint/compile checks; an isolated retry/EOF probe for the fixed boundaries.

rollback: revert only the new implementation/test/plan files after reviewing the exact diff; preserve all pre-existing dirty files.
