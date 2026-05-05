---
title: "Architecture Review: Agent Designation Merge"
doc_type: architecture-review
created: 2026-04-06
modified: 2026-04-06
status: historical
---
# Architecture Review: Agent Designation Merge

**Spec:** ../../rfcs/RFC-2026-04-06-agent-designation-merge.md
**Reviewer:** Claude Opus
**Date:** 2026-04-06

---

## TL;DR: DO NOT IMPLEMENT AS WRITTEN

This spec has 23+ issues ranging from trivial to catastrophic. The core idea is sound (unify UX, keep architecture separate), but the execution plan contradicts itself, breaks working features, ignores critical edge cases, and will cause data loss.

---

## 1. CONTRADICTIONS

### 1.1 "default designation = agent name" vs "gem pool only used when org launcher needs unique names"

**Spec claims:**
- Section 1: "if no --designation and no org mode, designation = agent name"
- Section 5: "gem pool only used when org launcher needs unique names"

**Reality:**
This is internally inconsistent. If the default is `designation = agent name`, the gem pool is NEVER used in solo mode. But the org launcher will still collide if it tries to assign meaningful role names (like "director") and a solo agent is already running with `agent=director`.

**The spec contradicts itself:**
- Says solo agents never see gem names
- But org launcher needs gem names for "unique names" (section 5)
- What happens when org launcher spawns an agent with `agent=coder` and there's already a solo `coder` running?

### 1.2 "keep the architecture separate" vs "vault keyed by agent name"

**Spec claims:**
- "keep the architecture separate, merge the UX" (design principle)
- "vault keyed by agent name in solo mode" (section 3)

**Reality:**
The vault IS the architecture. The vault path is `~/.kollab/hub/vaults/{designation}/`. Keying it by agent name instead of designation breaks the architecture separation the spec claims to preserve.

**Code reference:** `plugins/hub/vault.py:38`
```python
self._vault_dir = get_vaults_dir() / designation
```

If you change this to `agent_name`, every vault lookup breaks. The vault is fundamentally keyed by designation. Changing this is NOT cosmetic.

### 1.3 "gem pool only used when org launcher needs unique names" vs current implementation

**Spec claims:**
- "solo user never sees a gem name" (section 1)

**Reality:**
The spec doesn't account for what happens when a solo user runs:
```bash
kollab --agent coder      # First time: designation=coder
kollab --agent coder      # Second time (while first still running): ???
```

Currently, the DesignationAssigner (coordinator.py:137) handles this by assigning from the gem pool when the preferred designation is taken. The spec's "default designation = agent name" breaks this fallback.

**Code reference:** `plugins/hub/coordinator.py:147-161`
```python
if preferred and preferred not in taken:
    return preferred

for name in DESIGNATION_POOL:
    if name not in taken:
        return name
```

### 1.4 "hide --designation from basic help" vs "still functional"

**Spec claims:**
- "move to advanced group or hide behind --help-advanced"
- "still functional, just not in the default help output"

**Reality:**
If it's still functional, users will discover it and use it. Then you're back to the "two identity systems" problem. Hiding it from help doesn't solve UX confusion.

Also, there IS no `--help-advanced` in the current CLI. This requires new infrastructure.

---

## 2. MISSING EDGE CASES

### 2.1 Two agents with the same name (DEFAULT AGENT)

**Critical gap:** The spec says "default designation = agent name". What happens when:

```bash
kollab --agent coder      # Terminal 1: designation=coder
kollab --agent coder      # Terminal 2: designation=???
```

**Current behavior:** Second agent gets `lapis`, `peridot`, etc. from gem pool.

**Spec behavior:** UNDEFINED. The spec doesn't say what happens when the preferred designation (agent name) is taken.

**Code reference:** `plugins/hub/plugin.py:520-569`
The current logic has a fallback path (lines 530-536) but the spec's "default to agent name" removes this.

### 2.2 Agent with no name (default agent)

