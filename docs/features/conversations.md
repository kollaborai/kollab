---
title: "Conversations and Storage"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Conversations and Storage

Kollab automatically logs every conversation and supports resuming previous sessions.

## How It Works

Every message (user, assistant, system, tool calls) is saved as structured JSONL in a project-specific directory:

```
~/.kollab/projects/<encoded-path>/conversations/
```

The path is encoded from your working directory. For example, `/Users/dev/myproject` becomes `Users_dev_myproject`.

Each conversation gets a unique session ID and timestamped filename.

## Storage Structure

```
~/.kollab/projects/<encoded-path>/
  conversations/
    2026-02-24_session-name.jsonl     # Main conversation log
    raw/                               # Raw API request/response logs
    memory/                            # Intelligence cache (learned patterns)
    snapshots/                         # Conversation snapshots
```

## Resuming Conversations

Use the `/resume` command to pick up where you left off:

```
/resume           # Shows recent conversations to choose from
/resume list      # List all saved conversations
```

The resume system restores the full message history so the LLM has context from the previous session.

## Saving Conversations

Use `/save` to export a conversation in different formats:

```
/save             # Interactive format picker
/save markdown    # Export as readable markdown
/save jsonl       # Export raw JSONL
/save clipboard   # Copy to clipboard
/save local       # Save to current directory
```

## What Gets Logged

- User messages with timestamps
- Assistant responses (full content + metadata)
- Tool calls and their results
- System messages
- Session metadata (model, provider, version)
- Conversation start/end events

## Session Naming

Sessions are automatically named using a combination of timestamp and a generated descriptive name based on the conversation content.

## Per-Project Isolation

Conversations are isolated by working directory. Running `kollab` from `/Users/dev/project-a` stores conversations separately from `/Users/dev/project-b`. This keeps context organized across different projects.

## Configuration

Conversation logging is automatic and cannot be disabled. Storage location follows the project data directory pattern:

```
~/.kollab/projects/<encoded-working-directory>/conversations/
```

The global config at `~/.kollab/config.json` can customize conversation-related settings through dot notation:

```json
{
  "kollabor": {
    "llm": {
      "max_history": 90
    }
  }
}
```

`max_history` controls how many messages are kept in the active conversation context sent to the LLM.
