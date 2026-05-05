---
title: "Agent Pool System, Project-Scoped Agents, and Project-Scoped Working Memory"
doc_type: architecture-rfc
created: 2026-04-14
modified: 2026-04-15
status: revised-spec
author: maintainer + opus + review team
depends on:
  - kollabor/cli.py (--as flag parsing)
  - plugins/hub/models.py (GemIdentity, GEM_IDENTITIES, DESIGNATION_POOL, GEM_BY_NAME, ROLE_TO_GEM)
  - plugins/hub/coordinator.py (IdentityAssigner)
  - plugins/hub/plugin.py (_start_hub identity flow, hub_spawn handling)
  - plugins/hub/feed.py (GEM_BY_NAME color lookup)
  - plugins/hub/org_launcher.py (organization launching)
  - plugins/altview/hub_console_altview.py (GEM_BY_NAME sidebar colors)
  - plugins/agent_orchestrator/orchestrator.py (subprocess spawning)
  - packages/kollabor-agent/src/kollabor_agent/agent_manager.py (bundle discovery)
  - tests/test_hub_modules.py (hub regression coverage)
supersedes:
  - hardcoded gems.json pool (current)
  - plugins/hub/models.py module-level DESIGNATION_POOL constant
---

## problem statement

The agent identity system is tightly coupled to a single hardcoded gem
pool (24 gem names in `gems.json`). This creates four problems:

1. **No project-scoped agents.** A marketing project can't define its own
   agent pool with marketing-specific roles. The same 24 gems are used
   everywhere regardless of project context.

2. **No pool customization.** Users who want animals, mythical creatures,
   or custom names instead of gems have to manually replace `gems.json`
   globally — affecting all projects.

3. **No agent-type binding in orgs/spawns.** When koordinator spawns
   `research`, a gem identity is assigned but there's no way to say
   "spawn lapis specifically as a research agent" or define a pool where
   lapis defaults to the `research` bundle.

4. **Vault working memory bleeds across projects.** Vault/crystal memory
   is currently keyed by identity name, not project. If lapis works in
   `kollab` and then in `sprouts`, project-specific recent context
   can bleed between repos and confuse the agent.

The vision: projects define their own agent pools with domain-specific
identities, each identity bound to an agent type (bundle) and skill set.
A marketing project has `strategist`, `copywriter`, `analyst` identities
that spawn with the corresponding agent bundles and skills. At the same
time, long-term learned patterns should remain portable while project-
specific working memory stays isolated per repo.


## current architecture (what exists today)

### identity pool

```
plugins/hub/models.py
  GemIdentity:
    name: str           "lapis"
    color_rgb: tuple    (30, 90, 180)
    role_aliases: list  ["herald", "nexus", "messenger"]
    personality: str    "calm mediator, bridges gaps"
    caste: str          "communication"

  load order:
    1. ~/.kollab/hub/organizations/gems.json    (user override)
    2. plugins/hub/organizations/gems.json            (bundled)
    3. _hardcoded_gem_identities()                    (code fallback)

  module-level constants (set at import time):
    GEM_IDENTITIES: List[GemIdentity]    = _load_gem_identities()
    DESIGNATION_POOL: List[str]          = [g.name for g in GEM_IDENTITIES]
    GEM_BY_NAME: Dict[str, GemIdentity]  = {g.name: g for g ...}
    ROLE_TO_GEM: Dict[str, str]          = {alias: g.name for g ...}
```

### identity assignment

```
plugins/hub/coordinator.py :: IdentityAssigner.assign(taken, preferred)

  priority:
    1. preferred (if not in taken) → return immediately
    2. first available from DESIGNATION_POOL
    3. numbered variants: lapis-2, sapphire-2, ... lapis-3, ...
    4. fallback: agent-{pid}
```

### hub startup identity resolution

```
plugins/hub/plugin.py :: _start_hub() lines 2882-2962

  preferred identity resolved via priority chain:
    1. --as CLI flag               (highest)
    2. agent.json "identity" field OR agent bundle name (if != "default")
    3. plugins.hub.identity config key
    4. auto-assign from pool       (lowest)

  then: identity = IdentityAssigner.assign(taken, preferred)
```

