# Tool-output artifacts and aggregate context budget

status: implemented and replay-verified against the recorded wraith-spark failure

## objective

Keep every tool-result turn valid when one result or the combined results exceed
the safe model-input budget. Preserve the complete output in a managed `.output`
artifact and give the model a bounded preview plus a readable path.

## invariant

The model-visible request must satisfy:

`system + tool schemas + retained history + current tool batch <= effective input budget`

Every native tool call still receives exactly one corresponding tool result. A
context trim must never produce an empty request or an orphan-only request.

## scope

- shared artifact writer and bounded-preview formatter in `kollabor-agent`;
- per-result spill at the shared `ToolExecutor` boundary;
- file-read producer integration so its existing source cap also preserves the
  complete returned slice;
- aggregate packing in `QueueProcessor` after all native/XML results finish and
  before they enter conversation history;
- final non-empty recovery guard in `APICommunicationService`;
- focused regression tests and a replay of the recorded `wraith-spark` path.

## non-goals

- changing provider-specific request schemas;
- displaying the complete artifact in the terminal UI;
- deleting or pruning artifacts in this change;
- replacing the existing context summarizer.

## contract

- Artifacts live below the current project's managed conversations directory,
  scoped by session, unless `kollabor.llm.tool_output_dir` is configured.
- Files use the `.output` suffix, UTF-8 encoding, private directory/file modes,
  and names containing a safe tool type/id plus a collision-resistant suffix.
- Result metadata records `tool_output_path`, raw character/byte counts, and the
  spill reason (`per_result` or `aggregate`).
- The model-visible value contains a bounded preview and an explicit path when
  that pointer fits. If no characters remain, the content is empty while the
  artifact path stays in result metadata; the result envelope is never dropped.
- The existing per-result setting remains
  `kollabor.llm.max_tool_output_chars` (default 80,000). The new
  `kollabor.llm.max_tool_batch_output_chars` setting is an optional hard cap for
  the retained/current tool-output aggregate; when unset, the packer derives
  the remaining budget from the provider context window but bounds it by the
  existing per-result allowance so consecutive individually-valid results still
  get reduced before the provider trims their call owners.

## failure boundary

Spill before the result is displayed or appended to history. Aggregate-pack
after all results from the model response are available. Final request
validation must replace an orphan-only/empty window with a recoverable user
message instead of sending `input=[]`.

## acceptance

- An output below both budgets remains inline and no artifact is written.
- An output above the per-result budget writes one exact `.output` file and
  sends only preview/path text.
- Five individually valid outputs that exceed the aggregate budget result in
  bounded model-visible content, five preserved result envelopes, and exact
  artifacts for the spilled results.
- Native Responses call/result pairing survives packing.
- File reads retain their complete returned slice in an artifact when their
  source cap fires.
- Empty and orphan-only request windows never serialize as `input=[]`.
- Artifact-write failure degrades to a bounded explicit warning without
  crashing the tool turn.

## verification

- focused artifact/packer tests;
- file-read and context-budget regression tests;
- native/XML tool-history contract tests;
- broader agent/API test slices;
- replay the recorded `wraith-spark` conversation and inspect raw request,
  artifact, and log evidence.

## rollback

Revert only the new artifact/packer module, its producer seams, guard change,
tests, and this spec. Preserve unrelated dirty files and existing artifacts.
