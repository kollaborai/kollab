---
title: "Execution Flow"
created: 2026-02-24
modified: 2026-11-27
status: active
---
## Execution Flow

```
LLM Request
    |
    v
[Parse Response]
    |
    +---> Native tool_calls -----> [ToolExecutor]
    |
    +---> XML tags ---------------> [ResponseParser] ---> [ToolExecutor]
                                            |
                                            v
                                      [Permission Check]
                                            |
          +-------------------------------+-------------------------------+
          |                               |                               |
    [Approve]                       [Session/Project]                   [Deny]
          |                               |                               |
          v                               v                               v
    [Execute Tool]                  [Execute Tool]                  [Return Error]
          |                               |                               |
          v                               v                               |
    [Return Result] -------------> [Feed Back to LLM] <---------------+
```

When a user cancels a running terminal call, the remaining calls in that
sequential tool batch are marked cancelled and are not started.