Important bug in current code:
- `kollabor/cli.py` defines `--as` with `dest="identity"`
- `plugins/hub/plugin.py` checks `self._cli_args.as_identity`
- Result: `--as` is currently dead code and must be fixed as part of
  this work.

### organizations

```
plugins/hub/org_launcher.py

  org JSON format:
    {
      "name": "engineering",
      "description": "...",
      "director": {
        "identity": "director",
        "role": "Engineering Director",
        "agent_bundle": "default",
        "prompt": "...",
        "reports_to": null
      },
      "teams": [{
        "name": "platform",
        "manager": { identity, role, agent_bundle, prompt, reports_to },
        "members": [{ identity, role, agent_bundle, prompt, reports_to }]
      }]
    }

  org identities are HARDCODED in the JSON (e.g. "director",
  "backend-eng"). they don't come from the gem pool.

  launch: kollab --detached --as {identity} --agent {bundle} --system-prompt {file}
  staggered: 2s between agents to reduce registration races
```

### agent bundles

```
bundles/agents/{name}/
  agent.json:
    description: str
    identity: str (optional)
    tools: List[str]         granted tool names
    skills: List[str]        available skill names
    vault_enabled: bool

  discovery priority:
    1. .kollab/agents/{name}/    (project-local, highest)
    2. ~/.kollab/agents/{name}/  (user global)
    3. bundles/agents/{name}/          (bundled default)
```

### vault memory

```
current vault layout:
  ~/.kollab/hub/vaults/{identity}/
    crystal.jsonl / crystallized.md    # long-term memory
    meta.json                          # global metadata
    stream.jsonl                       # recent event log
    working_memory.md                  # session/project context summary
```

Problem:
- `stream.jsonl` and `working_memory.md` are keyed only by identity
- same identity across multiple repos shares recent/project-specific
  context
- this is the direct cause of cross-project confusion

### what's broken

| problem | root cause |
|---------|-----------|
| pool is global, not per-project | DESIGNATION_POOL is a module-level constant loaded from one file |
| no project-scoped pool | load path doesn't check `.kollab/hub/pool.json` |
| pool file hardcoded to gems.json | `_load_gem_identities()` only looks for `gems.json` |
| no config to select pool | no `plugins.hub.pool_file` or `plugins.hub.pool_name` key |
| orgs don't bind identity to agent type | org JSON has `agent_bundle` but it's not the same as the pool concept |
| hub_spawn can't request specific identity | no `identity` param on `<hub_spawn>` |
| spawned agents don't get pool metadata | no personality/color/caste injected from pool into spawned agent |
| `--as` flag is broken | CLI arg dest doesn't match hub plugin lookup |
| working memory bleeds across projects | vault stream + working memory are scoped by identity only |


## proposed architecture

### 1. rename and generalize the pool file

**Current:** `gems.json` with `GemIdentity` dataclass.
**Proposed:** `pool.json` with `PoolIdentity` dataclass.

```python
@dataclass
class PoolIdentity:
    name: str               # "lapis" or "strategist" or "wolf"
    color_rgb: tuple        # (30, 90, 180)
    role_aliases: list      # ["herald", "nexus"]
    personality: str        # "calm mediator"
    caste: str              # "communication" or custom
    agent_type: str = ""    # optional default bundle for this identity
    skills: list = field(default_factory=list)  # optional extra skills

GemIdentity = PoolIdentity  # explicit backward-compat alias
```

The new `agent_type` field lets the pool define a default bundle per
identity. If the spawner did not specify an agent type, the pool's
`agent_type` wins. If the spawner DID specify a type, the spawner wins.

The `skills` field lets the pool attach extra skills per identity.

### 2. pool file schema and backward compatibility

Current bundled `gems.json` uses this schema:

```json
{
  "name": "gems",
  "description": "...",
  "gems": [ ... ]
}
```

Proposed `pool.json` uses this schema:

```json
{
  "name": "marketing-team",
  "description": "optional",
  "identities": [ ... ]
}
```

Loader requirements:
- `pool.json` must load `identities`
- legacy `gems.json` must load `gems`
- if both keys exist, `identities` wins
- if both `pool.json` and `gems.json` exist at the same level,
  `pool.json` wins and `gems.json` is ignored with a warning

### 3. pool validation (required)

