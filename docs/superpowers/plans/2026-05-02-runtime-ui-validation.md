# Kollab Runtime UI Validation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the installed Kollab works for a fresh user through the real terminal UI, then isolate failures in login, profile persistence, native tool calling, XML tool calling, and hub workflows.

**Architecture:** Validate behavior from the same surfaces a user touches: Docker installed runtime, tmux-attached TUI, slash commands, profile modals, permission prompts, saved logs, and raw conversation JSONL. Treat XML tags and native tool calls as two current tool-expression protocols until evidence shows one should be removed.

**Tech Stack:** Python 3.12, Kollab, Docker `kollab:local-runtime`, tmux, OpenAI API key profile, OpenAI OAuth `/login`, Z.ai Anthropic-compatible profile, hub presence/socket files under `~/.kollab/hub`.

---

## Operating Rules

- Use UI-first evidence: tmux `send-keys`, `capture-pane`, slash commands, modal navigation, permission prompts, status bar, and app logs.
- Do not judge success from a short assistant reply alone. Confirm provider/model/tool path from raw JSONL and logs.
- Keep secrets out of the repo. Tokens are supplied from the host shell or a throwaway local Docker volume/env file outside the checkout.
- Redact tokens in captures and logs before sharing.
- Use a fresh Docker volume for each major scenario unless the scenario is explicitly testing restart persistence.
- Do not patch code until a scenario has a reproducible failure and a root-cause note.

## Secret Inputs

Use these local-only inputs:

- Host `OPENAI_API_KEY` for OpenAI API-key profile tests.
- Host `~/.kollab/oauth/openai.json` for existing OAuth-token tests.
- Manual `/login openai` device flow for fresh-login tests.
- Z.ai Anthropic-compatible token only in throwaway runtime state for cross-provider smoke tests.

Never commit raw values. If a local env file is needed, create it outside the repo:

```bash
mkdir -p "$HOME/.config/kollab"
chmod 700 "$HOME/.config/kollab"
touch "$HOME/.config/kollab/ui-test.env"
chmod 600 "$HOME/.config/kollab/ui-test.env"
```

## Harness Conventions

Use deterministic names so cleanup is obvious:

```bash
export KOLLAB_UI_IMAGE=kollab:local-runtime
export KOLLAB_UI_WORKSPACE=/Users/example/dev/kollab
export KOLLAB_UI_SESSION=kollab-ui-test
export KOLLAB_UI_CONTAINER=kollab-ui-test
export KOLLAB_UI_HOME_VOLUME=kollab-ui-home
```

Send slash commands with the known timing guard:

```bash
tmux send-keys -t "$KOLLAB_UI_SESSION" /
sleep 0.7
# then type the remaining command one character at a time
```

Capture evidence with token redaction:

```bash
tmux capture-pane -t "$KOLLAB_UI_SESSION" -p -S -180 \
  | perl -pe 's/[[:xdigit:]]{32}\.[A-Za-z0-9]+/[TOKEN]/g; s/(api[_-]?key[=": ]+)[^,}\s]+/${1}[REDACTED]/ig'
```

## Scenario Matrix

### Task 1: Fresh Installed Runtime Starts

**Files:**
- Read: `Dockerfile`
- Read: `scripts/docker-runtime.sh`
- Read: `docs/guides/docker-runtime.md`
- Evidence: Docker image, tmux capture, container logs

- [ ] **Step 1: Build or verify image**

Run:

```bash
docker image inspect kollab:local-runtime >/dev/null || scripts/docker-runtime.sh build
```

Expected: image exists and no daemon errors.

- [ ] **Step 2: Start clean UI runtime**

Run:

```bash
docker volume rm kollab-ui-home >/dev/null 2>&1 || true
docker volume create kollab-ui-home
tmux new-session -d -s kollab-ui-test -c /Users/example/dev/kollab \
  "docker run --rm -it \
    --name kollab-ui-test \
    --mount type=volume,src=kollab-ui-home,dst=/home/kollab/.kollab \
    --mount type=bind,src=/Users/example/dev/kollab,dst=/workspace \
    --workdir /workspace \
    --env TERM=xterm-256color \
    kollab:local-runtime \
    kollab --no-daemon"
```

