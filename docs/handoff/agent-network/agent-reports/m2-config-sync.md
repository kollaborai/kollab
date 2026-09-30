# M2 sealed config sync: report (stopped early on the coordinator's order)

## Branch and commits
- worktree branch `worktree-agent-ae763b064388e596b`, worktree `/Users/malmazan/dev/kollab/.claude/worktrees/agent-ae763b064388e596b`
- fast-forwarded from `issue-121-network-simple-flow` at `1111cbd`, then:
  - `2319b6f` sync engine, wiring, tests
  - `cacfe5b` /config managed-by rows + Loadout/Model rows + tests + tmux spec
  - `c12454d` Connect screen: accept notice + "config" row on the joined device
  - `61958bd` remove OAuth from provisioning bundles
- not pushed, no PR, no issue. Commit messages end `, refs #121`, no attribution.

## What is done
- Design: the device that issued a join code (primary) pushes to every device it accepted (`RelayState.config_recipients`, filled at both accept sites, cleared on revoke / rotate / leave). A knock-accepted stranger is never a recipient.
- Every 10 s the primary snapshots global `config.json` overrides, MCP `servers`, API keys (keyring sentinel resolved to the real key, last-read value kept if the keyring hiccups), `agents/`, `skills/`. A device is re-sent when the snapshot digest changes or its relay session changes (= on accept, on change, on reconnect). Failed push backs off 10 s doubling to 300 s.
- Wire: new secure method `config_sync` on the existing secure conversation path (TLS in NaCl boxes, relay sees ciphertext). Ops: `core` (settings+mcp, small: a loadout switch is one request), `sync` (file manifest, device answers with a need-bitmask), `put` (only missing files, batches under one request). Requests are paced under the relay's 8 frames/s.
- Sealing: each bundle is signed by the primary (Ed25519, domain-prefixed) and SealedBox'd to the receiving device key, same construction as the provisioning bundle. Revision (ms clock) blocks replay; a device that holds a newer revision replies `stale` + its revision and the primary raises its floor.
- Secondary: takes config only from `state.inviter`, primary wins, keeps keys the primary does not send, deletes keys/files the primary dropped, writes `config.json` 0600, refuses machine-local keys even from its primary, refuses paths outside `agents/` `skills/`, refuses symlinks, checks every file sha. Records what it manages in `~/.kollab/private/managed-config.json`; `/connect leave` and revoking the primary clear it (values stay as its own).
- Never travels: OAuth logins (oauth dir never read; oauth profile `api_key` and any `access_token/refresh_token/id_token/oauth_tokens` leaf dropped), project `.kollab`, vaults, conversations, scratchpads, symlinks, `__pycache__`/`.pyc`.
- Machine-local keys also excluded (JUDGMENT CALL, one tuple `LOCAL_ONLY` in `plugins/hub/config_sync.py`): `kollabor.updates`, `kollabor.permissions`, `plugins.hub`, `plugins.voice`, version stamps. `kollabor.permissions` and `plugins.hub` are the ones Marco may want flipped.
- `/config`: new read-only Loadout and Model rows lead LLM Settings on every device (Story 8 needed a loadout row; there was none). On a secondary each synced key becomes a read-only label `... managed by <primary>`, value read from the file, secrets shown as `set`.
- Connect screen: accept says `sealed config queued: settings, agents, skills, mcp, api keys; not oauth logins`; the joined device gets `config  received from <primary>  managed by <primary> in /config` once the record on disk names its own primary.
- OAuth removal (proved dead): `OpenAIOAuthCredential`, category `provider:openai:oauth_tokens` and the oauth branches are gone from `plugins/hub/provisioning.py` and `provisioning_store.py`. Only producer was `enrollment_client._profile_credential`, which already returns nothing for non-api-key profiles. A signed bundle naming an oauth login is now refused (test added).

## Files
- new: `plugins/hub/config_sync.py`, `plugins/hub/config_sync_service.py`, `packages/kollabor-config/src/kollabor_config/managed_config.py`, `tests/unit/test_config_sync.py`, `tests/unit/test_config_sync_service.py`, `tests/unit/test_managed_config.py`, `tests/unit/test_config_altview_managed.py`, `tests/tmux/specs/config_managed_by.json`
- changed: `packages/kollabor-config/.../__init__.py`, `plugins/hub/{relay_agent,relay_client,relay_commands,relay_state,secure_conversation,enrollment_client,provisioning,provisioning_store}.py`, `plugins/altview/{config_altview,connect_altview}.py`, `tests/unit/{test_connect_screen,test_hub_network_surface,test_provisioning,test_provisioning_store}.py`
- untouched as ordered: `core_widgets.py`, `layout_manager.py`, `test_voice_plugin_lifecycle.py`