Pool loading must validate before constructing `PoolIdentity` objects.
Invalid pool files must degrade gracefully to the next resolution level,
not crash the hub.

Validation rules:
- file must be valid JSON
- pool must contain at least 1 identity
- identity names must be unique within a pool
- identity names must match `^[a-z][a-z0-9-]*$`
- reserved numbered-variant pattern must be handled:
  - either reject real identities ending in `-<number>`
  - or explicitly reserve those names from auto-numbering
- `color_rgb` must be a 3-item list/tuple of ints 0-255
- required fields: `name`, `color_rgb`, `role_aliases`, `personality`, `caste`
- `agent_type`, if set, must resolve to an existing agent bundle or emit
  a warning and fall back safely
- `skills`, if set, follow existing `AgentManager` behavior for missing
  skill names (documented during implementation)

Failure behavior:
- invalid project-local pool → warning, fall back to global
- invalid global pool → warning, fall back to bundled
- invalid bundled pool → warning, fall back to hardcoded gems
- empty/duplicate/invalid pool never becomes active

### 4. pool resolution order (project-scoped)

```text
load order (highest to lowest priority):
  1. .kollab/hub/{pool_file}                    (project-local)
  2. .kollab/hub/organizations/gems.json        (project-local legacy)
  3. ~/.kollab/hub/{pool_file}                  (user global)
  4. ~/.kollab/hub/organizations/gems.json      (user global legacy)
  5. plugins/hub/organizations/pool.json              (bundled default)
  6. plugins/hub/organizations/gems.json              (bundled legacy)
  7. _hardcoded_gem_identities()                      (code fallback)
```

Notes:
- this preserves existing user overrides under the old
  `organizations/gems.json` path
- `.kollab/hub/` is optional; loader must treat missing directories
  as "not found," not as an error
- `pool_file` is a filename, not an arbitrary path

### 5. config keys

```json
{
  "plugins": {
    "hub": {
      "pool_file": "pool.json",
      "pool_name": ""
    }
  }
}
```

Config behavior:
- `pool_file`: filename only, default `pool.json`
- if configured file is missing at a given resolution level, continue to
  next resolution level
- `pool_name`: optional display override for status/logging; if empty,
  use pool file's `name`

### 6. dynamic pool loading

**Current:** `DESIGNATION_POOL` is a module-level constant set at import time.
**Proposed:** pool loaded at hub startup and passed to `IdentityAssigner`.

```python
# coordinator.py
class IdentityAssigner:
    def __init__(self, pool: List[str]):
        self._pool = pool
        self._hub_dir = get_hub_dir()

    def assign(self, taken: List[str], preferred: str = "") -> str:
        # same algorithm but uses self._pool instead of DESIGNATION_POOL
```

```python
# plugin.py :: _start_hub()
pool_identities = load_pool_identities(config.pool_file)
self._pool_identities = pool_identities
pool_names = [p.name for p in pool_identities]
self._designator = IdentityAssigner(pool_names)
```

No more module-level `DESIGNATION_POOL` usage in runtime assignment.

Important impact:
- `plugins/hub/feed.py` currently imports `GEM_BY_NAME`
- `plugins/altview/hub_console_altview.py` currently imports `GEM_BY_NAME`
- both must be updated in the same change set when module-level lookup
  strategy changes

### 7. project-scoped pool example

```text
~/dev/marketing-project/
  .kollab/
    hub/
      pool.json              <-- project pool
    agents/
      strategist/
        agent.json
        system_prompt.md
      copywriter/
        agent.json
        system_prompt.md
      analyst/
        agent.json
        system_prompt.md
```

**pool.json:**
```json
{
  "name": "marketing-team",
  "identities": [
    {
      "name": "strategist",
      "color_rgb": [70, 130, 200],
      "role_aliases": ["planner", "lead"],
      "personality": "big picture thinker, connects market trends to product decisions",
      "caste": "leadership",
      "agent_type": "strategist",
      "skills": ["market-research", "competitor-analysis"]
    },
    {
      "name": "copywriter",
      "color_rgb": [200, 100, 150],
      "role_aliases": ["writer", "content"],
      "personality": "creative wordsmith, adapts tone to audience",
      "caste": "creative",
      "agent_type": "copywriter",
      "skills": ["content-writing", "seo-optimization"]
    },
    {
      "name": "analyst",
      "color_rgb": [100, 200, 100],
      "role_aliases": ["data", "metrics"],
      "personality": "numbers-driven, finds patterns others miss",
      "caste": "intelligence",
      "agent_type": "data-analyst",
      "skills": ["data-visualization", "ab-testing"]
    }
  ]
}
```

