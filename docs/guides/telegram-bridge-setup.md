---
title: "Telegram Bridge Setup"
created: 2026-04-05
modified: 2026-04-05
status: active
---
# Telegram Bridge Setup

Bidirectional messaging between your kollabor agents and Telegram.
Send messages from your phone, receive agent responses, voice notes
transcribed locally via whisper.

## Prerequisites

- A Telegram account (mobile or desktop app)
- `kollab` installed and working
- For voice messages: [openai-whisper](https://github.com/openai/whisper) installed locally

## Step 1: Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Pick a display name (e.g. "Kollabor Bridge")
4. Pick a username (must end in `bot`, e.g. `kollabor_myname_bot`)
5. BotFather replies with your **bot token** -- looks like `<telegram-bot-token>`
6. Save this token. You'll need it in step 3.

**Important:** After creating the bot, open a chat with it and send any
message (e.g. "hello"). The bot needs at least one message from you
before it can send messages back.

## Step 2: Get Your Chat ID

1. Open Telegram and search for **@userinfobot**
2. Send `/start`
3. It replies with your **user ID** -- a number like `123456789`
4. This is your chat ID. Save it.

Alternative: search for **@RawDataBot**, send `/start`, and look for
the `chat.id` field in the JSON response.

## Step 3: Set Environment Variables

```bash
export KOLLAB_HUB_BRIDGE_TOKEN="<your-telegram-bot-token>"
export KOLLAB_HUB_BRIDGE_CHAT_ID=123456789
```

Add these to your shell profile (`~/.zshrc`, `~/.bashrc`) so they
persist across sessions.

## Step 4: Run Setup

```bash
kollab --agent jarvis
```

Once inside kollab, run:

```
/hub bridge setup
```

This will:
- Auto-detect the token and chat ID from env vars
- Validate the bot token via Telegram's `getMe` API
- Send a test message to your Telegram chat
- Save the config to `~/.kollab/config.json`

Check your Telegram -- you should see a test message from the bot.

## Step 5: Enable the Bridge

After setup confirms the test message was sent:

```
/hub bridge enable
```

The bridge loop starts polling for incoming messages and forwarding
agent activity to your Telegram chat.

To check the current state:

```
/hub bridge status
```

## Voice Messages

When you send a voice note on Telegram, the bridge downloads the
audio file and transcribes it locally using OpenAI's whisper CLI.

**Install whisper:**

```bash
pip install openai-whisper
```

Voice messages appear in the hub as `[voice] transcribed text here`
and are routed to agents like any other message.

The bridge uses the `base` model by default for fast transcription.
No API key or cloud service needed -- it runs entirely on your machine.

## Configuration Reference

All bridge config lives under `plugins.hub.*` and can be set via
`/config` in the TUI or directly in `~/.kollab/config.json`.

| Config Key | Default | Description |
|---|---|---|
| `plugins.hub.bridge_enabled` | `false` | Enable/disable the bridge loop |
| `plugins.hub.bridge_platform` | `"telegram"` | Platform (telegram is the only one currently) |
| `plugins.hub.bridge_token` | `""` | Bot token (or use env var) |
| `plugins.hub.bridge_chat_id` | `""` | Chat/channel ID (or use env var) |
| `plugins.hub.bridge_user_id` | `""` | Optional: restrict to specific Telegram user |
| `plugins.hub.bridge_poll_interval` | `2` | Seconds between polls for incoming messages |
| `plugins.hub.bridge_target_agent` | `""` | Route incoming messages to this agent ("" = self) |

**Environment variables** (override config values):

| Variable | Maps to |
|---|---|
| `KOLLAB_HUB_BRIDGE_TOKEN` | `plugins.hub.bridge_token` |
| `KOLLAB_HUB_BRIDGE_CHAT_ID` | `plugins.hub.bridge_chat_id` |

## Bridge Commands

| Command | Description |
|---|---|
| `/hub bridge status` | Show connection state and config |
| `/hub bridge setup` | Auto-detect config from env vars, send test message |
| `/hub bridge enable` | Start the bridge polling loop |
| `/hub bridge disable` | Stop the bridge polling loop |
| `/hub bridge send <text>` | Send a message through the bridge manually |

## What Gets Forwarded

When the bridge is enabled, your Telegram chat receives:

- Agent arrivals and departures on the hub
- Hub messages between agents (the open channel)
- LLM responses from your agent
- Task completions and status updates

Messages you send from Telegram are injected into the hub as if
you typed them in the TUI.

## Troubleshooting

### 409 Conflict Error

```
telegram poll error: 409 Conflict
```

Another process is polling the same bot token. This happens when:
- You have two kollab instances with the same bridge config
- A previous instance didn't shut down cleanly

Fix: Kill any stale kollab processes, or create a second bot for
the other instance.

### No Response from Bot

- Make sure you sent a message to the bot first (open the bot chat,
  send "hello")
- Verify the chat ID matches your user: check with @userinfobot
- Check that the token is correct: `/hub bridge setup` validates it

### Voice Transcription Fails

- Ensure `whisper` CLI is installed: `which whisper`
- Check that the `base` model is downloaded (whisper downloads it
  on first use, needs internet)
- Voice messages time out after 60 seconds of transcription

### Bridge Stops Working After Restart

The bridge config is saved to `~/.kollab/config.json` after
`/hub bridge setup`, but `bridge_enabled` defaults to `false` on
startup. Either:

- Run `/hub bridge enable` each session, or
- Set `plugins.hub.bridge_enabled` to `true` in your config file

### Messages Not Appearing in Telegram

- Run `/hub bridge status` to confirm the loop is running
- Check the kollabor log file for bridge errors:
  `~/.kollab/projects/<path>/logs/kollab.log`
- Telegram has rate limits -- if you're sending too many messages,
  some may be dropped
