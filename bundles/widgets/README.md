# Status Widgets

Auto-discovered executable widgets for Kollab.

## Installed Widgets

| Widget | Description | Refresh | Interactive |
|--------|-------------|---------|-------------|
| `last-commit` | Most recent commit message | 30s | No |
| `files-changed` | Modified/added/deleted counts | 10s | No |
| `branch-status` | Branch name with ahead/behind | 30s | No |
| `uptime` | System uptime since boot | 60s | No |
| `session-time` | Current session duration | 30s | No |
| `clock` | Current time (HH:MM:SS) | 1s | No |
| `date-widget` | Current date | 60s | No |
| `weather` | Weather from wttr.in | 5m | No |
| `motivation` | Encouraging messages | 30s | No |
| `pomodoro` | Pomodoro timer | 1s | **Yes (toggle)** |
| `life-bar` | Zelda-style hearts | 10s | **Yes (action)** |

## Creating New Widgets

Create an executable script in this directory with metadata comments:

```bash
#!/usr/bin/env bash
# @widget-id: my-widget
# @name: My Widget
# @description: What it does
# @category: custom
# @refresh: 30s
# @color: true

echo "widget output"
```

Then make it executable:
```bash
chmod +x ~/.kollab/status-widgets/my-widget.sh
```

## Metadata Fields

| Field | Values | Description |
|-------|--------|-------------|
| `@widget-id` | string | Unique identifier (required) |
| `@name` | string | Display name |
| `@description` | string | Brief description |
| `@category` | git, system, time, api, fun, custom | Widget category |
| `@refresh` | 5s, 30s, 1m, manual | Refresh interval |
| `@hooks` | post_command, pre_llm | Events that trigger refresh |
| `@interactive` | true, false | Widget is clickable |
| `@interaction-type` | toggle, action, modal, inline_edit | Interaction type |
| `@on-activate` | /path/to/script | Script to run on click |
| `@timeout` | number | Max execution time (seconds) |
| `@color` | true, false | Allow ANSI color codes |
| `@min-width` | number | Minimum width (characters) |

## Examples

### Simple Git Widget
```bash
#!/usr/bin/env bash
# @widget-id: git-branch
# @name: Git Branch
# @category: git
# @refresh: 30s

git rev-parse --abbrev-ref HEAD
```

### Interactive Toggle
```bash
#!/usr/bin/env bash
# @widget-id: my-toggle
# @name: My Toggle
# @category: custom
# @refresh: 5s
# @interactive: true
# @interaction-type: toggle
# @on-activate: ~/.kollab/status-widgets/my-toggle-action.sh

STATE="$HOME/.kollab/state/my-toggle"
if [ -f "$STATE" ]; then cat "$STATE"; else echo "OFF"; fi
```

Action script (`my-toggle-action.sh`):
```bash
#!/usr/bin/env bash
STATE="$HOME/.kollab/state/my-toggle"
if [ "$(cat "$STATE" 2>/dev/null)" = "ON" ]; then
    echo "OFF" > "$STATE"
else
    echo "ON" > "$STATE"
fi
```