Expected UI evidence: banner appears, prompt is ready, status bar shows cwd `/workspace`.

- [ ] **Step 3: Verify startup logs**

Run:

```bash
docker exec kollab-ui-test sh -lc 'tail -120 /home/kollab/.kollab/projects/workspace/logs/kollab.log'
```

Expected: app startup completes, no traceback, profile fallback is understandable.

### Task 2: OpenAI API-Key Launch

**Files:**
- Read: `packages/kollabor-ai/src/kollabor_ai/profile_manager.py`
- Read: `kollabor/commands/system_commands/handlers/profile.py`
- Evidence: active profile status, provider init log, raw conversation JSONL

- [ ] **Step 1: Launch with host OpenAI API key**

Run:

```bash
test -n "${OPENAI_API_KEY:-}"
tmux kill-session -t kollab-ui-test 2>/dev/null || true
tmux new-session -d -s kollab-ui-test -c /Users/example/dev/kollab \
  "docker run --rm -it \
    --name kollab-ui-test \
    --mount type=volume,src=kollab-ui-home,dst=/home/kollab/.kollab \
    --mount type=bind,src=/Users/example/dev/kollab,dst=/workspace \
    --workdir /workspace \
    --env TERM=xterm-256color \
    --env OPENAI_API_KEY \
    kollab:local-runtime \
    kollab --profile openai-auto --no-daemon"
```

Expected: no missing `api_key` error. If `openai-auto` is not registered, inspect whether env auto-detection skipped registration.

- [ ] **Step 2: Send a UI prompt**

Send:

```text
Reply exactly: openai api key path works.
```

Expected UI evidence: assistant replies exactly. Expected log evidence: provider `openai`, model from profile, request success.

- [ ] **Step 3: Confirm raw metadata**

Run:

```bash
docker exec kollab-ui-test sh -lc 'tail -1 /home/kollab/.kollab/projects/workspace/conversations/raw/*_raw.jsonl'
```

Expected: JSONL line has `provider` matching OpenAI API profile, not `openai_responses` OAuth.

### Task 3: Fresh `/login openai` Flow

**Files:**
- Read: `kollabor/commands/system_commands/handlers/login.py`
- Read: `packages/kollabor-ai/src/kollabor_ai/oauth/token_storage.py`
- Read: `packages/kollabor-ai/src/kollabor_ai/oauth/openai_oauth.py`
- Evidence: device-code UI, stored token file, `/login status`, restart persistence

- [ ] **Step 1: Start with no existing OAuth token**

Run:

```bash
docker volume rm kollab-login-home >/dev/null 2>&1 || true
docker volume create kollab-login-home
tmux new-session -d -s kollab-login-test -c /Users/example/dev/kollab \
  "docker run --rm -it \
    --name kollab-login-test \
    --mount type=volume,src=kollab-login-home,dst=/home/kollab/.kollab \
    --mount type=bind,src=/Users/example/dev/kollab,dst=/workspace \
    --workdir /workspace \
    --env TERM=xterm-256color \
    kollab:local-runtime \
    kollab --no-daemon"
```

Expected: `/login status` reports OpenAI not authenticated.

- [ ] **Step 2: Run `/login openai` through UI**

Use slash timing and send:

```text
/login openai
```

Expected: device/browser flow appears with clear instructions. User can complete auth without reading source docs.

- [ ] **Step 3: Verify token cache after login**

Run:

```bash
docker exec kollab-login-test sh -lc 'test -f /home/kollab/.kollab/oauth/openai.json && stat -c "%a %n" /home/kollab/.kollab/oauth/openai.json'
```

Expected: token exists, permissions are owner-only. UI `/login status` reports authenticated with expiry.

- [ ] **Step 4: Restart and verify cached login**

Kill and restart `kollab-login-test` with the same Docker volume.

Expected: `openai-oauth` auto-registers, `/login status` still authenticated, prompt succeeds without redoing browser login.

### Task 4: Existing OAuth Token Mount

**Files:**
- Read: `docs/features/profiles.md`
- Read: `packages/kollabor-ai/src/kollabor_ai/profile_manager.py`
- Evidence: mounted token, profile auto-registration, prompt request metadata

- [ ] **Step 1: Mount host OAuth token read-only**

