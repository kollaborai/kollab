---
title: "Learn Kollab"
doc_type: feature
created: 2026-10-20
modified: 2026-11-09
status: active
---
# Learn Kollab

The browser UI has a Learn book icon in the sidebar footer for people who are new
to Kollab or want a quick reminder. It opens a compact modal like Apps, with the
chat preserved behind it. Each topic uses plain language and feature-specific,
complete workflows. Content lives in topic JSON files under
`packages/kollabor-webui/frontend/src/learn/topics/`; the shared renderer owns
navigation, links, media and layout. A workflow identifies its entry point and
prerequisites, ordered action/expected-result steps, supported commands when
applicable, a success result and actionable recovery. Its first workflow has a
canonical `/learn/<topic-id>/overview.webp` image, or that image as its video poster. Topic URLs are
stable and use `/learn?topic=<id>`. Keep the existing topic IDs when changing
titles or content.

Learn uses borderless tonal surfaces for its navigation, topics, and callouts.
Interactive controls retain keyboard focus
indicators. Titles, body copy, and detail text use the same size scale as the
Settings and Agents panels across desktop and mobile.

The topic menu uses plain text labels without numbering. Page and section headings
omit decorative icons, taglines, and lesson counters. The modal has a close icon
in its top-right corner and scrolls internally. Escape closes an enlarged product
preview first, returning to the lesson; another Escape closes Learn. Topic URLs
can be bookmarked or opened directly and browser Back/Forward restores navigation.

| Topic ID | Lesson |
| --- | --- |
| `networks` | Connect |
| `devices` | Connected devices |
| `workspaces` | Project workspaces |
| `agents` | Agents, rosters, and teams |
| `profiles` | Provider profiles |
| `context-compaction` | Conversation context compaction |
| `koordinator` | The coordinator role |
| `hub-messaging` | Shared Hub messages |
| `voice` | Voice mode |
| `system-prompts` | Agent system prompts |
| `phone-access` | PhoneLink |

Use the Learn icon to browse lessons, or open a topic's direct URL. Contextual
control help stays in its hover help. Learn is a reference with no
completion tracking, progress bar, or Previous/Next footer. Navigate with the
topic menu.

## Connect and devices

A Kollab network connects trusted Kollab installs so their devices and agents can
find each other. It is separate from a VPN. On the computer already in the network,
open Settings > Network > Access and select New Join Code. Enter it on the
joining computer, then review and approve the identity on the existing computer.
Connect status shows network devices and their agents. An approved invite sends
the inviter's sealed managed settings, including API keys and loadouts, MCP servers,
agents and skills. The joiner trusts the inviter's synced configuration, including
MCP commands and environment; inspect the inviter and review configuration before
accepting. Changes continue syncing while joined. ChatGPT OAuth sign-ins, project
folders, vaults and conversations do not transfer. Leaving keeps already-synced
values; revoking stops future updates but does not revoke a provider API key. Rotate
it with its provider if needed. A knock accepted for an agent grants no network
configuration or credential sync. Use a knock when a caller should remain outside
your network; review and allow only the agent needed. A knock is consent-based and
does not itself enroll the caller or copy network settings.

Example: invite a second computer, then open its device from the sidebar to reach
an agent running there. Treat invite codes as private; do not post them publicly.

## Workspaces

A workspace is a project folder. Pick the folder before starting a session so its
sessions, local agents, and project context stay together. Use the workspace
picker in the sidebar or New Session controls, choose an available agent, and
start the session.

Drag an agent row to reorder it within its workspace; keyboard users press Space
to pick it up, use the arrow keys, then press Space to drop or Escape to cancel.
Drag past the sidebar edge and release on "Release to hide", or choose Hide Agent
from the row actions. Restore it from Hidden Agents. Hiding never deletes or stops
an agent.

Example: keep the Kollab repository and a customer repository in separate
workspaces so their local agents and conversation history do not mix.

## Agents

Agents are named collaborators with instructions and optional skills. A roster
groups related agents; a team starts several roles together, lead first. Open
Agents in the sidebar footer to inspect rosters and teams. To start one agent,
use Session Options beside +, choose Agent, Model and Workspace, then Start Session.
Use Agents > Teams > Configure & Launch when the work divides into roles.

Example: ask a coding agent to update a named component, add a regression test,
and report the exact test command and result.

## Provider profiles

A profile selects the provider connection, model, and reasoning effort for a
session. It does not select the agent or change that agent's prompt. Use the
Session Options > Model before starting a session, or Manage Profiles to inspect
and copy a connection. Keep API keys in Kollab's supported sign-in or environment flow, never
in a prompt or Hub message.

Example: keep one profile for quick everyday questions and a second profile with
higher reasoning effort for a difficult code review.


When a Copy control is available, review the provider and base URL/endpoint shown
in the form. The copy operation retains stored fields that the form does not show,
including an ACP `command` and keyring/sign-in references. Environment-sourced and
provisioned API keys are not transferred to the copied profile; configure its own
`KOLLAB_<PROFILE>_API_KEY` or sign in separately. A profile name is a label, so
verify the endpoint before starting a session.