**Gap:** What about `kollab` with no `--agent` flag? The active_agent is `default`. Does designation become `default`? Is that a valid gem name?

**Code reference:** `plugins/hub/plugin.py:535-536`
```python
elif active_agent.name and active_agent.name != "default":
    preferred = active_agent.name
```

The current code EXPLICITLY checks for `!= "default"`. The spec doesn't mention this case.

### 2.3 Agent name collision with org chart designations

**Gap:** What if a solo user has an agent named `director` and then runs `--org engineering.json` which also uses `designation: "director"`?

**Collision scenario:**
1. User runs `kollab --agent director` (solo mode)
2. User runs `kollab --org engineering.json` (org mode has director role)
3. Both want designation=`director`

**Spec behavior:** UNDEFINED. The org launcher doesn't have collision handling for this.

**Code reference:** `plugins/hub/org_launcher.py:224-236`
```python
designation = agent["designation"]
# ... no collision check here
self._launch_agent(designation, prompt, initial_message, bundle)
```

### 2.4 Org chart with duplicate designations

**Gap:** What if an org chart has TWO roles with the same designation?

```json
{
  "teams": [
    {
      "name": "backend",
      "manager": {"designation": "eng", "role": "Backend Lead"},
      "members": [
        {"designation": "eng", "role": "Backend Engineer 1"},
        {"designation": "eng", "role": "Backend Engineer 2"}
      }
    }
  ]
}
```

**Current behavior:** DesignationAssigner handles this with numbered fallbacks (`eng-2`, `eng-3`).

**Spec behavior:** UNDEFINED. The spec says "org charts define explicit designations per role" but doesn't say what happens on collision.

**Code reference:** `plugins/hub/coordinator.py:154-159`
```python
for i in range(2, 100):
    for name in DESIGNATION_POOL[:5]:
        candidate = f"{name}-{i}"
```

### 2.5 Vault migration for agents that never had designations

**Gap:** The spec says:
> "if agent.json has designation=gem-name, create symlink"

But what about agents that NEVER had a designation field? Old agents may have:
- `agent.json` with NO `designation` key
- Vault at `vaults/lapis/` (auto-assigned from gem pool)
- No record of which gem they got

**Migration behavior:** BROKEN. You can't create the symlink because you don't know the old gem name.

### 2.6 Team mode detection

**Gap:** The spec says:
> "in team mode (org active), show 'agent (designation)'"

How do you DETECT "team mode"? Is it:
- `org_name != ""` in AgentRuntime?
- `reports_to != ""`?
- Presence of `--org` flag at startup?

**Code reference:** `packages/kollabor-agent/src/kollabor_agent/runtime.py:232-243`
```python
org_name: str = ""
org_role: str = ""
org_level: str = ""
team_name: str = ""
reports_to: str = ""
```

There are 5 different "team mode" indicators. Which one(s) define it?

### 2.7 Message routing when designation != agent name

**Gap:** The spec says:
> "hub_msg to='agent-name' works (resolves to designation)"

But how? The messenger uses designation for routing. If I send to "jarvis" but jarvis's designation is "lapis", how does it resolve?

**Code reference:** `plugins/hub/messenger.py`
The messenger sends to socket paths like `/tmp/kollabor-hub/{agent_id}.sock`. It doesn't have name->designation resolution.

### 2.8 Attach client resolution

**Gap:** The spec says:
> "--attach takes agent name, resolves to designation"

But the attach client (attach_client.py:28) takes `designation` directly and uses it for display. There's no name resolution layer.

**Code reference:** `kollabor/attach_client.py:28-32`
```python
def __init__(
    self,
    socket_path: str,
    designation: str,
    interactive: bool = False,
):
```

And `kollabor/application.py:111-135` resolves `attach_to` to a socket by iterating over presence files and checking `designation` field. It doesn't check agent names.

### 2.9 Status bar: what to show when agent name is very long

