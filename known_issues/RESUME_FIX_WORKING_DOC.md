# Resume Fix — Working Doc (context-survival)

Status as of 2026-05-29. Branch work happens off `issue-22-dedupe-path-helpers`
(that's where `kollabor/llm/session_resume.py` exists). Mesh MUST be stopped
before editing (`kollab --hub stop all`) — running the app spawns hub agents
that commit to the current branch autonomously.

## The goal
Make `/conversations` resume actually work in attach/daemon mode, and fix the
related session bugs. The whole resume area is FOUR distinct bugs.

## What's ALREADY fixed + landed (do not redo)
- #25 tool calls in browser — commit db6d636 on branch `fix-session-display-bugs`, in PR #28.
- #26-1A widget session name (attach reads daemon's real session) — commit 135c6a8, PR #28.
- These are verified + clean. PR #28 is mergeable. LEAVE IT.

## The resume bug map (what's LEFT)
1. **#27 — browsers render nothing on resume in attach mode.**
   Root: the two conversation browsers call client-side `kollabor/llm/session_resume.py`,
   which emits `EventType.ADD_MESSAGE` on the CLIENT event bus. In attach mode the
   client never registers the ADD_MESSAGE handler (register_hooks is skipped), so
   the emit has zero subscribers → blank.
   - altview call site: `kollabor/altview/command_integration.py:365` (resume_selected_session after stack_mgr.push)
   - fullscreen call site: `kollabor/fullscreen/command_integration.py:268`

2. **#26 cause B — resume only updates conv_mgr.current_session_id, not the 3-way reset.**
   So the widget shows a name with no matching .jsonl, AND new turns append to the
   OLD .jsonl. Present in BOTH `session_resume.py:88` AND `kollabor/state/local.py:1953`.
   The correct 3-way reset pattern: `kollabor/llm/session_manager.py:136-142`
   (logger.reset_session + conv_mgr.reset_session + api_service.set_session_id).

3. **#29 — no-arg `/resume` crashes with KeyError 'width'.**
   `_show_conversation_menu` / `_build_*_modal` in `plugins/resume_conversation_plugin.py`
   builds a modal dict missing the `width` key; `UIConfig(width=modal_definition["width"])`
   at ~:1207 throws. Self-contained, separate from the daemon stuff.

4. **State-swap-into-next-turn — UNVERIFIED (the crux).**
   After resume, does the daemon's `conversation_history` actually get sent on the
   NEXT API turn? My live test was CONFOUNDED: attached to a hub agent whose
   vault/working-memory answered the follow-up, not the resumed history. Tell:
   resumed a May-5 session, the reply cited May-28 work — impossible from that
   history. DISPOSITIVE CHECK: read `~/.kollab/projects/*/conversations/raw/<session>_raw.jsonl`
   after a resume — does the outbound `messages` payload contain the resumed
   session's messages?

## The CORRECTED design (smaller than new RPC plumbing)
The working path ALREADY EXISTS: `/resume <session_id>` routes through
`state_service.resume_conversation()` → RPC → `LocalStateService.resume_conversation`
(`kollabor/state/local.py:1907`). VERIFIED LIVE that `/resume <id>` renders in
attach mode (daemon logs `resume: auto-saved` + `Loaded session`). The browsers
just don't use it.

**Fix plan:**
- A. Rewrite `kollabor/llm/session_resume.py` to be ONE shared wrapper: call
  `state_service.resume_conversation(session_id)` (daemon-side) and render the
  returned metadata via `renderer.message_coordinator.display_message_sequence`
  (the pattern `_load_conversation` uses at resume_conversation_plugin.py:1099-1117).
  Do NOT create a 4th copy. `/resume`, altview browser, fullscreen browser all
  share this one path.
- B. Route both browser command integrators through that wrapper instead of the
  old client-side ADD_MESSAGE body.
- C. Fix #26B's 3-way reset ONCE in `kollabor/state/local.py:1953` (verify
  logger.reset_session / api_service.set_session_id reachable from LocalStateService).
- D. Fix #29's modal width default (independent, can do anytime).
- BEFORE wiring A: confirm `state_service` AND `renderer.message_coordinator`
  are reachable from the altview command integrator's `self` context.

## Verification protocol (both halves, fresh daemon)
1. `kollab --hub stop all`; fresh `python main.py` in tmux (220x50).
2. Drive input CHAR-BY-CHAR (tmux paste eats args): `/` then type `resume 2605...-id`
   one char at a time, then Enter. Or use the browser: `/` then `conversations`.
3. Display: resume renders the conversation on screen.
4. STATE (the one that matters): after resume, send a follow-up, then read the
   raw payload `conversations/raw/<new_session>_raw.jsonl` — outbound `messages`
   must contain the RESUMED session's content. Do NOT trust the model's worded
   reply (vault confounds it). Use a distinctive NON-hub session to be safe.

## Guardrails
- PR-not-merge for anything touching the daemon/attach seam. Marco reviews.
- Don't touch PR #24's commits (force-push forbidden); its body is already
  corrected to flag the conversations-migration piece as gated on the raw check.
- No attribution in commits. Branch + `fixes #N`.
- Stop the mesh before editing; verify on fresh daemons (old ones hold stale code).

## Issues
#20 flicker/migration, #21 renderer twins, #23 shadow audit (dedup side).
#25 (done), #26 name mismatch, #27 blank resume, #29 width crash (this workstream).
