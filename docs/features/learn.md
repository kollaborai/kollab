---
title: "Learn Kollab"
doc_type: feature
created: 2026-10-20
modified: 2026-10-20
status: active
---
# Learn Kollab

The browser UI has a Learn entry in the sidebar for people who are new to Kollab
or want a quick reminder. Each lesson uses plain language, a short how-to, and an
example. Topic URLs are stable and use `/learn?topic=<id>`.

| Topic ID | Lesson |
| --- | --- |
| `networks` | Kollab networks |
| `devices` | Connected devices |
| `workspaces` | Project workspaces |
| `agents` | Agents, rosters, and teams |
| `profiles` | Provider profiles |
| `koordinator` | The coordinator role |
| `hub-messaging` | Shared Hub messages |
| `voice` | Voice mode |
| `system-prompts` | Agent system prompts |
| `phone-access` | WireGuard phone access |

Use a lesson link from a related sidebar, workspace, or agent surface to open the
matching topic. Progress is saved only in the current browser's local storage; it
is not an account or cross-device setting. If browser storage is blocked, lessons
remain usable and the page explains that completion cannot be saved.

## Kollab networks and devices

A Kollab network connects trusted Kollab installs so their devices and agents can
find each other. It is separate from a VPN. Start the invite flow with `/connect`
or the Web UI Network controls, share a one-time invite only with the person who
should join, and confirm the joining device. Expand Networks in the sidebar to
browse devices, then their workspaces and agents.

Example: invite a second computer, then open its device from the sidebar to reach
an agent running there. Treat invite codes as private; do not post them publicly.

## Workspaces

A workspace is a project folder. Pick the folder before starting a session so its
sessions, local agents, and project context stay together. Use the workspace
picker in the sidebar or New Session controls, choose an available agent, and
start the session.

Example: keep the Kollab repository and a customer repository in separate
workspaces so their local agents and conversation history do not mix.

## Agents

Agents are named collaborators with instructions and optional skills. A roster
groups related agents; a team starts several roles together, lead first. Open
Agents in the sidebar, choose one specialist for a focused task, or launch a team
when the work naturally divides into roles.

Example: ask a coding agent to update a named component, add a regression test,
and report the exact test command and result.

## Provider profiles

A profile selects the provider connection, model, and reasoning effort for a
session. It does not select the agent or change that agent's prompt. Use the
profile selector or profile settings, then choose the profile before starting a
session. Keep API keys in Kollab's supported sign-in or environment flow, never
in a prompt or Hub message.

Example: keep one profile for quick everyday questions and a second profile with
higher reasoning effort for a difficult code review.

## Koordinator

The koordinator organizes shared work: it tracks assignments, asks agents for
progress, and routes tasks to the right specialist. Give it a bounded goal,
acceptance checks, and constraints; then review each result and its evidence
before accepting it. Coordination does not replace human review.

Example: ask the koordinator to split a feature into a UI task and a test task,
then report both results and the integration check.

## Hub messaging

Hub messages are visible to the Hub, not private direct messages. Address the
agent by its Hub identity rather than its general agent type, keep the request
specific, and state what evidence you want back. Never send passwords, API keys,
private keys, invite codes, or QR images through Hub chat.

Example: `hub_msg to="lapis"` followed by a short request to review a route and
report any broken links.

## Voice mode

Voice mode is explicit. Check it with `/voice status`, enter `/voice on` when you
are ready to speak, and enter `/voice off` when you are done. Opening the Learn
page never turns on the microphone.

Example: say “summarize the last answer in three steps,” then use `/voice off` to
release the microphone.

## System prompts

An agent's system prompt explains how it should work. A project agent's prompt
lives in `.kollab/agents/<agent-name>/system_prompt.md`; reusable prompt fragments
can live in that agent's `sections/` directory and be included with a supported
`<trender>` include. Skills are separate and use their own `SKILL.md` directory
contract. Reload the agent after changing its startup prompt and verify the
rendered result.

Example: add a short “verify before reporting” section and include it with
`<trender type="include" path="sections/verify.md" />`.

## WireGuard phone access

WireGuard phone access joins a phone to an administrator-prepared private VPN so
it can open the Web UI at a private address. It does not publish the Web UI.

1. Ask the host administrator to prepare a reachable WireGuard server, its public
   key and endpoint, and a private tunnel address for the phone.
2. The network owner configures any required router/firewall UDP forwarding, DNS,
   and trusted HTTPS certificate outside Kollab.
3. The host administrator keeps the Web UI private by allowing only its LAN IP
   in `KOLLAB_WEBUI_HOSTS` before launch (for example,
   `KOLLAB_WEBUI_HOSTS=192.168.1.20 kollab --web-ui`). The host also sets
   `KOLLAB_PHONE_ACCESS_APPLY_COMMAND` to a reviewed peer-reconciliation helper;
   this helper is not editable from the phone API. Without it, add/revoke controls
   remain disabled.
4. In Phone Access, add the phone and scan its one-time QR code with the official
   WireGuard app. The QR contains the phone's private key; keep it secret and do
   not save it in screenshots or share it.
5. Activate the VPN on the phone, then open the Web UI through its private
   address (for example, `http://<private-host>:8080`).

Kollab does not install WireGuard, configure routers, open firewalls, or set up
DNS. Keep the Web UI restricted to the private network; do not expose it as a
public service just to reach it from a phone.

## What's New and progress

The Learn page's What's New card reads `kollabor/updates/CHANGELOG.md` from the
canonical source when the Web UI bundle is built. It shows Unreleased notes and
the newest release section; it does not keep a separate copy of release notes.
If notes are empty or cannot load, the page offers the full changelog and a
retry. Unknown topic IDs show a recovery link to the lesson list.