Run:

```bash
test -f "$HOME/.kollab/oauth/openai.json"
tmux new-session -d -s kollab-oauth-mounted -c /Users/example/dev/kollab \
  "docker run --rm -it \
    --name kollab-oauth-mounted \
    --mount type=volume,src=kollab-oauth-home,dst=/home/kollab/.kollab \
    --mount type=bind,src=$HOME/.kollab/oauth/openai.json,dst=/home/kollab/.kollab/oauth/openai.json,readonly \
    --mount type=bind,src=/Users/example/dev/kollab,dst=/workspace \
    --workdir /workspace \
    --env TERM=xterm-256color \
    kollab:local-runtime \
    kollab --profile openai-oauth --no-daemon"
```

Expected: status bar shows `openai-oauth`, `/login status` says authenticated.

- [ ] **Step 2: Confirm provider is OAuth endpoint**

Send a short prompt and inspect raw JSONL.

Expected: provider `openai_responses`, endpoint `chatgpt.com/backend-api/codex`, model resolved from OAuth path.

### Task 5: Native Tool Calling

**Files:**
- Read: `kollabor/llm/llm_coordinator.py`
- Read: `packages/kollabor-agent/src/kollabor_agent/native_tools_handler.py`
- Read: `packages/kollabor-agent/src/kollabor_agent/tool_executor.py`
- Evidence: native tools loaded, permission prompt, tool execution, raw `tool_calls`

- [ ] **Step 1: Start with a profile where tools are enabled**

Use a working provider profile whose status confirmation says `Tools: enabled`.

Expected log line:

```text
Loaded ... tools for native API calling
```

- [ ] **Step 2: Prompt the model to use a file tool**

Send:

```text
Use a tool to read README.md, then answer with the first heading only.
```

Expected UI evidence: permission prompt for file read appears. Approve only the specific file-read action.

- [ ] **Step 3: Verify native path**

Run:

```bash
docker exec <container> sh -lc 'tail -5 /home/kollab/.kollab/projects/workspace/conversations/raw/*_raw.jsonl'
```

Expected: saved turn contains native `tool_calls` or native tool-result messages. If no native tool call appears and XML text appears instead, file a failure: native tool mode did not steer the model.

### Task 6: XML Tool Calling

**Files:**
- Read: `packages/kollabor-ai/src/kollabor_ai/response_parser.py`
- Read: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`
- Read: `bundles/agents/_base/sections/tool-reference/`
- Evidence: XML tag emitted, parser dispatch, same executor backend

- [ ] **Step 1: Create or switch to tools-disabled profile**

Use `/profile edit` or a temporary profile with `supports_tools=false`.

Expected: status confirmation says `Tools: disabled`.

- [ ] **Step 2: Prompt XML tool use**

Send:

```text
Use the XML read tool to read README.md, then answer with the first heading only.
```

Expected UI evidence: assistant emits XML tool request, parser triggers permission prompt, file read executes.

- [ ] **Step 3: Verify XML path**

Expected logs: `ResponseParser` or `ToolExecutor` dispatch for parsed XML tool. Expected raw conversation: assistant content includes XML tag and subsequent tool-result content.

Decision gate: Do not remove XML unless this test is replaced by an equivalent native-only flow for every tool family the app still advertises.

### Task 7: Tool Scope and Permission Safety

**Files:**
- Read: `packages/kollabor-agent/src/kollabor_agent/permissions/`
- Read: `packages/kollabor-agent/src/kollabor_agent/tool_executor.py`
- Evidence: risk levels, approve/deny/session choices, blocked unsafe path

- [ ] **Step 1: File read low/medium risk path**

Prompt a README read. Approve once. Expected: tool result is shown without leaking too much raw output in display.

- [ ] **Step 2: Deny a terminal command**

Prompt:

```text
Use a terminal command to run git status and then summarize it.
```

Press deny. Expected: assistant receives denial and does not execute command.

- [ ] **Step 3: Session approval**

Repeat `git status`, choose session approval. Expected: next same command does not prompt again during this session, but does after restart.

### Task 8: Hub Single-Agent Basics

**Files:**
- Read: `docs/guides/hub-quick-start.md`
- Read: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`
- Read: `plugins/agent_orchestrator/plugin.py`
- Evidence: coordinator status, whoami, presence file, hub status UI

