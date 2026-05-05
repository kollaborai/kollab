500 error display test spec
===========================

goal
----
verify that when an llm api call returns 500, the user sees a real error
message in the ui that names:
  - the error type (server error, not misleading "rate limited")
  - the status code (500 / 502 / 503 / 504)
  - the attempt count (1/5, 2/5, ...)
  - the retry delay
  - a short snippet of the error body (enough to know which upstream failed)

failure mode observed 2026-04-21:
  - user swapped to openai profile via /profile modal (client-only switch bug,
    separate issue)
  - daemon kept calling glm, glm returned 500 network errors
  - ui showed NOTHING during the 5-retry backoff cycle
  - the only hint was a generic spinner, so "rate limited" was the user's
    interpretation, not the ui's message

fix scope (separate change, already diagnosed):
  1. api_communication_service.py:418-420 — forward error_type + snippet
     through the on_rate_limit callback
  2. streaming_handler.py:163-167 — rename-behavior-only: display the real
     error_type + snippet instead of hardcoded "rate limited"

this spec tests step 2 ONLY (the ui display). step 1 is a precondition.

test topology
-------------
two tmux sessions on a dedicated socket, matching the phase_4_5_smoke.sh
pattern. one session runs the app ("daemon" target), the other sends input
and captures output. sessions never cross pollute a real daemon.

  tmux socket: kollabor-err500-$$
    session 1 (APP):    python main.py  (interactive, headless via tmux)
    session 2 (DRIVER): sends slash commands + captures ui output

no --attach in this test. single-process is enough — the retry display
path runs in the same process as the ui. the app session IS both the
daemon and the attach client.

if we want to test attach-mode too, that's a follow-up spec that mirrors
phase_4_5_smoke.sh (daemon + attach client, drive the attach client).

mocking strategy
----------------
we need an http endpoint that reliably returns 500. options considered:

  A) python http.server one-liner in background
     + zero dependencies, dies with test
     + full control over response code + body
     - needs a free port, process management in cleanup
     CHOSEN

  B) point the base_url at localhost:1 or a dead endpoint
     + trivial
     - raises connection error, not http 500 — wrong error type
     REJECTED

  C) reuse an existing provider mock
     - none exists for this path
     REJECTED

the mock server (tests/tmux/mock_llm_500_server.py):
  - tiny python http.server subclass
  - always returns http 500 with a canned body:
      {"error": {"code": "500", "message": "Operation failed"}}
  - logs received requests to stderr for debugging
  - accepts any path so the openai /responses and glm /chat/completions
    endpoints both hit it
  - listens on an ephemeral port; the test captures the port and
    templates it into a throwaway profile's base_url

throwaway profile
-----------------
we CAN'T modify the user's real profiles. the test:
  1. creates ~/.kollab/config.json backup copy: config.json.test-bak-$$
  2. writes a temp config with a throwaway profile "test-500-mock" pointing
     at http://127.0.0.1:<mock-port>/v1/chat/completions (custom provider,
     supports_tools=false to match glm profile shape)
  3. sets that profile as the current active profile
  4. on exit, restores config.json from backup
  5. CRUCIAL: if the user has a daemon already running, we DO NOT stop it.
     we spawn our own tmux session on our own socket. the config file
     write still risks racing a live daemon, so we sanity-check
     ~/.kollab/hub/presence/*.json first; if anything alive, abort
     with a clear message ("stop your agents before running this test").

test steps
----------
  step 1: preflight
    - verify no live hub agents (presence files all stale)
    - verify python main.py is runnable (compiles clean)

  step 2: set up mock + profile
    - start mock_llm_500_server.py in background, capture port + pid
    - back up config.json
    - overlay test-500-mock profile into config.json
    - set active profile to test-500-mock
    - verify by `python -c "import json; print(json.load(open(CONFIG)).get('kollabor.llm.default_profile_name'))"`
      that the active profile landed

  step 3: launch app session in tmux
    - tmux new-session APP  "python main.py"
    - wait for "Ready" marker in pane capture (or app_init_sleep=3)

  step 4: send a prompt that will trigger an api call
    - tmux send-keys APP "Hello" Enter
    - wait ~8s for the first retry attempt to log + display
      (base_delay=5s + a little slack)

  step 5: capture + assert
    - capture APP pane
    - asserts (all MUST pass):
       A. ui shows "server error" OR "500" text visible
          (not hardcoded "rate limited")
       B. ui shows "attempt 1/5" or "1/5"
       C. ui shows a retry delay ("5s" or "retrying in")
       D. ui shows some error body snippet ("Operation failed" OR
          "Network error" OR the upstream message)
       E. ui does NOT show "rate limited" (that's the bug we're fixing)

  step 6: send ESC to cancel mid-retry
    - verify the app doesn't hang, returns to idle
    - verify a cancellation message shows

  step 7: final capture + summary

teardown
--------
  - tmux kill-server -L $SOCKET
  - kill mock server pid
  - restore config.json from backup
  - delete backup file
  - clean up any temp presence/socket files created
  trap on EXIT so it always runs

files to create
---------------
  tests/tmux/mock_llm_500_server.py   — the 500-returning http server
  tests/tmux/err500_display.sh        — the shell driver (daemon + driver)
  tests/tmux/specs/err500_display.json — optional thin JSON wrapper

the shell driver is primary because:
  - two tmux sessions
  - mock server lifecycle
  - config.json backup/restore
none of these fit the existing JSON test runner.

open questions for operator
------------------------
  Q1: should we test in --attach mode too (3 sessions: daemon + attach + driver)?
      my take: no, not in v1. the error-display path runs in the client process
      either way. adding attach doubles the test complexity without more coverage.
      follow-up spec if we want to verify daemon-side error propagation.

  Q2: is it ok to touch ~/.kollab/config.json with backup/restore?
      my take: yes, with a preflight "no live daemons" check. otherwise we
      have to invent a --config-dir override for the test, which is a bigger
      change. but if you'd rather, i can add that flag first.

  Q3: run as part of `tests/tmux/run_all_tests.sh`, or standalone?
      my take: standalone. it modifies user config and spawns servers;
      run_all_tests is for lightweight isolated specs.

review and tell me which knobs to turn. once you approve i'll write the
mock server + shell driver and run it.
