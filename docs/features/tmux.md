---
title: "Tmux Integration"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Tmux Integration

The tmux plugin lets you manage terminal sessions directly from Kollab without leaving the chat interface.

## Requirements

- [tmux](https://github.com/tmux/tmux) must be installed
- Automatically detected at startup; if not found, the `/terminal` command won't be available

## Commands

Use `/terminal` (aliases: `/tmux`, `/term`, `/t`):

```
/terminal new <name> <command>    Create a new session running a command
/terminal view [name]             Live view of session output (fullscreen)
/terminal list                    List all active sessions
/terminal kill <name>             Kill a session
```

## Creating Sessions

```
/terminal new dev-server python -m http.server 8080
/terminal new tests pytest -x --tb=short
/terminal new logs tail -f /var/log/app.log
```

Sessions run in detached tmux windows. They persist even if you exit Kollab.

## Live Viewing

```
/terminal view dev-server
```

Opens a fullscreen modal showing the session output in real-time. The view auto-scrolls and refreshes. Press `q` or `ESC` to exit back to the chat.

If you run `/terminal view` without a name, it shows the most recent session.

## Listing Sessions

```
/terminal list
```

Shows all managed sessions with their status, creation time, and the command they're running.

## Killing Sessions

```
/terminal kill dev-server
```

Terminates the tmux session and removes it from the managed list.

## Attaching

```
/terminal attach dev-server
```


## Status Widget

When sessions are active, the tmux plugin shows a status indicator in the terminal status bar showing the count of running sessions.

## How It Works

The plugin uses a dedicated tmux socket (`kollab-tmux`) to avoid conflicts with your personal tmux sessions. All sessions created through Kollab are isolated from your regular tmux setup.

Sessions are tracked in memory during the Kollab process. If you restart Kollab, previously created sessions still exist in tmux but won't appear in `/terminal list` until you interact with them.
