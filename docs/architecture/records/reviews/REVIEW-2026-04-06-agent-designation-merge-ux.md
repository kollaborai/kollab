---
title: "Agent Designation Merge - UX Review"
doc_type: architecture-review
created: 2026-04-06
modified: 2026-04-06
status: historical
---
# Agent Designation Merge - UX Review

**Date:** 2026-04-06
**Reviewer:** Product Design (user perspective)
**Subject:** ../../rfcs/RFC-2026-04-06-agent-designation-merge.md spec

---

## EXECUTIVE SUMMARY

The spec correctly identifies a real problem: "agent" vs "designation" is confusing.
However, the proposed solution introduces NEW confusion while trying to fix the old.

**Critical finding:** The spec says "hide designation from basic help" but keeps
it EVERYWHERE ELSE in the UX. A confused user who stumbles on "designation" in
error messages, status bars, or docs won't be helped by it being missing from
--help.

**Recommendation:** Either FULLY commit to the merge (eliminate "designation"
from ALL user-facing surfaces) OR embrace the duality and explain it clearly.
Half measures will confuse everyone.

---

## 1. USER CONFUSION - AFTER THE CHANGE

### 1.1 The "What's My Name?" Problem

Current help text after the change (per spec):
```
  -a, --agent AGENT      Use a specific agent (e.g., --agent lint-editor)
  [no --designation in basic help]
```

User runs:
```
kollab --agent coder
```

Status bar shows:
```
◈ coder +0 peers
```

So far so good. But then user sees:
```
kollab --attach coder
```

What happens internally? Attach client looks up socket by DESIGNATION, not
agent name. If they match (coder=coder), it works. But the user doesn't know
that this ONLY works because of the new default rule.

Now user tries team mode:
```
kollab --org engineering.json
```

Status bar shows:
```
◈ manager (ruby-234) +4 peers
```

Wait, what? Now there's TWO names? The spec says "agent (designation)" in
team mode but this is THE FIRST TIME user sees this pattern. Where was it
explained?

**Confusion:** User thought --agent set their identity. Now they have TWO
identities and no context for why.

### 1.2 Error Message Breakage

User types wrong agent name:
```
kollab --agent nonexistent
```

What error do they see? The spec doesn't say. Current code probably says:
```
error: agent 'nonexistent' not found
```

But with --attach:
```
kollab --attach nonexistent
```

This goes through hub resolution. Error could be:
```
cannot connect to nonexistent: No such file or directory
```

Or worse:
```
attach failed: no agent found with designation 'nonexistent'
```

**Confusion:** User said --agent, error says "designation". They've never seen
that word in --help. What does it mean?

### 1.3 Vault Path Inconsistency

Solo mode user:
```
~/.kollab/hub/vaults/coder/     # matches agent name
```

Team mode user:
```
~/.kollab/hub/vaults/manager/   # agent name
~/.kollab/hub/vaults/ruby-234/  # ???
```

The spec says vault keyed by agent name in solo mode. But team mode agents
need unique designations. Which one does the vault use? The spec says "when
designation != agent_name (team mode), use designation" but HOW does user
know which mode they're in? They just ran --org.

**Confusion:** User's vault disappeared because it's now under a different
name they didn't choose.

### 1.4 Hub Messaging Madness

From hub-collaboration.md:
```
<hub_msg to="designation">your message here</hub_msg>
```

After the merge, user thinks: "I'm agent 'coder'. I should message 'lapis'."

But lapis might BE a coder agent too. The spec says designation is used
internally for routing. So:
```
<hub_msg to="lapis">can you help?</hub_msg>
```

Works. But user doesn't know "lapis" IS the designation. They think it's
the agent. What if they have two coder agents, both named "coder" but with
designations "lapis" and "peridot"?

**Confusion:** User types `<hub_msg to="coder">` - which one gets it? The spec
doesn't address duplicate agent names in team mode.

---

## 2. NAMING: IS "DESIGNATION" THE RIGHT WORD?

**No.** Here's why:

### 2.1 Mental Model Mismatch

- "Agent" = What I do (coder, writer, analyst)
- "Designation" = ???

Users understand "role", "identity", "alias", "name". "Designation" feels like
HR terminology or military ranks. It doesn't evoke "this is who you talk to
me as."