## Tests run (only the ones for touched files; NO full `tests/unit/` run)
- `.venv/bin/python -m pytest` from the worktree root:
  - `test_config_sync.py` 24 + `test_managed_config.py` 5: 29 passed
  - `test_config_sync_service.py`: 15 passed (real secure transport over the in-process Wire; asserts no key/skill text in wire frames)
  - `test_config_altview_managed.py` + scroll/setup/auto_update: 36 passed
  - related relay/enrollment/secure/peer files (before the altview/connect edits): 291 passed
  - `-k "connect or network_surface or request_rows or secret_reprs"`: 348 passed
  - provisioning, provisioning_store, provisioned_profile_management (after the OAuth removal): 39 passed
- tmux, run from the worktree: `config_managed_by.json` PASS (throwaway HOME, `--no-daemon`, KOLLAB_NO_KEYRING=1; screen shows `Loadout: anthropic   managed by mac-kollab`); the 5 M1 connect specs PASS; `test_config_modal_width` PASS; `regression_config_altview_save_false` PASS.
- ruff clean on every touched file (last checked after the OAuth commit). black applied to new files only.
- Gotcha: the shared venv's `.pth` files point at the MAIN checkout's `packages/*/src`. pytest is fine (`pythonpath` in pyproject); manual runs and tmux specs that set `PYTHONPATH="$R"` only need `packages/*/src` added when run from a worktree. My spec sets it itself. A git-ignored symlink `.venv` sits in the worktree.

## NOT done (exact next steps)
1. Docs, all wrong or incomplete now:
   - `docs/specs/agent-network-simple-flow.md`: section 9 (primary = issuer of the code, chain case, LOCAL_ONLY list, limits below, `/config` rows, leave/revoke), Story 1 target lines (now `sealed config queued: ...` and the `config` row), Story 8 (mention the Loadout/Model rows), section 12 item 2, section 15 (new open item: join still copies the issuer's active API-key profile once as `kollab-<id>`).
   - `docs/guides/connect.md`: the paragraph "What accepting copies" describes the old one-profile copy; replace with the sealed config (what syncs, what does not, `managed by` in `/config`, leave keeps the values, revoke stops updates).
   - `CHANGELOG.md` [Unreleased] and `kollabor/updates/CHANGELOG.md`: add Added/Changed lines; keep the two byte-identical (`cmp`).
2. `tests/live/m2/` (README + `proof.sh` reusing `tests/live/m1/env.sh`, `scan.py`, `tmuxtype.py`; run after `m1/proof.sh` and before `m1/teardown.sh`, on wheels built from this branch). Planned Story 8 steps, none written:
   - s8-pre: both `m1-*` sessions up and each `/connect status` lists the other side.
   - s8-loadout: on the Mac edit `~/.kollab/config.json` atomically to add profile `m2-proof` (fake key `sk-m2-proof-<rand>`, model `m2-proof-model`) and set `active_profile`; within 60 s the server's `~/.kollab/config.json` has the same active_profile, model and a key with the same sha256, mode 0600.
   - s8-config-screen: on the server run `/config`, search `loadout`, pane shows `Loadout: m2-proof   managed by <mac device name>`.
   - s8-sealed: fake key appears in no pane, no kollab log on either host.
   - s8-excluded: server `~/.kollab/oauth/openai.json` sha256 unchanged before/after, server `hub/vaults` listing unchanged.
   - s8-files: create `~/.kollab/skills/m2-proof-skill/SKILL.md` on the Mac, it appears on the server within 2 min; delete it, it disappears.
   - s8-reconnect: edit the server's managed key by hand, restart the server TUI, within 60 s of the relay coming back it is restored.
   - s8-cleanup: remove the fake profile and skill on the Mac, wait for the server to follow.
3. Full `tests/unit/ -q` run (counts unknown). Run it first.
4. Live proof on the real relay/two machines: unproven. All wire behaviour is proven only in-process.

## Decisions for Marco / known limits
- Join still copies the issuer's active API-key profile once as a private `kollab-<offer id>` profile (M1 code, ~700 lines of enrollment tests depend on it). The sealed sync makes it redundant and it can show a duplicate loadout. Removal touches the delegation store, recovery journal and challenge scope. Left alone; needs Marco's word.
- Downstream OAuth slots stay (`provisioned_state` oauth fields, `token_storage` profile-scoped paths, `profile_manager` overlay, `api_communication_service` passing `profile_name`): they have callers in the login flow the vision answers put off limits.
- A chain (A issues to B, B issues to C) makes B the source for C and forwards A's keys; "one primary" holds for the star case.
- Limits: files over 512 KiB or over 30 KB compressed are skipped and counted; max 1500 files; a key changed only in the OS keyring syncs on the next config change or reconnect; running app follows via `config.reload()` + `set_active_profile(reload_profile=True)` + MCP reload, but agents/skills changes are picked up on next use; `/llm` on a secondary can still switch locally until the next primary change or reconnect (loadout system untouched); `/mcp` does not mark managed servers.
- Networks joined before this build have no recipients: re-join once (0.11.0 needs new codes anyway).
