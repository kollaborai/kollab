---
title: "Learn Kollab"
doc_type: feature
created: 2026-10-20
modified: 2026-10-20
status: active
---
# Learn Kollab

The browser UI has a Learn book icon in the sidebar footer for people who are new
to Kollab or want a quick reminder. Each lesson uses plain language, a short how-to,
and an example. Topic URLs are stable and use `/learn?topic=<id>`.

Learn uses borderless tonal surfaces for its navigation, lessons, release notes,
completion controls, and callouts. Interactive controls retain keyboard focus
indicators. Titles, body copy, and detail text use the same size scale as the
Settings and Agents panels across desktop and mobile.

The topic menu uses plain text labels without numbering. Page and section headings
omit decorative icons, taglines, and lesson counters; completion remains visible
through the progress bar and completed state.

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
| `phone-access` | PhoneLink |

Use the Learn icon to browse lessons, or a lesson link from a related workspace
or profile control to open the matching topic. Progress is saved only in the current
browser's local storage; it is not an account or cross-device setting. If browser
storage is blocked, lessons remain usable and the page explains that completion
cannot be saved.

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

Drag an agent row to reorder it within its workspace; the other rows move aside
before you drop. Keyboard users can pick up a focused row with Space, move it with
the arrow keys, and press Space to save or Escape to cancel.
Drag past the sidebar's edge and release on the "Release to hide" cue to hide
an agent. It shrinks into the Hidden Agents button and can be restored there. Move back inside
or press Escape to cancel; hiding never deletes or stops an agent.

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


When a Copy control is available, review the provider and base URL/endpoint shown
in the form. The copy operation retains stored fields that the form does not show,
including an ACP `command` and keyring/sign-in references. Environment-sourced and
provisioned API keys are not transferred to the copied profile; configure its own
`KOLLAB_<PROFILE>_API_KEY` or sign in separately. A profile name is a label, so
verify the endpoint before starting a session.

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

## PhoneLink

PhoneLink opens this computer's Kollab app in a phone browser through the same
connection service used by `/connect`. Open **Settings > PhoneLink**, choose
**Link a Phone**, scan with the phone's camera, compare the pairing code, and
approve on the computer. The invitation expires after five minutes and links
one phone. Bookmark the opened app to return in the same phone browser.

Keep the computer awake, online and running Kollab. Agents, provider calls and
tools still run on the computer. The phone is a trusted app controller, with no
credential setup or agent-network enrollment. Removing the phone withdraws its app access.
The directory must include the PhoneLink portal; the UI checks this before
issuing a QR. Code in a checkout does not establish public deployment.

Manual WireGuard access remains optional under **Advanced Settings**; its
operator prerequisites are in [PhoneLink](phone-access.md).

## What's New and progress

The Learn page's What's New card reads `kollabor/updates/CHANGELOG.md` from the
canonical source when the Web UI bundle is built. It shows Unreleased notes and
the newest release section; it does not keep a separate copy of release notes.
If notes are empty or cannot load, the page offers the full changelog and a
retry. Unknown topic IDs show a recovery link to the lesson list.
