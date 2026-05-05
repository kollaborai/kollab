# Kollab Tmux Test Framework

Automated UI testing using tmux to simulate user interactions with the terminal application.

## Quick Start

```bash
# Run entire test suite
./run_all_tests.sh

# Run with visible captures
SHOW_CAPTURES=true ./run_all_tests.sh

# Run single test
./lib/test_runner.sh specs/mcp_integration.json

# Run single test with visible captures
SHOW_CAPTURES=true ./lib/test_runner.sh specs/navigation_mode.json

# Run installed Docker runtime UI smoke
./runtime_docker_ui_smoke.sh

# Run a safe subset of the Docker runtime UI smoke
KOLLAB_RUNTIME_SMOKE_CASES=clean,login-flow ./runtime_docker_ui_smoke.sh
```

## Directory Structure

```
tests/tmux/
├── run_all_tests.sh        # Run entire test suite
├── lib/
│   ├── test_runner.sh      # JSON-driven test executor
│   └── test_helpers.sh     # Bash helper library for custom scripts
├── runtime_docker_ui_smoke.sh # Docker runtime UI smoke driver
├── specs/
│   ├── mcp_integration.json
│   ├── navigation_mode.json
│   ├── paste_detection.json
│   └── input_control.json
├── templates/
│   └── spec_verification_template.sh
└── test_*.sh               # Legacy bash test scripts
```

## Docker Runtime UI Smoke

`runtime_docker_ui_smoke.sh` drives the installed Docker image through tmux
instead of running the source-tree dev entrypoint. It covers first-run provider
errors, `/login openai`, cached OpenAI OAuth, OpenAI env auto-detection, optional
Z.AI Anthropic-compatible profiles, native tool calls, XML tool calls, and hub
messaging.

It is not part of `run_all_tests.sh` because it needs Docker and may use live
provider credentials. It skips credentialed cases when the needed token is not
available.

Useful environment variables:

```bash
KOLLAB_DOCKER_IMAGE=kollab:local-runtime
KOLLAB_RUNTIME_SMOKE_CASES=clean,login-flow,tools
KOLLAB_RUNTIME_SMOKE_KEEP=1
KOLLAB_RUNTIME_SMOKE_OAUTH_DIR="$HOME/.kollab/oauth"
ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic
ANTHROPIC_DEFAULT_OPUS_MODEL=glm-5.1
ANTHROPIC_AUTH_TOKEN=...
```

The script redacts provider tokens and OAuth device codes from captured output.

## JSON Test Schema

Tests are defined in JSON files with a simple schema:

```json
{
  "name": "test-name",
  "description": "What this test verifies",
  "config": {
    "command": "python main.py",
    "app_init_sleep": 3,
    "term_width": 120,
    "term_height": 35,
    "key_delay": 0.3,
    "menu_delay": 0.5
  },
  "steps": [
    { "action": "start_app" },
    { "action": "slash_command", "command": "mcp", "subcommand": "show" },
    { "action": "capture" },
    { "action": "assert_contains", "pattern": "MCP", "description": "MCP visible" }
  ]
}
```

### Config Options

| Option | Default | Description |
|--------|---------|-------------|
| `command` | `python main.py` | Command to start the application |
| `show_captures` | `false` | Show all capture output (ENV `SHOW_CAPTURES` trumps all) |
| `app_init_sleep` | 3 | Seconds to wait for app to initialize |
| `term_width` | 120 | Terminal width in columns |
| `term_height` | 35 | Terminal height in rows |
| `key_delay` | 0.3 | Delay after sending keys (seconds) |
| `menu_delay` | 0.5 | Delay for menu animations (seconds) |

### Available Actions

#### Application Control

| Action | Parameters | Description |
|--------|------------|-------------|
| `start_app` | `command` (optional) | Start the application. Uses `config.command` or defaults to `python main.py` |

#### Input Actions

| Action | Parameters | Description |
|--------|------------|-------------|
| `send_keys` | `keys` | Send raw keys to tmux (bulk - triggers paste detection) |
| `type` | `text`, `delay` (optional, default 0.05) | Type char-by-char (no paste detection) |
| `slash_command` | `command`, `subcommand` (optional) | Execute a slash command with filtering |
| `control` | `key` | Send Ctrl+key (e.g., `"key": "c"` for Ctrl+C) |
| `clear_input` | - | Clear input line (Ctrl+U) |
| `nav_mode` | - | Press Tab to enter navigation mode |
| `edit_mode` | - | Press 'e' to enter edit mode |
| `escape` | - | Press Escape |
| `enter` | - | Press Enter |
| `arrow` | `direction` (Up/Down/Left/Right) | Press arrow key |

#### Capture & Assert

| Action | Parameters | Description |
|--------|------------|-------------|
| `capture` | `show` (optional, default false) | Capture current screen to `LAST_OUTPUT`. Set `show: true` to print output. |
| `assert_contains` | `pattern`, `description` | Assert captured output contains regex pattern |
| `assert_not_contains` | `pattern`, `description` | Assert captured output does NOT contain pattern |

#### Flow Control

| Action | Parameters | Description |
|--------|------------|-------------|
| `sleep` | `seconds` | Wait for specified time |
| `section` | `title` | Print section header (organizational) |
| `info` | `message` | Print info message (no pass/fail) |

## Paste Detection vs Typing

Kollab-cli detects pasted content and shows a summary instead of raw text. The test framework supports both scenarios:

```json
// Bulk send - triggers paste detection
{ "action": "send_keys", "keys": "pasted content" }
// Result: [Pasted #1 1 lines, 14 chars]

// Char-by-char - shows literal text
{ "action": "type", "text": "typed content" }
// Result: typed content
```

Use `send_keys` to test paste functionality, `type` to test normal input.

## Control Keys

Send control sequences with the `control` action:

```json
{ "action": "control", "key": "u" }   // Ctrl+U - clear line
{ "action": "control", "key": "c" }   // Ctrl+C - cancel
{ "action": "control", "key": "l" }   // Ctrl+L - clear screen
{ "action": "clear_input" }           // Shorthand for Ctrl+U
```

## Slash Command Filtering

The test runner supports subcommand filtering with space-separated syntax:

```json
{ "action": "slash_command", "command": "mcp", "subcommand": "show" }
```

This types `/mcp show` which filters the menu to show only matching subcommands, then presses Enter.

## Parallel Safety

Each test run uses dynamic socket and session names based on the process ID:

```bash
SOCKET_NAME="kollabor-$$"      # e.g., "kollabor-12345"
SESSION_NAME="test-name-$$"    # e.g., "test-mcp-12345"
```

This allows multiple tests to run simultaneously without conflicts:

```bash
# Safe to run in parallel
./tests/tmux/lib/test_runner.sh ./tests/tmux/specs/mcp_integration.json &
./lib/test_runner.sh specs/navigation_mode.json &
wait
```

## Writing Tests

### Example: Testing a Slash Command

```json
{
  "name": "profile-command",
  "description": "Verify /profile command works",
  "config": {
    "app_init_sleep": 3
  },
  "steps": [
    { "action": "start_app" },

    { "action": "section", "title": "Test /profile list" },
    { "action": "slash_command", "command": "profile", "subcommand": "list" },
    { "action": "sleep", "seconds": 1 },
    { "action": "capture" },
    { "action": "assert_contains", "pattern": "profile|default|API", "description": "Profile list displays" }
  ]
}
```

### Example: Testing Navigation Mode

```json
{
  "name": "nav-mode-basic",
  "description": "Test navigation mode entry and exit",
  "steps": [
    { "action": "start_app" },

    { "action": "nav_mode" },
    { "action": "capture" },
    { "action": "assert_contains", "pattern": "NAVIGATE", "description": "Nav mode indicator shown" },

    { "action": "escape" },
    { "action": "capture" },
    { "action": "assert_not_contains", "pattern": "NAVIGATE", "description": "Nav mode exited" }
  ]
}
```

## Regex Patterns

Assertions use extended regex (`grep -iE`). Common patterns:

```
"MCP|server|tool"           # Match any of these words (case-insensitive)
"not available"             # Match exact phrase
"Error.*failed"             # Match "Error" followed by "failed"
"\\[PASS\\]"                # Match literal brackets (escape with \\)
```

## Bash Helper Library

For complex tests that need custom logic, use `test_helpers.sh`:

```bash
#!/bin/bash
source "$(dirname "$0")/lib/test_helpers.sh"

init_test "my-custom-test"
start_app

send_keys "/"
send_keys "help"
send_keys Enter
sleep 0.5

OUTPUT=$(capture)
assert_contains "$OUTPUT" "Available commands" "Help displayed"

finish_test
```

### Helper Functions

| Function | Description |
|----------|-------------|
| `init_test "name"` | Initialize test with dynamic socket/session |
| `start_app ["cmd"]` | Start application |
| `send_keys "keys"` | Send keys with default delay |
| `send_keys_delay 0.5 "keys"` | Send keys with custom delay |
| `send_slash_command "cmd" "sub"` | Execute slash command |
| `enter_nav_mode` | Press Tab |
| `exit_nav_mode` | Press Escape |
| `capture` | Capture screen (returns output) |
| `capture_tail 10` | Capture last N lines |
| `assert_contains "$out" "pat" "desc"` | Assert pattern present |
| `assert_not_contains "$out" "pat" "desc"` | Assert pattern absent |
| `wait_for "pattern" 5` | Wait up to N seconds for pattern |
| `section "title"` | Print section header |
| `info "message"` | Print info message |
| `finish_test` | Print summary and exit |

## Debugging Failed Tests

1. Increase `app_init_sleep` if app doesn't start in time
2. Add `sleep` actions between steps if timing issues
3. Check the "Got (last 15 lines)" output in failures
4. Run manually to observe behavior:
   ```bash
   tmux -L test-debug new-session -s debug "python main.py"
   # In another terminal:
   tmux -L test-debug attach -t debug
   ```

## Test Suite Runner

The `run_all_tests.sh` script runs all JSON specs in `specs/` directory:

```bash
# Run all tests
./run_all_tests.sh

# Force show all captures (overrides JSON config)
SHOW_CAPTURES=true ./run_all_tests.sh

# Force hide all captures
SHOW_CAPTURES=false ./run_all_tests.sh
```

Features:
- Automatically finds all `*.json` files in `specs/`
- Runs each spec in order
- Color-coded output (green pass, red fail)
- Summary report with total/passed/failed counts
- Exit code 0 if all pass, 1 if any fail

## Exit Codes

- `0` - All tests passed
- `1` - One or more tests failed

## Best Practices

1. Use descriptive test names and descriptions
2. Add `section` actions to organize test output
3. Always `capture` before `assert_*`
4. Use `sleep` after commands that trigger animations
5. Keep patterns flexible with `|` alternatives for different states
6. Test both success and error cases
