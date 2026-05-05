# Status Layouts

Custom status widget layouts for Kollab.

## Layout Format

Layouts define up to 6 rows of status widgets. Each row can be hidden/shown and contains widgets with specific widths.

## Structure

```json
{
    "version": 1,
    "description": "Layout description",
    "rows": [
        {
            "id": 1,
            "visible": true,
            "widgets": [
                {"id": "widget-name", "width": {"type": "auto"}},
                {"id": "another-widget", "width": {"type": "fixed", "value": 20}}
            ]
        }
    ]
}
```

## Width Types

- `auto`: Widget takes its natural width
- `fixed`: Widget takes exact character width: `{"type": "fixed", "value": 20}`

## Available Core Widgets

| ID | Description |
|----|-------------|
| `cwd` | Current working directory |
| `profile` | Active LLM profile |
| `model` | Current model name |
| `status` | LLM status (ready/thinking/error) |
| `stats` | Session statistics |
| `session` | Session time |
| `agent` | Active agent |
| `skills` | Active skills |
| `tasks` | Background tasks |
| `tmux` | Tmux session info |
| `bg-tasks` | Background task count |

## Custom Widgets

Any script widget in `~/.kollab/status-widgets/` can be used by its `widget-id`.

## Applying Layouts

Copy a layout JSON to `~/.kollab/layouts/` and it will appear in the layout picker (when implemented).

Or manually edit `status_layout` in `~/.kollab/config.json`.
