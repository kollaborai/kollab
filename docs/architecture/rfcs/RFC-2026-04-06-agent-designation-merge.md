---
title: "Agent Identity System Redesign"
doc_type: architecture-rfc
created: 2026-04-06
modified: 2026-04-06
status: rejected
superseded_by: RFC-2026-04-04-agent-hub-unification
reviews:
  - ../records/reviews/REVIEW-2026-04-06-agent-designation-merge-architecture.md
  - ../records/reviews/REVIEW-2026-04-06-agent-designation-merge-ux.md
---
# Agent Identity System Redesign

This RFC proposed merging user-facing "agent" and "designation" concepts
into a single identity model. The reviewed proposal remains useful as
historical design context, but should be treated as rejected in its
documented form.

problem:
  two identity systems visible to users: "agent" and "designation"
  confusing for new users who don't understand the distinction
  "designation" is an ugly word nobody likes

decision:
  rename "designation" → "identity" across the codebase
  agent = what you CAN DO (skills, prompt, tools)
  identity = who you ARE (memory, comms, persistence)

  solo mode: identity = agent name (automatic, invisible)
  team mode: identity = org chart role (explicit)

  kill "default" agent → use "kollabor" as base agent
  kill --designation flag → add --as for power users
  gem pool reserved for org launcher color themes only

new behavior:
  kollab                            agent=kollabor, identity=kollabor
  kollab --agent coder              agent=coder, identity=coder
  kollab --agent jarvis             agent=jarvis, identity=jarvis
  kollab --agent coder --as marcus  agent=coder, identity=marcus
  kollab --org engineering.json     agent=per-role, identity=per-org-chart

identity resolution order:
  1. --as flag (explicit override)
  2. org chart role (if --org mode)
  3. agent name (default, solo mode)

collision handling:
  if identity already taken (e.g. two coder agents):
    second agent gets identity=coder-2, coder-3, etc.
  no gem pool fallback for solo mode
  gem pool only used by org launcher for color assignment

