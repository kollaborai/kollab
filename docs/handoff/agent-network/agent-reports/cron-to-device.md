# cron-to-device report

Branch: worktree-agent-add4be445207fcd35 (worktree /Users/malmazan/dev/kollab/.claude/worktrees/agent-add4be445207fcd35)
Base: fast-forwarded to issue-121-network-simple-flow at a759814. One commit on top: 3f7770d (refs #121, no attribution). Not pushed, no PR, no issue.

## What the fire path did before (a759814)

- `_cron_loop` built `HubMessage(to=<target>, from_identity="hub-cron", content="[cron <id>] <msg>")` and called `_route_message`. That router already resolves an `agent@device` (`parse_handle`, `resolve_handle`, `relay.send`): the same branch hub_msg uses. So a handle target would have been delivered.
- Nothing could set one. The XML extractor read only `interval` (the `target="name"` attribute that the hub-context help text advertised was ignored), the native tool had no target parameter, and the handler read `target`, which nobody passed. Every reminder went to the agent itself. Only `/hub cron add <handle> ...` could create a remote job, and it validated nothing.
- The loop threw away `_route_message`'s return value. An unknown handle, "not connected", or a receiver refusal vanished; "hub cron fired" was logged anyway and `next_fire` advanced. An exception escaping the send (TimeoutError etc.) aborted the whole tick at debug level and left `next_fire` unchanged, so it retried every 10 s beat and starved the other jobs.

## What it does now

- Tag: `<hub_cron_add to="agent@device" interval="5m">`, read with `tag_pattern` / `tag_attrs` (any order, either quote). `target=` is accepted as the older spelling of `to`. An attribute-less tag now matches and the handler answers "requires an interval attribute" instead of leaving it raw on screen.
- Native tool: optional `to` string parameter (schema, `xml_attributes`, example, key rules, safety line updated). No `to` still means the sender.
- Add time (`_cron_add`, shared by the tool and `/hub cron add`): a `relay:` address or an `@` that is not a valid handle is refused ("bad target: use an agent name, or agent@device as /connect status lists it", the bad text is not echoed). A valid handle is lower-cased. Offline or off-roster is accepted. The tool handler also refuses to schedule a network message from inside a remote task turn (mirrors hub_msg's rule that a remote task replies only to its sender).
- Fire (`_fire_cron_job`, extracted from `_cron_loop`): still `_route_message`. For a handle it reads the rejections. Logged at WARNING: `hub cron <id> not delivered to <handle>: <reason>`; kept in `job.last_error` and shown in `hub_cron_list` and `/hub cron list` as `last fire failed: <reason>`; job stays and is rescheduled a full interval out. A send that raises keeps only its class (`send failed (TimeoutError)`), so nothing from the exception reaches a log or screen. A success clears `last_error`, logs "hub cron fired", and draws the outgoing box like hub_msg and the CLI path do.
- Drop: `RelayAgentBridge.device_unknown(name)` (new, async, reads through the owner so a second agent in the workspace answers the same). True only when the relay is online and no approved device can be `name`: not a recorded peer name, not on the live roster, and every approved key is either named or online. If an approved device is unnamed and offline it could be the one meant, so the job is kept. Relay unreachable, no bridge, any error: kept. When true the job is removed with `hub cron <id> dropped: device <name> is not on this network (<reason>)`.
- Local targets (`lapis`, `all`, `*`, self) go through the same code as before; their rejections ("in waiting state") stay silent, exceptions still propagate as before.
- Keys, relay addresses and receipts: none in add/list output, logs or the outgoing box (test scans 6 failure shapes, including an exception whose text is a relay address).

## Files

- plugins/hub/plugin.py: tag registration, `_handle_hub_cron_add_tool`, `_cron_target_error`, `_cron_add`, `_cron_list`, `HubCronJob.last_error`, `_fire_cron_job`, `_cron_device_gone`, `_cron_loop`, hub-context help line (`target="name"` -> `to="name"`)
- plugins/hub/relay_agent.py: `device_unknown`
- packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py: `hub_cron_add` definition
- bundles/agents/system/hub-collaboration.md (cron section only; `_base/sections/` never listed cron)
- CHANGELOG.md, kollabor/updates/CHANGELOG.md (one Fixed line each; `cmp` identical)
- docs/guides/hub-quick-start.md, docs/architecture/tool-calling-architecture.md (example and error text for the tag)
- tests/unit/test_hub_cron_to_device.py (new, 41 tests), tests/unit/test_hub_xml_tags.py (`hub_cron_add` moved from ONE_OR_NO_ATTRIBUTE into CONVERTED, as that file requires for a tag with two attributes)
- Untouched: core_widgets.py, layout_manager.py, test_voice_plugin_lifecycle.py, docs/specs/.

## Tests

- `KOLLAB_NO_KEYRING=1 /Users/malmazan/dev/kollab/.venv/bin/python -m pytest tests/unit/ -q` (from the worktree root): 4918 passed, 7 skipped, 203 subtests passed, 0 failed, 76 s.
- Focused: test_hub_cron_to_device, test_hub_xml_tags, test_hub_msg_remote_target, test_hub_network_surface, test_relay_agent_bridge, test_relay_network_trust, test_tool_grant_revoke: 283 passed.
- `/Users/malmazan/dev/kollab/.venv/bin/ruff check` on every touched .py file: clean. py_compile clean.
- What the new tests cover: the tag with `to` in every order and both quote styles, `target=` alias, through the real ResponseParser; native call with `to`; default is self; local, self and `all` fires unchanged; an `agent@device` fire lands in the same relay `send` a hub_msg uses (same kwargs, same address, kind="message"); undeliverable fire logged with its reason and kept (unknown agent on a known device, receiver refusal, send exception, no bridge, relay unreachable, unnamed offline device); unknown device dropped through one real `_cron_loop` beat while a failing job and a local job keep going and are rescheduled; malformed targets refused (6 shapes); offline remote accepted; slash command validates the same; remote-task guard; leak scan. Against the real two-bridge fixture: a cron fire delivers to the other bridge's model, and one test runs raw reply text -> real ResponseParser -> real ToolExecutor -> job -> cron beat -> other device.
- Mutation check: 21 single-line mutations of plugin.py / relay_agent.py (ignore rejections, never/always drop, drop the online guard, ignore roster/recorded names/unaccounted devices, no target validation, tag ignores `to` or `target`, no remote-turn guard, no exception isolation, log the exception text, never clear last_error, no display, retry every beat, no handle normalize, hide last_error in list). Each fails at least one test; files restored byte-for-byte.

## Unverified

- No live run: no kollab process, no real model emitting the tag, no ssh, no alzan-prod. The end-to-end proof is in-process (two real bridges on the test wire). A real `hub_cron_add to="infra@alzan-prod-home"` on the Mac reaching the server agent still needs the two-machine run, plus a look at the screen: each remote fire draws one `lapis -> infra@alzan-prod-home` box (same as hub_msg and the CLI path), so a 30 s job draws one every 30 s.
- `device_unknown` is only exercised on the owner side. A second agent in the same workspace goes through the owner RPC (`relay.status` and `relay.directory` are allowed local methods); that path is by design, not driven in a test.
- No tmux spec (CLAUDE.md asks for one; needs a live app).
- Under `manual` trust a fire needs a human grant, so it fails with the grant reason every interval (kept, logged, listed). Not changed.

## Flags

- docs/specs/agent-network-simple-flow.md section 7 row for `hub_cron_add` is silent on failure. I did not edit the constitution. If it should say it: "a fire that is not delivered is logged and listed; a job whose device is not on the network is dropped".
- The drop rule is deliberately conservative: an approved device that is offline and has no recorded name (a third device this one never accepted or joined through) keeps every typo'd or dead job alive, failing and logged each interval, until that device shows up or is revoked. Persisting roster names would close it; not done.
- Noticed, not touched: `hub_msg` tool definition (tool_definitions/hub.py) still describes the Codex model ("authorized remote relay agent", `kind='message'` with the exact human-authorized request) although section 7 says `to` takes `agent@device` under open trust: a remnant per section 1. `_handle_hub_cron_add_tool` returns success=True for `_cron_add`'s "bad interval:" / "usage:" strings (pre-existing).
