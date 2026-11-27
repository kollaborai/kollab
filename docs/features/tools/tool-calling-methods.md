---
title: "Tool Calling Methods"
created: 2026-02-24
modified: 2026-11-27
status: active
---
## Tool Calling Methods

Different LLM providers use different protocols for tool calling:

### Anthropic (Claude)

Uses native tool calling with structured `tool_use` blocks:

```json
{
  "type": "tool_use",
  "id": "toolu_01...",
  "name": "terminal",
  "input": {"command": "ls -la"}
}
```

Also supports XML tags as fallback when native calling is disabled.

### OpenAI / OpenAI-compatible

Uses native `tool_calls` format:

```json
{
  "tool_calls": [{
    "id": "call_01...",
    "type": "function",
    "function": {
      "name": "terminal",
      "arguments": "{\"command\": \"ls -la\"}"
    }
  }]
}
```

### Gemini

Transformed to/from Anthropic format for compatibility.

### OpenRouter

Depends on the underlying model. Routes through appropriate transformer.

### Configuration

Native tool calling can be disabled per profile:

```json
{
  "profiles": {
    "my-profile": {
      "native_tool_calling": false,
      ...
    }
  }
}
```

When disabled, the LLM uses XML tags instead.

