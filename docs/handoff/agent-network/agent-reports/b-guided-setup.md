# b-guided-setup (issue #121, 0.11.0 guided setup)

## Done
- f75b30a: notice, two choices, "On your other computer" box, post-join line, marker, gate;
  unit tests; tmux specs at 80 and 120 columns.
- Next commit (git log -3): constitution Story 1, docs/guides/connect.md, CHANGELOG pair
  (cmp identical), and the ConnectGuideAltView default that test_altview_registration needs.
- Code: plugins/hub/connect_guide.py (texts, marker, gate, post_join_line);
  plugins/altview/connect_altview.py (ConnectGuideAltView, connect_guide_lines,
  ConnectScreenState.guide, ConnectOutcome.note); plugins/hub/plugin.py (SYSTEM_STARTUP hook
  `hub_connect_guide`, _run_connect_guide, _connect_has_network, _guided_new_network,
  _start_connect_network, _altview_stack, _primary_name; `note` in the approved result).
- Reuse: the form's first-device path and the guide share _start_connect_network; three copies
  of the stack-manager lookup became _altview_stack().

## Marker (the release driver deletes it after the test runs)
- `~/.kollab/connect-guide-seen`, written on the first Enter or Esc at the notice.
- Override: env `KOLLAB_CONNECT_GUIDE_MARKER`. The specs run with an empty HOME and their own
  marker; the real one was checked untouched after both runs.

## Tests
- tests/unit/test_connect_guide.py: marker once / Enter / Esc / quit unanswered, network vs
  no-network branches, double-tapped Enter, flow routing, box text at 80 and 120, post-join
  line and its render, gate (pipe, detached, query, --hub, --web-ui, no tty, spawned child).
- tests/tmux/specs/network-guided-setup-80.json and -120.json: PASS (notice, Enter, the two
  choices, marker written, Esc closes). They stop there by design.
- Full unit suite: 5329 passed, 1 failed (test_altview_registration needed a default for
  has_network; fixed, that file plus the guide and connect tests re-ran green, 120 passed).
  The whole suite was not re-run after that one-line fix.
- Changed an existing test: test_connect_attached_and_waiting.py (approved result carries `note`).

## Left / flags
- The primary's name is not known at approval time (it arrives with the first sealed bundle),
  so the post-join line says "the device that issued the code" unless the managed-config
  record already names the inviter. Spec says <primary name>; decide whether that is enough.
- Not driven live: "Start a new network" (creates a real network on kollabor.ai), the
  "On your other computer" screen in a terminal (unit-tested only), and the default daemon
  launch (hook runs in the attached client; unit-tested with fakes, specs use --no-daemon).
- The post-join line shows on the code form's outcome screen only; a form closed before the
  approval never shows it.

## Next step
- Driver live run on a clean HOME with the default (daemon) launch: confirm the notice shows
  once, Enter on no network offers both choices, "Start a new network" shows the box, the
  second machine joins by code and sees the post-join line; then `rm -f ~/.kollab/connect-guide-seen`.