**Gap:** Agent names can be long (`lint-editor-with-rules`). Designations are short gem names (`lapis`). The status bar widget uses middle truncation, but:

**Code reference:** `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py:411-436`
```python
def render_hub(width: int, ctx: Optional[WidgetContext]) -> str:
    # ...
    designation = identity.designation or "?"
    # ...
    name = _fg(designation, T().text)
```

This renders DESIGNATION, not agent name. The spec says "show agent name" but the code doesn't have access to agent name here (only `hub._identity.designation`).

### 2.10 "solo mode" vs "team mode" in the same process

**Gap:** Can an agent start solo and later join an org? Can it transition? The spec treats them as binary but doesn't say what happens on transition.

---

## 3. FEASIBILITY ISSUES

### 3.1 "hub_msg to='agent-name' works (resolves to designation)"

**Claim:** Section 8 says messaging will resolve agent names to designations.

**Reality:** This requires a NEW resolution layer. The current hub protocol:
1. Sender creates `HubMessage(to="designation")`
2. Messenger looks up socket by designation in presence files
3. Sends to socket

To support name resolution, you need:
- Registry mapping agent_name -> designation
- Lookup on every send
- Conflict handling (what if two agents have same name?)

**Code reference:** `plugins/hub/presence.py`
Presence files store `designation` field. There's no `agent_name` field.

### 3.2 "remove designation field from agent.json"

**Claim:** Section 11 says "remove designation field from agent.json (use agent dir name)"

**Reality:** This breaks org launcher, which needs explicit designations per role.

**Code reference:** `plugins/hub/org_launcher.py:225`
```python
designation = agent["designation"]
```

Where does this come from if not agent.json? The org chart JSON has a `designation` field. Are we supposed to:
1. Remove designation from agent.json but keep it in org.json? (confusing)
2. Remove it from both and use agent name everywhere? (breaks role-based designations)

### 3.3 "hide --designation from basic help"

**Claim:** Section 2 says "move to advanced group or hide behind --help-advanced"

**Reality:** The CLI parser (cli.py) uses argparse. There's no built-in "advanced group" feature. You'd need to:
1. Create a custom help formatter
2. Or maintain two separate argument parsers
3. Or use a hidden flag that only shows with `--help-all`

This is non-trivial work for questionable UX benefit.

### 3.4 "org charts can use any designation string, not just gems"

**Claim:** Section 5 says "gems provide color themes, not identity"

**Reality:** The gem system includes:
- Name (`lapis`)
- Color RGB (`(30, 90, 180)`)
- Role aliases (`["herald", "nexus", "messenger"]`)
- Personality string
- Caste (`communication`)

If org charts use "any designation string", where do colors come from?
- Hash the designation? (ugly, unpredictable)
- Default to gray? (boring)
- Require explicit color in org JSON? (more data to maintain)

**Code reference:** `plugins/hub/models.py:42-217`
The gem designations have rich metadata. Arbitrary strings don't.

---

## 4. BREAKAGE (What the spec doesn't mention)

### 4.1 Vault data loss

**Spec says:** "create symlink: vaults/{agent-name}/ -> vaults/{gem-name}/"

**What breaks:** If agent.json NEVER had a designation field, you can't find the old vault. The user loses all their vault data.