## Context compaction

Compaction starts automatically near the model's context limit. Use `/compact status`
or the Web UI banner to distinguish compacting, paused, failed and ready to retry.
Use `/compact preview` for message counts and an estimated token reduction, rather
than a proposed summary, before choosing
whether to compact. `/compact now` compacts immediately; `/compact` asks the
model what to keep. Compaction changes conversation history, so review the
result. If a provider rejects an oversized request, compact or start a smaller
conversation, then resend the original request yourself; Kollab does not
silently replay it.

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

Example: `kollab --hub msg reviewer "Check this route and report broken links."`.

## Voice mode

Voice mode is explicit. Check it with `/voice status`, enter `/voice on` when you
are ready to speak, and enter `/voice off` when you are done. Opening the Learn
page never turns on the microphone.

Example: say “summarize the last answer in three steps,” then use `/voice off` to
release the microphone.

Quick Model chooses Same as Chat or any configured provider's model for short
spoken asides during a running agent turn; the selected provider uses its own
connection. Typed messages still use the chat model. Dream Model selects a cheaper
model supported by the active provider for Hub background dreaming only; blank
falls back to `kollabor.llm.fast_model`, then the active session model, reusing
the active profile's endpoint and credentials. It does not change voice replies.
Choose speech and transcription engines in Settings > Voice or `/voice config`;
saving settings does not activate the microphone.

## System prompts

An agent's system prompt explains how it should work. Open Settings > Prompts (or
type `/prompts` in Web chat), select a role and model variant, choose its sections,
and use Full Prompt to inspect the assembled result. A project agent's startup
prompt lives in `.kollab/agents/<agent-name>/system_prompt.md`; reusable prompt
fragments can live in that agent's `sections/` directory and be included with a
supported `<trender>` include. Skills are separate and use their own `SKILL.md`
directory contract. Reload the agent after changing its startup prompt and verify
the rendered result.

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
The connection service must support PhoneLink; the UI checks this before offering
an invitation.

## Maintaining lessons and media

Edit each topic's JSON in `frontend/src/learn/topics` within the Web UI package;
`src/learn/model.ts` defines the shared workflow contract. Keep entry paths as
individual breadcrumb labels, command examples on one logical line, and expected
results short and observable. Use arrows (→) when describing a UI path inside a
step; preserve literal command syntax. Preserve substantive guidance when
reorganizing a topic.
Write recovery symptoms as labels without a trailing period; the shared renderer
adds the colon before the corrective action.

Keep product assets in `frontend/public/learn/<topic-id>/`. A workflow can show a
focused image or a video with a readable poster and captions. Still previews can
be enlarged to a full-screen view at their native size, with scrolling to inspect
details on a phone. Workflow choices wrap so their labels remain visible. Videos
start from their poster and offer Watch walkthrough, native
playback controls and a return to the preview. Reduced motion keeps playback manual.
Recovery guidance stays collapsed below the instructions.

The reusable recorder, all eleven capture recipes and watched-source freshness checks
are maintained in the existing local capture toolchain. Publishing that private
toolchain is outside [this change's scope](https://github.com/kollaborai/kollab/issues/1006);
its commands and recipe maintenance belong in its local guide. The committed media
manifest records provenance, and does not by itself establish that a scene is current
or that its backend workflow was verified.

From a fresh clone, rebuild the Web UI and capture native product stills with staged
example data at 1280, 820 and 390 px. Replace the reviewed assets in
`frontend/public/learn/<topic-id>/`. When a control's label or location changes, update
the lesson's entry paths, steps and command examples in that topic's JSON, then refresh
the scenes that show the old control. Capture selectors and assertions belong to the
capture recipes, maintained through the local toolchain's guide.

These repository checks require the frontend dependencies:

```sh
cd packages/kollabor-webui/frontend
npm run typecheck
npm test
node --test tests/learn-catalog.test.ts
```

For reproducible Learn screenshots and browser checks with the repository's staged
API fixtures, use a compatible Chromium executable and a fresh screenshot directory:

```sh
CHROME_PATH=/absolute/path/to/chromium \
  LEARN_SCREENSHOT_DIR=/absolute/path/to/fresh-screenshots \
  npm run test:e2e:learn
```

Keep a video at one viewport. Capture responsive product stills separately, and
crop full frames after recording so screenshot clipping cannot resize the video.

The normal Web UI serves media from its local `/learn/` assets. PhoneLink embeds
the referenced images in its signed app payload and uses a poster plus written
steps for video. It does not fetch lesson images or video from an unsigned relay
asset directory or relax the existing content security policy.

## What's New

What's New is a separate `/whats-new` page, opened with the newspaper icon in the
sidebar footer. It reads `kollabor/updates/CHANGELOG.md` from the canonical source
when the Web UI bundle is built. It shows the full Unreleased notes and newest
release section; it does not keep a separate copy of release notes.
If notes are empty or cannot load, the page offers the full changelog and a
retry. Unknown topic IDs show a recovery link to the lesson list.
