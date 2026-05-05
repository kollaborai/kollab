---
title: "Pipe Mode"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Pipe Mode

Pipe mode allows Kollab to process input from stdin and exit immediately after generating a response. This enables scripting, automation, and integration with other tools.

## Usage

### Basic Pipe Mode

```bash
# Pipe input to kollabor
echo "what is 2+2?" | kollab -p

# Use with query argument
echo "The code has bugs" | kollab "fix these issues" -p

# Process file content
cat file.py | kollab "explain this code" -p

# Git diff integration
git diff | kollab "write a commit message" -p
```

### With Timeout

```bash
# Custom timeout (default: 2min)
echo "long task" | kollab -p --timeout 5min
echo "query" | kollab -p --timeout 30s
cat large.txt | kollab "summarize" -p --timeout 1h
```

### Timeout Formats

- `30s` or `30sec` - 30 seconds
- `5min` or `5m` - 5 minutes
- `1h` or `1hour` - 1 hour

## Input Handling

When both stdin and a query argument are provided, they're combined:

```
stdin content + query argument → combined input
```

The stdin content is treated as context/data, and the query argument provides the instruction.

```bash
# stdin = context, query = instruction
cat code.py | kollab "find bugs" -p
# Result: "Here are the bugs in [code.py content]..."
```

## CLI Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `-p, --pipe` | Enable pipe mode | Off |
| `--timeout` | Timeout duration | varies by provider |

note: there is no global default of 2min. timeout comes from the profile
      (defaults to 0 = no timeout in LLMProfile).

## Exit Behavior

Pipe mode exits after:
1. LLM response is complete
2. Timeout is reached
3. Error occurs

Exit code 0 on success, non-zero on errors.

## Configuration

No special configuration needed. Pipe mode is automatically activated when:
- `-p/--pipe` flag is used, OR
- stdin is not a tty (redirected/piped)

## Examples

### Commit Message Generation

```bash
git diff | kollab "write a concise commit message" -p
```

### Code Review

```bash
git diff main | kollab "review this diff for issues" -p --timeout 5min
```

### Documentation

```bash
cat script.sh | kollab "write documentation for this script" -p
```

### Automation

```bash
# In a script
#!/bin/bash
output=$(cat "$1" | kollab "summarize" -p)
echo "$output" > summary.txt
```

## Implementation

Located in `kollabor/cli.py`. Pipe mode is detected in `async_main()` and processed via `app.start_pipe_mode()`.