changes:

  1. rename designation → identity (codebase-wide)

     every file that references "designation" gets renamed.
     this is a mechanical find-and-replace with these mappings:
       designation → identity
       --designation → --as
       from_designation → from_identity
       parent_designation → parent_identity
       get_agent_by_designation → get_agent_by_identity
       effective_designation → effective_identity
       DesignationAssigner → IdentityAssigner
       DESIGNATION_POOL → kept (internal, for gem color lookup)
       hub_designation config key → hub_identity
       GemDesignation → GemIdentity (or keep as color-only)

  2. default agent → kollabor

     where: packages/kollabor-agent/src/kollabor_agent/agent_manager.py
     what: change fallback from "default" to "kollabor"
     bundles/agents/default/ still exists but is no longer special
     the "kollabor" agent becomes the base agent

  3. CLI flag changes

     where: kollabor/cli.py
     what:
       remove --designation / -d flag
       add --as flag (hidden from basic help with argparse.SUPPRESS)
       help text for --agent: "Set your agent type and hub identity"

  4. identity = agent name by default

     where: plugins/hub/plugin.py
     what: if no --as flag and no org mode, identity = agent.name
     remove gem pool fallback for solo agents
     remove special-casing for agent.name == "default"

  5. vault path follows identity

     where: plugins/hub/vault.py
     what: vault path is ~/.kollab/hub/vaults/{identity}/
     identity defaults to agent name, so solo vault = agent name
     team mode vault = org chart role name

  6. presence files store agent_name

     where: plugins/hub/presence.py
     what: add agent_name field to presence JSON
     roster display shows agent_name, falls back to identity
     enables --attach to resolve by agent name

  7. status bar: no redundancy in solo mode

     where: packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py
     what: hub widget shows identity
     when identity == agent_name (solo mode), agent widget hidden
     when identity != agent_name (team mode), show both

  8. hub collaboration docs

     where: agents/system/hub-collaboration.md
     what: replace "designation" with "identity" in all docs
     hub_msg to="identity" (not "designation")
     explain: identity = agent name unless in team mode

  9. org launcher

     where: plugins/hub/org_launcher.py
     what: org chart "designation" field → "identity" field
     org charts can use any string as identity
     gem colors assigned based on identity for visual theming
     collision handling: identity-2, identity-3 for duplicates

  10. messenger and routing

      where: plugins/hub/messenger.py, messaging_bridge.py
      what: rename designation fields to identity
      message routing uses identity (unchanged behavior)
      from_designation → from_identity in HubMessage

  11. coordinator

      where: plugins/hub/coordinator.py
      what: DesignationAssigner → IdentityAssigner
      solo mode: returns agent name directly
      team mode: returns org chart identity
      numbered fallback (identity-2) replaces gem pool fallback

  12. data models

      where: plugins/hub/models.py
      what: rename GemDesignation fields
      keep gem data for color/caste assignment
      DESIGNATION_POOL can stay internal (just a color lookup)

  13. engine API

      where: packages/kollabor-engine/src/kollabor_engine/
      what: rename designation fields to identity in routes
      hub.py, hub_ws.py, hub_bridge.py

  14. agent runtime

      where: packages/kollabor-agent/src/kollabor_agent/runtime.py
      what: rename designation field to identity
      effective_designation → effective_identity
      parent_designation → parent_identity

  15. agent manager

      where: packages/kollabor-agent/src/kollabor_agent/agent_manager.py
      what: rename designation references to identity
      agent.json "designation" key → "identity" key

  16. prompt renderer

      where: packages/kollabor-ai/src/kollabor_ai/prompt_renderer.py
      what: hub_designation trender tag → hub_identity
      update identity context injection

  17. attach client

      where: kollabor/attach_client.py
      what: --attach resolves by agent name first, then identity
      display shows agent name

  18. application init

      where: kollabor/application.py
      what: identity assignment uses new resolution order
      config key: plugins.hub.identity (was plugins.hub.designation)

  19. agent orchestrator

      where: plugins/agent_orchestrator/plugin.py
      what: rename designation field in snapshots

  20. notifier

      where: plugins/hub/notifier.py
      what: rename get_designation callback

  21. task ledger

      where: plugins/hub/task_ledger.py
      what: rename agent_designation params to agent_identity

  22. altview console

      where: plugins/altview/hub_console_altview.py
      what: rename my_designation to my_identity

  23. mentiko adapter

      where: packages/kollabor-agent/src/kollabor_agent/mentiko_adapter.py
      what: --designation → --as in spawned commands

  24. agent.json files

      where: bundles/agents/*/agent.json
      what: rename "designation" key to "identity"
      or remove entirely (solo agents use dir name)

  25. org chart JSON files

      where: plugins/hub/organizations/*.json
      what: rename "designation" key to "identity"

  26. error messages

      where: throughout
      what: all user-facing errors say "identity" not "designation"
      --attach errors: "no agent found with identity 'foo'"

all files:
  plugins/hub/plugin.py
  plugins/hub/presence.py
  plugins/hub/feed.py
  plugins/hub/models.py
  plugins/hub/vault.py
  plugins/hub/messenger.py
  plugins/hub/coordinator.py
  plugins/hub/org_launcher.py
  plugins/hub/notifier.py
  plugins/hub/task_ledger.py
  plugins/hub/messaging_bridge.py
  kollabor/cli.py
  kollabor/application.py
  kollabor/attach_client.py
  packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py
  packages/kollabor-ai/src/kollabor_ai/prompt_renderer.py
  packages/kollabor-agent/src/kollabor_agent/runtime.py
  packages/kollabor-agent/src/kollabor_agent/agent_manager.py
  packages/kollabor-agent/src/kollabor_agent/mentiko_adapter.py
  packages/kollabor-engine/src/kollabor_engine/routes/hub.py
  packages/kollabor-engine/src/kollabor_engine/routes/hub_ws.py
  packages/kollabor-engine/src/kollabor_engine/hub_bridge.py
  plugins/agent_orchestrator/plugin.py
  plugins/altview/hub_console_altview.py
  agents/system/hub-collaboration.md
  bundles/agents/system/hub-collaboration.md
  bundles/agents/*/agent.json
  plugins/hub/organizations/*.json
  docs/ (any reference to designation)