- [ ] **Step 1: Start one agent**

Run app in tmux and send:

```text
/hub status
```

Expected: one coordinator, no peers, stable designation in status bar.

- [ ] **Step 2: Verify presence files**

Run:

```bash
docker exec <container> sh -lc 'find /home/kollab/.kollab/hub -maxdepth 3 -type f | sort'
```

Expected: presence/state/vault files exist and match UI designation.

### Task 9: Hub Multi-Agent Messaging

**Files:**
- Read: `docs/guides/hub-quick-start.md`
- Read: `docs/specs/hub-message-flow.md`
- Evidence: two tmux sessions, peer roster, message delivery, capture

- [ ] **Step 1: Launch two agents in same home volume**

Use separate tmux sessions and container names, same Docker home volume.

Expected: one coordinator, two online agents, distinct designations.

- [ ] **Step 2: Send UI message**

From agent A:

```text
/hub msg <agent-b-designation> reply with exactly hub message received
```

Expected: agent B receives injected message and responds.

- [ ] **Step 3: Capture from UI and CLI**

Run:

```text
/hub capture <agent-b-designation> 50
```

Expected: capture shows the message and response. CLI `kollab --hub capture` should agree.

### Task 10: Hub Spawn and Stop

**Files:**
- Read: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`
- Read: `plugins/agent_orchestrator/plugin.py`
- Evidence: spawn request, new agent online, task receipt, graceful stop

- [ ] **Step 1: Spawn from UI**

Send:

```text
/hub spawn coder "reply with exactly spawned ok, then wait"
```

Expected: new agent appears in `/hub agents` and reports back.

- [ ] **Step 2: Stop spawned agent**

Send:

```text
/hub stop <spawned-designation>
```

Expected: process exits, presence disappears, hub status updates.

### Task 11: Restart Persistence

**Files:**
- Read: `docs/features/profiles.md`
- Read: `docs/guides/hub-quick-start.md`
- Evidence: active profile, cached OAuth token, hub vault/presence cleanup, conversation persistence

- [ ] **Step 1: Restart same Docker volume**

Kill tmux/container and restart with same home volume.

Expected: active profile restored if persisted, OAuth token still valid, previous conversation available, stale presence cleaned up or ignored.

- [ ] **Step 2: Restart fresh Docker volume**

Start with a new home volume.

Expected: no leaked profiles/tokens, onboarding/login path is clear.

## Failure Report Template

For each failure, record:

```markdown
### Failure: <short name>

**Scenario:** Task N Step M
**Exact UI steps:** ...
**Expected:** ...
**Actual:** ...
**Evidence:**
- tmux capture:
- log path:
- raw JSONL path:
**Suspected layer:** UI input / profile manager / provider / parser / tool executor / permissions / hub
**Root-cause status:** unknown / confirmed
**Next diagnostic:** ...
```

## Initial Architectural Decision Gate

Do not remove XML tool calling yet.

Current docs describe XML and native as two supported protocols over one executor. The first decision is not “which feels cleaner,” it is:

1. Which protocol actually works for OpenAI API-key profiles?
2. Which protocol actually works for OpenAI OAuth profiles?
3. Which protocol actually works for Anthropic-compatible profiles?
4. Which protocol supports hub tools reliably?
5. Which protocol gives better UI permission behavior?
6. Which protocol gives inspectable raw evidence without hiding failures?

Only after Tasks 5-10 have evidence should the design move to one of:

- Keep both, but make mode selection explicit and documented.
- Make native the default and keep XML as compatibility/fallback.
- Remove XML from agent prompts and parser only after all XML-only tools have native equivalents and hub workflows pass.

## Execution Order

Run in this order:

1. Task 1 fresh runtime
2. Task 2 OpenAI API key
3. Task 3 fresh `/login openai`
4. Task 4 existing OAuth mount
5. Task 5 native tools
6. Task 6 XML tools
7. Task 7 permissions
8. Task 8 hub single-agent
9. Task 9 hub messaging
10. Task 10 hub spawn/stop
11. Task 11 restart persistence

Stop and write a failure report as soon as a scenario fails reproducibly.