### 2.2 Alternative Names Tested

| Word | Pros | Cons |
|------|------|------|
| designation | Already in codebase | Confusing, formal |
| identity | Clear, familiar | Overloaded term |
| handle | IRC/slack vibes | Too casual? |
| alias | Users understand it | Implies secondary |
| hub_name | Very clear | Redundant with "hub" |

**Recommendation:** "hub_name" or "handle". "hub_name" wins because it's
explicitly scoped: "your name in the hub" vs "your agent type."

### 2.3 But Actually...

If we're merging the concepts, WHY HAVE TWO WORDS AT ALL?

The spec says "keep architecture separate, merge the UX." But that's not a
true merge. A true merge would be:

- User has ONE identity
- Internal code can map identity -> agent config
- User never sees "designation"

If the spec commits to hiding designation from help, it should hide it from
EVERYTHING or explain it ONCE upfront.

---

## 3. HELP TEXT - WHAT SHOULD IT LOOK LIKE?

### 3.1 Current (per spec)

```
  -a, --agent AGENT      Use a specific agent (e.g., --agent lint-editor)
  [missing: --designation]
```

### 3.2 Proposed - Better

```
AGENT IDENTITY:
  -a, --agent NAME       Use a specific agent (default: default)
                        This sets both your agent type AND your hub name.
                        Advanced: use --designation for a custom hub name.

  -d, --designation NAME Custom hub name (for team mode or rebirth)
                        In solo mode, defaults to agent name.
                        In team mode (--org), assigned by org chart.

EXAMPLES:
  kollab --agent coder                  Solo agent, hub name "coder"
  kollab --agent coder -d marcus        Agent "coder", hub name "marcus"
  kollab --attach coder                 Attach to hub "coder"
  kollab --org engineering.json         Team mode (names assigned by org)
```

### 3.3 Proposed - Best (Full Commitment)

If we're truly merging concepts:

```
AGENT IDENTITY:
  -a, --agent NAME       Set your agent type and hub identity
                        Use --agent for solo mode, --org for team mode

  --org FILE.json        Launch as a team (assigns identities from org chart)

EXAMPLES:
  kollab --agent coder                  Solo: agent "coder", hub "coder"
  kollab --org engineering.json         Team: assigns roles automatically
  kollab --attach coder                 Connect to agent "coder"
```

Advanced mode (hidden from basic help):

```
ADVANCED (rarely needed):
  -d, --hub-name NAME   Override hub identity (for rebirth or conflicts)
  --detach-identity     Run with different hub name than agent type
```

---

## 4. STATUS BAR - SOLO VS TEAM MOCKUPS

### 4.1 Current Status Bar

Row 3 shows:
```
◈ lapis* +3 peers    [hub widget]
coder                  [agent widget]
```

Two widgets, adjacent, showing DIFFERENT names. This is the core confusion.

### 4.2 After Spec Implementation (Solo Mode)

```
◈ coder +0 peers       [hub: designation=agent name]
coder                  [agent: agent name]
```

Redundant. Why show both?

### 4.3 Proposed - Solo Mode

```
◈ coder +0 peers       [only hub widget, hide agent widget in solo mode]
```

OR merge into one widget:
```
[coder] +0 peers       [combined hub+agent widget]
```

### 4.4 Proposed - Team Mode

```
◈ team: eng (5 agents)     [summary view]
[press H for roster]       [discoverability prompt]
```

When user presses 'H' or types /hub agents:
```
Active Team (engineering):
  manager (ruby-234) *     [coordinator]
  frontend-dev (sapphire)  [role: agent, hub: sapphire]
  backend-dev (peridot)
  qa-tester (emerald)
```

The pattern is: "role (hub_name)" - explicit about both identities.

### 4.5 Key Insight

The spec shows "agent (designation)" but doesn't explain WHEN this pattern
appears. Status bar should use CONDITIONAL DISPLAY:

- Solo: show one name (they're the same)
- Team: show "role (hub_name)" with visual distinction
- NEVER show two adjacent widgets with the same info

---

## 5. ERROR MESSAGES - WHAT HAPPENS WHEN THINGS GO WRONG?

### 5.1 Wrong Agent Name

```
$ kollab --agent nonexistent
error: unknown agent 'nonexistent'
available agents: coder, writer, analyst, tech-dude
run 'kollab --agent list' for details
```

Good: says "agent" (user's term), offers alternatives.

### 5.2 Wrong Attach Target

```
$ kollab --attach nonexistent
error: no agent found with hub name 'nonexistent'
active agents: coder (idle), writer (working)
run 'kollab --hub agents' to see all agents
```

Note: Uses "hub name" not "designation" - clearer terminology.

### 5.3 Name Conflict in Team Mode

```
$ kollab --org engineering.json
error: hub name 'ruby-234' already taken by another agent
options:
  1. Stop the conflicting agent: kollab --hub stop ruby-234
  2. Use a different org chart
  3. Launch without hub: kollab --agent manager --no-hub
```

Clear about the conflict and offers resolution paths.

### 5.4 Migration Edge Case

```
$ kollab --attach coder
warning: found vault for 'lapis' (previous hub name for 'coder')
attach to 'coder' (new vault) or 'lapis' (old vault)? [C/l]
```

Helps user navigate the migration smoothly.

---

## 6. DISCOVERABILITY - HOW DO USERS LEARN ABOUT TEAM MODE?

### 6.1 The Problem

Team mode (--org) is mentioned NOWHERE in the basic help flow. User discovers
it by accident or reads advanced docs.

### 6.2 Proposed - Onboarding Prompt

First time user runs kollab:
```
Kollabor v1.0 - Terminal AI Chat

Starting in SOLO mode (1 agent).

Want to try TEAM mode? Multiple agents collaborating together?
  Type: /hub orgs        [list available teams]
  Or:  kollab --org startup     [launch a team]

Press Enter to continue...
```

### 6.3 Help Section Addition

In --help, add:
```
MODES:
  SOLO (default):     One agent, hub name matches agent type
  TEAM (--org):       Multiple agents with roles from org chart

STARTING:
  kollab                  Start solo mode (agent: default)
  kollab --agent coder    Start solo with specific agent
  kollab --org eng.json   Start as a team
```

### 6.4 Interactive Discovery

When user has been using solo mode for a while:
```
Tip: You've been running solo. Try team mode!
  /hub orgs              See available teams
  /hub org startup       Launch a startup team
```

This is CONTEXTUAL help, not dumping everything upfront.

---

## 7. DOCS - WHAT SHOULD GETTING-STARTED SAY?

### 7.1 Current Problem

Docs explain "designation" as a gem-inspired system but the spec hides it.
This creates a gap between docs and reality.

### 7.2 Proposed Getting-Started Section

```
## Agent Identity

Kollabor has two modes: SOLO and TEAM.

### Solo Mode (Default)

When you run `kollab` or `kollab --agent coder`, you're in solo mode.
Your agent type AND your hub identity are the same.

Examples:
  kollab                  → agent: default, hub: default
  kollab --agent coder    → agent: coder, hub: coder

Your hub name is how other agents see you. In solo mode, it matches
your agent type for simplicity.

### Team Mode (--org)

When you launch a team with `--org engineering.json`, each agent gets
a role (manager, frontend, backend) AND a unique hub name (ruby-234,
sapphire, peridot).

The org chart assigns both:
  "role": "manager"
  "hub_name": "ruby-234"   [auto-assigned, unique per team]

This lets you have multiple "coder" agents on the same team, each with
different hub identities.

### Advanced: Custom Hub Names

Rarely needed, but you can override:
  kollab --agent coder --hub-name marcus

Use this for:
  - Rebirth: reconnect to previous vault under a different name
  - Conflicts: two agents of the same type on the same hub
```

### 7.3 Key Changes from Current Docs

1. Eliminate "designation" terminology
2. Introduce "hub_name" as clear alternative
3. Explain solo vs team modes upfront
4. Show the progression from simple to advanced

---

## 8. CRITIQUE OF SPECIFIC SPEC ITEMS

### Item 3: Vault Keyed by Agent Name

**Problem:** Migration is glossed over. Existing vaults are under gem names.
If user has vault at ~/.kollab/hub/vaults/lapis/ and they run
`kollab --agent tech-dude`, the vault is orphaned.

**Fix Required:**
- Detect orphaned vaults on startup
- Prompt user to migrate or link
- Document the vault path changes in migration guide

### Item 7: Hub Roster Display

**Problem:** "show agent name by default, show designation only when it
differs" - HOW does user know when it differs? There's no visual indicator.

**Fix Required:**
- Use color or format to distinguish: `coder (lapis)` or `coder → lapis`
- Add legend to /hub agents output

### Item 11: Docs and Prompts

**Problem:** "remove 'designation' from user-facing docs" is too broad.
Internal docs for devs should still use the correct term. Only user-facing
docs change.

**Fix Required:**
- Separate "user docs" from "dev docs" in this item
- Create a terminology translation guide for contributors

### Item 12: Attach Client

**Problem:** "--attach takes agent name, resolves to designation" - what if
there are two agents with the same agent name but different designations?
(Valid in team mode per spec.)

**Fix Required:**
- Document disambiguation: `kollab --attach coder:1` or `--attach coder@lapis`
- Or prohibit duplicate agent names in team mode (simpler)

---

## 9. MISSING FROM SPEC

### 9.1 Agent.json "designation" Field

The spec says "remove designation field from agent.json" but all current
agent.json files HAVE this field:
```json
{
  "description": "Native function calling...",
  "profile": null,
  "designation": "native",   ← what happens to this?
  "capabilities": [...]
}
```

**Required:** Migration path for agent.json files. Does the field become
"hub_name"? Is it ignored? Deprecated?

### 9.2 /hub msg Command Behavior

Current: `/hub msg <agent> <message>`

After merge: does this take agent name or hub name? Spec doesn't say.

User experience expectation:
```
/hub msg coder help me out    → sends to agent "coder" (resolves to hub name)
```

But if there are two "coder" agents with different hub names, which one?

**Required:** Disambiguation story or prohibition of duplicates.

### 9.3 Org Launcher Behavior

Spec says "org charts define explicit designations per role." What if user
creates:
```json
{
  "team": "engineering",
  "roles": [
    {"type": "coder", "hub_name": "alice"},
    {"type": "coder", "hub_name": "bob"}
  ]
}
```

This works. But what about:
```json
{
  "team": "engineering",
  "roles": [
    {"type": "coder"}  ← no hub_name specified
  ]
}
```

Does it auto-assign? Error? What is the default behavior?

**Required:** Default hub_name assignment rules for org launcher.

---

## 10. FINAL RECOMMENDATIONS

### 10.1 Commit to the Merge

Don't half-hide "designation." Pick one approach:

**Option A: Full Merge (Recommended)**
- Eliminate "designation" from ALL user-facing surfaces
- Use "hub_name" internally
- Agent name = hub name in solo mode
- Org charts assign "hub_name" field explicitly

**Option B: Embrace Duality**
- Keep "designation" visible
- Explain it clearly in onboarding
- Use visual distinction in UI
- Document when solo vs team mode applies

### 10.2 Terminology Change

Replace "designation" with "hub_name" everywhere:
- CLI flags: --hub-name (not --designation)
- Status bar: "hub name" in tooltips
- Error messages: "hub name 'foo' not found"
- Docs: "hub_name" field in configs

### 10.3 Conditional UI Display

- Solo mode: hide agent widget, show only hub widget
- Team mode: show "role (hub_name)" format
- Never display redundant info side-by-side

### 10.4 Discoverability Framework

- First-run prompt explaining solo vs team
- Contextual tips when user seems ready
- /hub orgs command to list available teams
- Clear error messages with resolution paths

### 10.5 Migration Strategy

- Detect orphaned vaults on startup
- Prompt user to link or migrate
- Document agent.json field changes
- Version the hub protocol for breaking changes

---

## 11. PRIORITY FIXES

Must fix before shipping:
1. Error message terminology (designation → hub_name)
2. Status bar redundancy in solo mode
3. Vault migration path
4. Disambiguation for --attach with duplicates
5. Org launcher default hub_name assignment

Should fix before shipping:
6. Onboarding prompt for team mode
7. Help text reorganization
8. Visual distinction for role vs hub_name
9. /hub orgs command implementation
10. Terminology guide for contributors

---

**Review Status:** CRITICAL ISSUES FOUND
**Recommendation:** Spec needs revision before implementation
**Next Step:** Product decision on Option A (full merge) vs Option B (embrace duality)
