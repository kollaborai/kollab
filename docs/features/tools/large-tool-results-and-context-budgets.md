---
title: "Large Tool Results and Context Budgets"
created: 2026-02-24
modified: 2026-11-27
status: active
---
## Large Tool Results and Context Budgets

Tool results are bounded both per result and across the retained tool-history
aggregate. When a result must be reduced, Kollab writes the complete UTF-8
content to a managed `.output` artifact and returns a bounded preview plus the
artifact path to the model. Native tool-call/result envelopes remain paired.

Artifacts live under the current project's conversation data, so a model can
inspect the full result later without forcing the entire payload back into the
context window. See [tool-output artifacts](../../specs/tool-output-artifacts.md)
for limits, configuration, and recovery behavior.