When a user runs `kollab --agent koordinator` in this project:
- hub loads `.kollab/hub/pool.json`
- pool has 3 identities: strategist, copywriter, analyst
- koordinator spawns agents from THIS pool
- assigned identities can default to their pool `agent_type`
- extra `skills` are passed through the spawn pipeline

### 8. hub_spawn with identity parameter

Extend hub_spawn to accept an optional identity:

```xml
<!-- auto-assign from pool (current behavior) -->
<hub_spawn name="research">audit the repo</hub_spawn>

<!-- request specific identity -->
<hub_spawn name="research" identity="lapis">audit the repo</hub_spawn>
```

When `identity` is specified:
- check if identity exists in current pool
- check if identity is available (not taken/reserved)
- if available: spawn with `--as {identity} --agent {name}`
- if taken: error with `identity '{identity}' is already in use`
- if not in pool: error with `identity '{identity}' not in pool`

### 9. full spawn pipeline changes (explicit call chain)

This is the complete call chain that must be updated. Previous drafts
only traced part of this path.

Current flow:
1. XML tag registration in `plugin.py :: _register_pipeline_tags`
2. `_extract_hub_spawn()` parses `name` + `task`
3. `_handle_hub_spawn_tool()` receives extracted data
4. `_handle_spawn_command(f"{name} {task}")`
5. orchestrator `spawn()`
6. orchestrator `_create_session()`
7. subprocess launch with `kollab --agent ...`

Required changes:
1. extend hub_spawn regex/extractor to capture optional `identity`
2. thread `identity` through extracted tool data
3. update `_handle_hub_spawn_tool()` to validate `identity`
4. stop collapsing everything to a string-only `_handle_spawn_command`
   interface, or extend that interface to carry structured identity data
5. pass `identity`, effective `agent_type`, and effective `skills`
   through to `orchestrator.spawn()`
6. forward `identity` to subprocess as `--as {identity}`
7. forward extra skills to subprocess as `--skill ...`

Without all 7 steps, phase 3/4 is incomplete.

### 10. identity reservation (required to avoid races)

Current race:
- coordinator assigns identity A based on current `taken` snapshot
- spawned agent writes presence file later during startup
- another concurrent spawn can assign the same identity in the gap

Required fix:
- add identity reservation at assignment time
- reservation written immediately by coordinator/hub before subprocess starts
- `assign()` must consider both live presence and active reservations
- stale reservations cleaned on startup / timeout

This is required for simultaneous `hub_spawn` calls. The 2s stagger in
`org_launcher.py` is not enough because XML-driven hub spawns do not use
that stagger.

### 11. org integration with pools

Orgs currently define their own hardcoded identities (e.g. `director`,
`backend-eng`). This is fine for orgs that want full control. But orgs
should also be able to reference pool identities:

```json
{
  "name": "marketing-campaign",
  "pool": "marketing-team",
  "director": {
    "identity": "strategist",
    "role": "Campaign Director",
    "agent_type": "strategist"
  },
  "teams": [{
    "name": "content",
    "manager": {
      "identity": "copywriter",
      "role": "Content Lead",
      "agent_type": "copywriter"
    },
    "members": [{
      "identity": "analyst",
      "role": "Performance Analyst",
      "agent_type": "data-analyst"
    }]
  }]
}
```

When `pool` is specified in an org:
- identities are resolved against that pool for color/personality
- if org agent does not specify `agent_type`, pool `agent_type` may fill it
- if identity is not in the pool, fall back to current custom-identity
  behavior

### 12. project-scoped working memory in vaults

Team consensus: long-term learned patterns should remain portable, but
recent/project-specific context must be scoped per repo.

Scope model:
- global/shared:
  - `crystal.jsonl` / crystallized memory
  - `meta.json`
- project-scoped:
  - `stream.jsonl`
  - `working_memory.md`

Proposed layout:

```text
~/.kollab/hub/vaults/
  lapis/
    crystal.jsonl
    meta.json
    stream.jsonl           # legacy fallback only
    working_memory.md      # legacy fallback only
    projects/
      Users_example_dev_kollab/
        stream.jsonl
        working_memory.md
      Users_example_dev_sprouts/
        stream.jsonl
        working_memory.md
```

Implementation sketch:
- `AgentVault.__init__` keeps global vault dir for crystal/meta
- add `project_hash` using existing encoded-path scheme already used in
  `~/.kollab/projects/`
- set:
  - `self._crystal_path` and `self._meta_path` at global identity level
  - `self._stream_path` and `self._working_path` in
    `vaults/{identity}/projects/{project_hash}/`
- downstream methods keep using those paths without API changes

Behavior:
- default `vault_write` goes to current project scope unless explicitly
  promoted/globalized during implementation
- session/rebirth context loads:
  1. global crystallized memory
  2. project-specific working memory
  3. project-specific recent stream
- existing global `stream.jsonl` and `working_memory.md` remain as legacy
  fallback to avoid migration pain

This resolves the current cross-project bleed and also resolves the vault
collision concern introduced by project-scoped custom identity pools.

### 13. backward compatibility

- bundled `gems.json` ships alongside new bundled `pool.json`
- legacy override paths remain in load order:
  - `.kollab/hub/organizations/gems.json`
  - `~/.kollab/hub/organizations/gems.json`
- `GemIdentity` becomes an explicit alias of `PoolIdentity`
- `hub_spawn` without `identity` works exactly as today
- org JSON files continue to work unchanged; `pool` field is optional
- existing global vault crystal/meta remain untouched
- existing global stream/working memory files remain as fallback so no
  data is lost during rollout


## implementation plan

Important dependency correction:
- phase 1 and phase 2 must ship together
- phase 3 and phase 4 must ship together
- phase 5 can be standalone after that
- phase 6 docs/migration comes after previous phases are real
- phase 7 adds project-scoped vault working memory

### phase 1+2: pool generalization + project-scoped pool loading

> estimated: ~300 lines changed across 6+ files

1. **Rename dataclass and preserve compatibility** (`plugins/hub/models.py`)
   - `GemIdentity` → `PoolIdentity`
   - add `agent_type` and `skills`
   - explicitly set `GemIdentity = PoolIdentity`
   - rename `_load_gem_identities()` → `load_pool_identities(pool_file="")`

2. **Add validation + dual-schema loading** (`plugins/hub/models.py`)
   - support `identities` key for `pool.json`
   - support `gems` key for legacy `gems.json`
   - validate malformed/empty/duplicate/bad-name/bad-color pools
   - fall back safely on failure

3. **Add project-local + legacy path resolution** (`plugins/hub/models.py`)
   - check new project-local/global pool paths
   - also check old `organizations/gems.json` paths
   - `pool.json` wins over `gems.json` at same level

4. **Make pool dynamic** (`plugins/hub/models.py`, `plugins/hub/coordinator.py`)
   - remove runtime dependence on module-level `DESIGNATION_POOL`
   - `IdentityAssigner.__init__(pool)` accepts pool list
   - `IdentityAssigner.assign()` uses `self._pool`

5. **Fix `--as` bug + add config keys** (`kollabor/cli.py`, `plugins/hub/plugin.py`)
   - align CLI arg dest with hub plugin lookup
   - add `plugins.hub.pool_file`
   - add `plugins.hub.pool_name`

6. **Update hub startup + color lookups**
   - `plugins/hub/plugin.py` loads pool once at startup
   - `plugins/hub/feed.py` updated for new pool lookup path
   - `plugins/altview/hub_console_altview.py` updated for new pool lookup path
   - pool metadata stored on plugin instance

7. **Bundled default files**
   - add `plugins/hub/organizations/pool.json`
   - keep `plugins/hub/organizations/gems.json` for backward compatibility

8. **Tests**
   - update/add regression coverage in `tests/test_hub_modules.py`
   - verify existing gem pool behavior remains unchanged
   - verify resolution order: project > project legacy > user > user legacy > bundled > bundled legacy > hardcoded

### phase 3+4: identity-bound agent types + hub_spawn identity support

