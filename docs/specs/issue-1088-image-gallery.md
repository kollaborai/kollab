# Sent-image thumbnails, an image viewer, and a per-agent Gallery

Issue: #1088
Status: Draft
Owner: sapphire (chain lane)

## Problem

Marco pastes an image into the web UI composer and sends it. His bubble shows the
literal placeholder `[image1]` twice (before and after the text), not the image.
The image data reaches the agent for that one turn and is then unreachable: the
stored history row deliberately keeps only the placeholder text, so no reload,
restart, or resume can ever show the image again. There is also no single place
to find what was exchanged with an agent.

Evidence: `routes/messages.py` `_extract_add_message_parts` accepts
`{"type": "image", "image": <data-url>}` from the composer, but the persisted
user row is `content_to_text(submitted_content)` with the comment "Keep data
URLs out of assistant-ui state/history".

## Goal and non-goals

Goal:
1. A sent image renders as a thumbnail in the message bubble, in place of the
   `[imageN]` placeholder. Clicking it opens a viewer.
2. The viewer opens on the clicked image and can step through every image sent
   to this agent, oldest to newest.
3. Sent images are stored durably beside the agent's conversation and survive
   reloads, restarts, and resumed sessions, served back by the engine.
4. Each agent gets a Gallery entry in the sidebar (right under Terminal) that
   lists the images sent to it and the artifacts the agent produced.

Non-goals: no receiving-side rendering changes for agent-generated images beyond
what exists; no editing or deletion of sent images in v1; no image generation.

## What people see

- Bubble: thumbnail (max ~160px tall, rounded, click = viewer) exactly where the
  `[imageN]` token sat; the token text disappears.
- Viewer: dark overlay, the image fit to screen, left/right arrows and
  left/right keys, "i / N" position label, Esc or click-outside closes.
- Sidebar: under Terminal, "Gallery" opens the active agent's gallery panel:
  "Sent images" (grid, newest first, click = viewer) and "Artifacts" (list of
  files the agent created, click = the existing artifact viewer/download).

## Contract

- `kollabor_engine/media_store.py`: `save(conversation_id, data_url) -> ref`;
  validates `data:image/(png|jpeg|jpg|webp|gif);base64,`, decodes, caps at 10 MB
  decoded, stores under `<conversations_dir>/media/<conversation_id>/<id>.<ext>`
  where `<id>` is `yyyymmdd-hhmmss-<rand4>`; `open(ref) -> (bytes, mime)`;
  `list_for(conversation_id) -> [refs oldest first]`. Refs are opaque ids; the
  path never leaves the engine.
- History row: user content stays text for the LLM seam, but the stored row gains
  a parallel `media` list: `[{"index": 1, "ref": "<id>", "mime": "image/png"}]`.
  The `[imageN]` text stays (agents and tools match on it); the row carries the
  ref so any history read can resolve thumbnails.
- Route `routes/media.py`: `GET /media/{ref}` -> bytes, correct mime,
  `Cache-Control: immutable` (ids are unique); 404 on unknown; ref scoped by
  conversation membership check; same bearer-token auth as every engine route.
- Route `GET /media?conversation=<id>` -> `[{ref, mime, ts}]` oldest first, for
  the viewer's browsing and the gallery grid.
- Frontend `api.ts`: `historyContentToText` unchanged; new
  `resolveMediaUrls(row)` maps the row's `media` list to token-replacement URLs
  (`mediaUrl(ref)` with the same bearer-token fetch pattern as avatars).
- Sidebar: `Gallery` entry under Terminal, per active agent, opening the panel.

## Scenarios

- If Marco sends "look at this [image1] thanks" with a paste, then his bubble
  shows text with a thumbnail inline, and the viewer opens on click.
- If he reloads the page or restarts the engine, the same bubble still shows the
  thumbnail (ref resolves from disk).
- If a session is resumed in a new conversation id, previously sent images stay
  viewable from the original conversation's media directory.
- If a request names an unknown ref, the engine answers 404 and the UI shows the
  placeholder text instead of a broken image.
- If the composer sends a non-image attachment or an over-limit image, the send
  fails the way it does today (no silent store), and the placeholder stays.

## Failure modes

1. Oversized image (>10 MB decoded): send rejected with a clear composer error;
   nothing stored.
2. Corrupt/truncated base64: decode fails -> send rejected, nothing stored.
3. Media file deleted underneath a history row: `/media/{ref}` 404 -> UI falls
   back to the `[imageN]` text.
4. Two sends in the same second: id carries a 4-char random suffix; no collision.
5. Gallery opened for an agent with no media: empty-state text, no broken grid.
6. Viewer open while history refresh replaces rows: viewer holds its ref list;
   closing returns to the bubble, no stale DOM writes.

## Decisions

- Store by ref, never data URLs, in history rows (Marco's complaint; keeps
  history payloads small and matches the existing "no data URLs in state" rule).
- Keep the `[imageN]` token in the LLM-visible text (engine seam unchanged;
  agents already reason about the token).
- Images live under the existing conversations dir (same workspace scoping,
  same backup story); no new config key for v1.
- koordinator assigned the lane to sapphire (11-14, thread 956c4daa); Marco
  approved the feature (11-14, hub channel).

## Test plan

Red-first: send a data-url image through the messages route and assert the row
carries a resolvable ref and `/media/{ref}` serves identical bytes (fails today:
no ref, no route). Media store unit tests: validation, caps, id uniqueness,
list ordering, 404s. Frontend: bubble renders thumbnail from a row with `media`
refs and falls back to placeholder text; viewer stepping order oldest-first.
Live proof: send a real paste in the running web UI, click through viewer and
gallery, reload, restart engine, reload again; screenshots at 390/820/1280.
Sibling sweep: ACP sessions (images to Claude Code), voice sessions, phone link.

## Docs and changelog fragment

- `docs/features/webui.md` (or the closest live feature doc): gallery + viewer.
- `changes/1088.added.md`: "Web UI: sent images render as thumbnails in chat,
  an image viewer browses every image sent to an agent, and a per-agent Gallery
  in the sidebar collects sent images and artifacts (#1088)."

## Open questions

- Artifact listing shape (what exactly `result_fields["artifact"]` carries) is
  being traced; if artifacts are not durably listed anywhere, v1 gallery lists
  sent images and shows artifacts only for the current session's known events,
  with the durable artifact index as a fast-follow.
