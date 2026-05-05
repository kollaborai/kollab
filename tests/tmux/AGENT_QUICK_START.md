# Agent Quick Start - Bash to JSON Test Conversion

## Setup

You are one of 3 agents (agent1, agent2, agent3) converting bash tests to JSON format.

## Workflow

### 1. Claim a test
```bash
cd tests/tmux
./claim_test.sh list                           # See available tests
./claim_test.sh claim test_agent_create.sh agent1   # Claim one
```

### 2. Read the bash script
```bash
cat test_agent_create.sh
```

Understand:
- What it's testing
- Steps it takes
- What it asserts

### 3. Create JSON equivalent
```bash
# Create in specs/ directory
vim specs/test_agent_create.json
```

Use template from `CONVERSION_CHECKLIST.md` or copy an existing spec.

### 4. Convert bash steps to JSON actions

**Bash patterns → JSON actions:**

```bash
# Bash
tmux send-keys "text"
# JSON
{ "action": "send_keys", "keys": "text" }

# Bash
tmux send-keys -l "char-by-char"
# JSON
{ "action": "type", "text": "char-by-char" }

# Bash
tmux send-keys "/"
tmux send-keys "help"
tmux send-keys Enter
# JSON
{ "action": "slash_command", "command": "help" }

# Bash
tmux send-keys Tab
# JSON
{ "action": "nav_mode" }

# Bash
sleep 0.5
# JSON
{ "action": "sleep", "seconds": 0.5 }

# Bash
OUTPUT=$(tmux capture-pane -p)
# JSON
{ "action": "capture" }

# Bash
if echo "$OUTPUT" | grep -q "pattern"; then PASS; fi
# JSON
{ "action": "assert_contains", "pattern": "pattern", "description": "Test passes" }
```

### 5. Test your JSON spec
```bash
./lib/test_runner.sh specs/test_agent_create.json
```

Debug with visible captures:
```bash
SHOW_CAPTURES=true ./lib/test_runner.sh specs/test_agent_create.json
```

### 6. Mark as done
```bash
./claim_test.sh done test_agent_create.sh agent1
```

### 7. Repeat
```bash
./claim_test.sh list    # Get next test
```

## JSON Schema Quick Reference

```json
{
  "name": "test-name",
  "description": "Brief description",
  "config": {
    "command": "python main.py",
    "show_captures": false,
    "app_init_sleep": 3,
    "term_width": 120,
    "term_height": 35
  },
  "steps": [
    { "action": "start_app" },
    { "action": "section", "title": "Section name" },
    { "action": "slash_command", "command": "help" },
    { "action": "sleep", "seconds": 0.5 },
    { "action": "capture" },
    { "action": "assert_contains", "pattern": "text", "description": "What we check" }
  ]
}
```

## Common Actions

| Action | Parameters | Example |
|--------|------------|---------|
| start_app | command (opt) | `{ "action": "start_app" }` |
| send_keys | keys | `{ "action": "send_keys", "keys": "hello" }` |
| type | text, delay | `{ "action": "type", "text": "hello" }` |
| slash_command | command, subcommand | `{ "action": "slash_command", "command": "help" }` |
| capture | show (opt) | `{ "action": "capture" }` |
| assert_contains | pattern, description | `{ "action": "assert_contains", "pattern": "text", "description": "Test" }` |
| sleep | seconds | `{ "action": "sleep", "seconds": 1 }` |
| section | title | `{ "action": "section", "title": "Test 1" }` |

## Tips

1. **Use flexible patterns**: `"pattern": "text|alternative|option"`
2. **Add sleeps**: After commands that trigger animations
3. **Sections**: Organize tests with section headers
4. **Descriptions**: Make assert descriptions clear
5. **Test both**: Success and error cases when possible

## Priority Order

Start with HIGH priority tests first (see `CONVERSION_CHECKLIST.md`).

## Help

- See existing JSON specs in `specs/` for examples
- Check `README.md` for complete documentation
- Test runner docs: `lib/test_runner.sh` header comments