> estimated: ~150 lines changed across 3+ files

1. **Complete spawn pipeline threading**
   - update `_extract_hub_spawn()`
   - update `_handle_hub_spawn_tool()`
   - update `_handle_spawn_command()` or replace string-only handoff
   - update `orchestrator.spawn()`
   - update subprocess command construction

2. **Pool identity → agent type binding**
   - pool `agent_type` used when spawner did not specify type
   - spawner-specified type wins over pool default

3. **Pool identity → extra skills**
   - forward effective skills through spawn pipeline
   - document/verify missing-skill behavior against `AgentManager`

4. **Identity validation + reservation**
   - explicit identity must exist in current pool
   - explicit identity must not be taken/reserved
   - reservation created before subprocess start

5. **Tests**
   - hub_spawn XML with identity attribute
   - spawn with explicit identity
   - identity already taken/reserved
   - pool `agent_type` vs explicit type priority
   - extra skills forwarding

### phase 5: org pool integration

> estimated: ~60 lines changed in `plugins/hub/org_launcher.py`

1. add optional `pool` field to org JSON format
2. resolve org identities against pool for color/personality
3. use pool `agent_type` if org agent did not specify one
4. retain current custom-identity behavior if not in pool
5. add org integration tests

### phase 6: documentation & migration docs

1. update `CLAUDE.md` hub section with pool docs
2. update relevant agent prompt docs / sections that describe spawning
3. update koordinator prompt step 4 with pool concepts
4. write user-facing docs: `docs/features/agent-pools.md`
5. write migration guide: `gems.json` → `pool.json`
6. document legacy path fallback behavior and `pool.json` precedence

### phase 7: project-scoped vault working memory

> estimated: ~small, targeted path-scoping change

1. update vault path construction in `AgentVault.__init__`
2. keep crystal/meta global
3. move stream/working memory to project-scoped subdirs
4. keep old global stream/working as legacy fallback
5. verify rebirth/session context uses project-scoped recent context
6. add regression coverage for two repos sharing the same identity name


## file impact summary

| file | phase | changes |
|------|-------|---------|
| kollabor/cli.py | 1+2 | fix `--as` arg destination / lookup alignment |
| plugins/hub/models.py | 1+2 | PoolIdentity dataclass, validation, dual-schema loading, dynamic pool support |
| plugins/hub/coordinator.py | 1+2 | IdentityAssigner accepts pool param |
| plugins/hub/plugin.py | 1+2, 3+4 | pool loading at startup, identity-type binding, hub_spawn identity threading |
| plugins/hub/feed.py | 1+2 | update color lookup away from stale module-level GEM_BY_NAME assumptions |
| plugins/altview/hub_console_altview.py | 1+2 | update sidebar color lookup away from stale module-level GEM_BY_NAME assumptions |
| plugins/hub/organizations/pool.json | 1+2 | bundled default pool file |
| plugins/hub/organizations/gems.json | 1+2 | legacy bundled fallback retained |
| plugins/agent_orchestrator/orchestrator.py | 3+4 | identity param, effective type/skills forwarding |
| plugins/hub/org_launcher.py | 5 | optional pool field, identity resolution, pool defaults |
| tests/test_hub_modules.py | 1+2, 3+4, 7 | regression coverage for hub identity/pool/vault behavior |
| agent prompt docs / sections | 6 | updated spawn and pool docs |
| docs/features/agent-pools.md | 6 | user-facing docs |
| vault implementation files | 7 | project-scoped stream + working memory paths |


## deferred / non-blocking follow-ups

These came up in review but do not block this corrected spec:

1. pool size limits for extremely large pools
2. pool file schema version field
3. stricter config validation for `pool_name`
4. coordinator-only identity reservation policies


## acceptance criteria

The spec is only considered complete when all of the following are true:

- existing gem behavior still works unchanged by default
- old `organizations/gems.json` overrides still load
- new `pool.json` overrides win when present
- malformed/empty/duplicate pool files never crash the hub
- `--as` works again
- `feed.py` and `hub_console_altview.py` continue rendering colors correctly
- explicit `hub_spawn identity="..."` works end-to-end
- simultaneous spawns do not double-assign an identity
- project A and project B can both use `lapis` without sharing working memory
- crystallized memory remains portable across projects