**Scenario:**
1. User runs `kollab --agent jarvis` (old behavior: gets designation=`lapis` from gem pool)
2. Agent creates vault at `vaults/lapis/`
3. User writes agent.json with NO designation field (because they didn't know about it)
4. New code runs: `designation = agent.name = "jarvis"`
5. New code creates NEW vault at `vaults/jarvis/`
6. Old vault at `vaults/lapis/` is orphaned
7. User loses all their vault data

### 4.2 Existing org charts break

**Spec says:** "org launcher uses explicit designations per role"

**What breaks:** Existing org charts that RELY on gem pool assignment for duplicate roles.

**Example org:**
```json
{
  "teams": [
    {
      "name": "backend",
      "members": [
        {"agent": "coder", "role": "API Engineer"},
        {"agent": "coder", "role": "Service Engineer"}
      ]
    }
  ]
}
```

If both use `agent: "coder"` and designation defaults to `coder`, they collide. Current gem pool fallback (`coder-2`) is removed by spec.

### 4.3 Hub roster display breaks

**Spec says:** "show agent name by default, show designation only when it differs"

**What breaks:** The presence system ONLY stores designation. It doesn't know agent names.

**Code reference:** `plugins/hub/presence.py`
Presence files are JSON like:
```json
{
  "agent_id": "...",
  "designation": "lapis",
  "pid": 12345,
  "socket_path": "/tmp/..."
}
```

There's NO `agent_name` field. To show agent names, you need to:
1. Add `agent_name` to presence files
2. Add `agent_name` to AgentRuntime
3. Update all roster display code
4. Handle migration (old presence files without agent_name)

### 4.4 Status bar widget breaks

**Spec says:** "hub widget shows agent name in solo mode"

**What breaks:** The hub widget (core_widgets.py:411) only has access to `hub._identity.designation`. It doesn't have the agent name.

To fix, you need to:
1. Pass agent_manager to the widget context
2. Add agent_name to hub identity
3. Update widget rendering logic

### 4.5 Attach client breaks

**Spec says:** "--attach takes agent name, resolves to designation"

**What breaks:** The current attach resolution (application.py:111-135) iterates presence files and matches on `designation`. To match on `agent_name`, you need agent_name in presence files (see 4.3).

### 4.6 Engine API breaks

**Spec says:** "REST/WebSocket routes accept agent name OR designation"

**What breaks:** The engine API (not fully implemented yet) would need duplicate routes or resolution logic for every endpoint.

### 4.7 AgentRuntime serialization breaks

**Spec says:** "designation field preserved internally for routing"

**What breaks:** AgentRuntime (runtime.py:199-204) has BOTH `name` (agent name) and `designation`. Serialization keeps both. But if they're always the same in solo mode, why serialize both?

Wasted space, confusion over which to use, potential for drift.

### 4.8 Gem alias system becomes useless

**Spec says:** "gems provide color themes, not identity"

**What breaks:** The `ROLE_TO_GEM` mapping (models.py:224-227) lets agents use role aliases as designations:
- "herald" -> "lapis"
- "forger" -> "bismuth"
- etc.

If gems are "cosmetic only" and org charts use "any designation string", the alias system dies. Or it becomes a color lookup only, which defeats the purpose.

---

## 5. ORDERING (Dependencies the spec gets wrong)

### 5.1 Section 3 (vault path) MUST happen first

**Spec order:** 1, 2, 3, 4, 5, ...

**Correct order:** 3 must be FIRST or tied with 1.

**Why:** If you change designation resolution (section 1) BEFORE updating vault paths (section 3), every agent creates a NEW empty vault on next run. Old vaults are orphaned.

### 5.2 Section 7 (roster display) depends on 3 (vault) and 4 (status bar)

**Spec order:** 3, 4, 7

**Dependencies:**
- Roster display needs agent_name in presence files (not mentioned)
- Presence files need agent_name BEFORE roster can show it
- This requires presence.py changes BEFORE plugin.py changes

### 5.3 Section 10 (engine API) is blocked on 8 (messaging)

**Spec order:** 8, 10 (appears correct)

**Reality:** Engine API can't resolve agent names until messaging can. If you build API first, it's broken until messaging is done.

### 5.4 Section 11 (docs) should happen throughout

**Spec order:** Listed as step 11 (last)

**Reality:** Docs should be updated AS YOU GO, not at the end. Otherwise:
- Other devs write code against old mental model
- PR reviews get confusing
- You forget what you changed

### 5.5 Section 2 (hide --designation) should be LAST

**Spec order:** 2 (early)

**Reality:** Hide the flag AFTER everything works. If you hide it first and something breaks, users can't debug by passing `--designation`.

---

## 6. MIGRATION ISSUES

### 6.1 "brand new app so migration is low priority"

**Spec says:**
> "brand new app so migration is low priority"

**Reality:** Kollabor has USERS. The public release was 2026-02-24 (6 weeks ago). People have:
- Vaults with memories
- Org charts in production
- Scripts using `--designation`
- Muscle memory around the UX

Migration is NOT low priority. It's critical or you lose users.

### 6.2 Symlink migration is fragile

**Spec says:**
> "create symlink: vaults/{agent-name}/ -> vaults/{gem-name}/"

**Problems:**
1. Symlinks break if you move the vaults directory
2. Symlinks don't work on all filesystems (Windows, network drives)
3. You still have TWO paths to the same data (confusing)
4. What if the symlink target doesn't exist (deleted vault)?

**Better approach:** Atomic directory move or data migration script.

### 6.3 No rollback plan

**Spec doesn't mention:** What if this breaks everything? How do users revert?

**Need:**
- Migration script that's idempotent
- Rollback script that undoes changes
- Feature flag to disable new behavior

### 6.4 No migration for org charts

**Spec doesn't mention:** Org charts that rely on gem pool assignment.

**Problem:** Existing org JSON files don't have agent names, only designations. After the change, how do they work?

---

## 7. SPEC OMISSIONS (Things not mentioned at all)

### 7.1 How to detect "solo mode" vs "team mode"

The spec uses these terms constantly but never defines them. Is it:
- `--org` flag present?
- `org_name` field populated?
- Number of peers > 0?

### 7.2 What happens to `RoleResolver` (if it exists)

There may or may not be a component that maps roles to designations. The spec doesn't mention it.

### 7.3 Color assignment for non-gem designations

Section 5 says "gems provide color themes" but doesn't say how to get colors from non-gem strings.

### 7.4 Error messages

When agent name resolution fails, what does the user see?
- "Agent 'jarvis' not found"
- "Designation 'jarvis' not found"
- "No agent named or designated 'jarvis'"

### 7.5 Testing strategy

No mention of how to verify this works. No test plan, no migration test, no rollback test.

### 7.6 Performance impact

Name resolution on every message send could be expensive. No performance analysis.

---

## 8. RECOMMENDATIONS

### 8.1 ABANDON the "default designation = agent name" approach

**Better approach:** Keep the current gem pool for solo agents. Add a new `--identity` flag for users who want persistent names.

```bash
kollab --agent coder           # Still gets gem designation (lapis, peridot...)
kollab --identity jarvis        # Gets designation=jarvis, pinned to this agent
```

### 8.2 Add `agent_name` to presence files FIRST

Before changing any UX, add agent_name to the presence system. Then roster and messaging can use it.

### 8.3 Write the migration FIRST

Don't change any behavior until you have a working migration script with tests.

### 8.4 Keep designation as the primary key

Designation IS the identity. Changing this breaks everything. Instead:
- Add agent_name as a DISPLAY field
- Keep designation for all routing, vault paths, messaging
- Show agent_name in UI where appropriate

### 8.5 Split into phases

This is too big for one change. Split into:
1. Add agent_name to presence/routing (no UX changes)
2. Update UI to show agent_name where useful
3. Add `--identity` flag for pinned names
4. Update docs

---

## SUMMARY

The spec has good intent (unify confusing UX) but fatal execution flaws:

**Critical issues:**
- Vault data loss scenario (section 3)
- Collision handling undefined (section 1)
- Name resolution layer missing (section 8)
- Migration incomplete (section 11)

**Recommendation:** Reject and rewrite. Focus on adding agent_name as a DISPLAY field rather than changing the core identity system.

**Risk level:** HIGH. Implementing as written would cause:
- User data loss
- Broken existing orgs
- Confusing new UX
- Expensive rollback
