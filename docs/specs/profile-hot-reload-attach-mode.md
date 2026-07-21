---
title: Profile Hot-Reload in Attach Mode
created: 2026-07-20
status: completed
author: maintainers
updated: 2026-07-20
---

editing or switching the active LLM profile from the `/profile` modal must
reload the DAEMON's live provider, not just the attach client's shadow. today
it reloads the shadow only, so model/key changes silently fail to take effect
until a full app restart.


## problem

symptom (reproduced live):

- user edits the active profile's model via `/profile` (e.g.
  `tencent/hy3:free` -> `deepseek/deepseek-v4-flash`). the command prints
  `Updated profile: openrouter ... [reloaded - changes applied]`.
- the very next turn STILL calls the old model (OpenRouter 404 on
  `tencent/hy3:free`). the model status widget STILL shows the old model.
- re-applying the API key via `/profile` behaves identically.
- only a full `python main.py` restart actually applies the change.

so the "[reloaded - changes applied]" message is misleading in attach mode: the
config on disk is updated and the client shadow is reloaded, but the running
daemon (which owns the real provider and makes the API calls) is not.


## root cause

attach mode = a thin client attaches to a long-running daemon; the daemon owns
the real `LLMService` / provider / `profile_manager`. the `/profile` modal
handlers reinitialize the CLIENT SHADOW's provider and never touch the daemon:

- `kollabor/commands/system_commands/handlers/profile_actions.py:58-67`
  (`select_profile`) and `:269-279` (`edit_profile_submit`) call
  `handler.llm_service.api_service.reinitialize_provider(profile)` directly.
- in attach mode `handler.llm_service` is a client-side shadow
  (`kollabor/application.py:539,548,670-671`; resolved in
  `handlers/profile.py:70-75`). reinitializing it does nothing to the daemon's
  live provider or `profile_manager`.
- the daemon keeps the old model/key. the model widget is then *correct* about
  a stale daemon: `render_model`
  (`packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py:220-229`)
  reads `remote_state["model"]`, populated from the daemon's still-active
  profile by `kollabor/state/refresher.py:258`.

the correct daemon-reload path already exists and is fully RPC-wired -- it is
simply not called by the modal:

- `LocalStateService.set_active_profile(name, reload_profile=True)`
  (`kollabor/state/local.py:785`) reloads the daemon `profile_manager` from
  config, THEN reinits the provider.
- `RemoteStateService.set_active_profile(...)` -> RPC ->
  `kollabor/state/handlers.py:135-147` (`_set_active_profile` reads the
  `reload_profile` param).

the slash path `/profile set` already routes through
`state_service.set_active_profile()` (`handlers/profile.py:331`), which is why
switching profiles via slash reaches the daemon -- but it omits
`reload_profile`, so it too misses an in-place EDIT of the active profile
(config changed while the daemon's registry keeps the old copy).


## fix

in `profile_actions.py`, both `select_profile` and `edit_profile_submit`: when
the affected profile is the active one, route the reload through the state
service instead of the shadow.

```python
active_name = handler.profile_manager.active_profile_name
state_service = handler.event_bus.get_service("state_service")
if state_service is not None:
    # attach OR local -- daemon-owned reload; reload_profile=True so an
    # in-place model/key EDIT (not just a switch) is picked up from config.
    await state_service.set_active_profile(active_name, reload_profile=True)
else:
    # local-mode fallback (no state service): existing shadow reinit
    handler.llm_service.create_background_task(
        handler.llm_service.api_service.reinitialize_provider(profile),
        name="reinitialize_provider",
    )
    handler.llm_service.create_background_task(
        handler.llm_service._load_native_tools(),
        name="reload_native_tools",
    )
```

`reload_profile=True` is the load-bearing part: without it the daemon reinits
its provider from the profile object already in its registry, not from the
freshly-edited config, so an in-place model/key change is dropped.

optional, same class of gap: pass `reload_profile=True` at the slash path
`handlers/profile.py:331` so `/profile set` also hot-reloads in-place edits of
the already-active profile.


## verification

- tmux spec (`tests/tmux/specs/`): attach to a running daemon, `/profile`-edit
  the active profile's model, send a message, assert the response/log uses the
  NEW model without a restart, and assert the model status widget shows the new
  model.
- unit: extend `tests/unit/test_model_profile_hot_reload.py` to cover the MODAL
  path (current coverage is the slash/state path only), asserting
  `state_service.set_active_profile(reload_profile=True)` is invoked for an
  active-profile edit in attach mode.

behavioral change to LLM provider lifecycle -> MUST be verified on the live
daemon, not only in unit tests. note the daemon must be running the dev tree
(`.venv`), not a stale pyenv build, for the fix to be exercised.


## files touched

- `kollabor/commands/system_commands/handlers/profile_actions.py` (primary)
- `kollabor/commands/system_commands/handlers/profile.py` (optional slash-path
  `reload_profile`)
- `tests/unit/test_model_profile_hot_reload.py` (modal-path coverage)
