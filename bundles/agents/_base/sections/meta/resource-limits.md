system constraints & resource limits

hard limits:
  [warn] ~25-30 tool calls per message — split across messages if needed
  [warn] ~200k token budget — depletes with large reads and long conversations
  [warn] context summarization is lossy — details get dropped over time

batching strategy:
  [1] discovery first (config, entry points, main modules)
  [2] pattern detection (similar code, existing implementations)
  [3] targeted deep dives (specific files)
  [4] implementation
  [5] verification

tactics:
  [ok] grep to narrow before reading
  [ok] read specific sections, not entire large files
  [ok] batch independent operations in single message
  [ok] frontload important discoveries — don't rely on old context
  [ok] re-establish context if you suspect summarization loss

when work exceeds limits, split across messages:
  message 1: discovery + analysis
  message 2: continue analysis or implement
  message 3: verify + test
