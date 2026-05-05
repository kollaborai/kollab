---
title: Crystal Memory XML Tags
created: 2026-04-13
status: draft
author: maintainers
---

# Crystal Memory XML Tags

give agents full CRUD access to their own crystallized memory
via XML tags. right now agents can only write (vault_write) and
passively receive nudges. they can't search, read, edit, or
delete their own memories.


## problem

the crystal nudge system injects summary lines like:
  [crys-003] hub message routing fix (score: 2.3)
  use /hub vault read <id> to see full details.

but agents can't use /hub vault commands -- those are slash
commands for humans. so agents see breadcrumbs to their own
memories but have no way to follow them.

existing vault XML tags:
  hub_vault    -- vault summary only (session count, etc)
  hub_vaults   -- list all vaults (no entry detail)
  vault_write  -- create new crystal entry

missing:
  search crystal entries by keyword
  read full body of a specific entry
  list all entries (with pagination)
  edit/update an existing entry
  delete a bad/stale memory


## design

### new XML tags

all tags are self-closing. all return tool results into the
conversation so the agent can act on them.

1. crystal_search -- keyword search

   <crystal_search query="hub message routing"/>
   <crystal_search query="vault persistence" limit="3"/>

   attrs:
     query (required): search terms
     limit (optional): max results, default 5, max 10

   returns:
     search results for 'hub message routing' (3 matches):
       [crys-003] hub message routing fix
       [crys-011] message delivery dedup window
       [crys-015] broadcast vs directed messages
     use crystal_read to see full entry body.

   context limit: result capped at 2000 chars. if results
   exceed that, truncate list and append "N more matches,
   narrow your query."

2. crystal_read -- read full entry by ID

   <crystal_read id="crys-003"/>

   attrs:
     id (required): entry ID (e.g. crys-003)

   returns:
     [crys-003] hub message routing fix
     date: 2026-04-12
     keywords: hub, message, routing, broadcast, socket
     ---
     Full body text of the entry here.

   context limit: body truncated at 3000 chars with
   "[truncated, N chars total]" suffix.

3. crystal_list -- list all entries

   <crystal_list/>
   <crystal_list limit="10" offset="0"/>

   attrs:
     limit (optional): entries per page, default 20, max 50
     offset (optional): skip N entries, default 0

   returns:
     crystal entries (47 total, showing 1-20):
       [crys-001] initial vault setup and config
       [crys-002] prompt_renderer trender patterns
       ...
       [crys-020] dreaming cycle improvements
     use crystal_list with offset="20" for next page.

   context limit: summary lines only (no bodies). result
   capped at 3000 chars.

4. crystal_edit -- update an existing entry

   <crystal_edit id="crys-003">updated body text here</crystal_edit>
   <crystal_edit id="crys-003" summary="new summary">new body</crystal_edit>
   <crystal_edit id="crys-003" keywords="hub,routing,fix">new body</crystal_edit>

   attrs:
     id (required): entry ID to edit
     summary (optional): new summary line
     keywords (optional): comma-separated, replaces existing
   body:
     new body text (required)

   returns:
     updated crys-003: hub message routing fix
     keywords: hub, routing, fix (3)
     body: 245 chars

   if entry doesn't exist: "no crystal entry 'crys-003'"

5. crystal_delete -- remove a bad/stale memory

   <crystal_delete id="crys-003"/>
   <crystal_delete id="crys-003" reason="outdated after refactor"/>

   attrs:
     id (required): entry ID to delete
     reason (optional): logged to stream but not stored

   returns:
     deleted crys-003: hub message routing fix

   if entry doesn't exist: "no crystal entry 'crys-003'"

   the deletion is logged to the vault stream so there's
   an audit trail. the entry is removed from crystallized.md.


### context limits (critical)

crystal memories can get large. every tag response must be
bounded to prevent context overflow:

  crystal_search: max 2000 chars output
  crystal_read:   max 3000 chars output (body truncation)
  crystal_list:   max 3000 chars output (summary only)
  crystal_edit:   max 500 chars output (confirmation only)
  crystal_delete: max 500 chars output (confirmation only)

these limits apply to the ToolExecutionResult.output string.
if the raw result exceeds the limit, truncate and append a
count of omitted content.


### ID normalization (critical)

agents may send bare digits ("3", "110") or full IDs
("crys-003", "crys-110"). all handlers that accept an id
attr MUST normalize before lookup:

  normalize_crystal_id(raw: str) -> str:
    - strip whitespace
    - if bare digits: zero-pad to 3 digits, prepend "crys-"
      "3" -> "crys-003", "110" -> "crys-110"
    - if already has "crys-" prefix: pass through
    - if other format: pass through (get_by_id returns None)

add this as a shared helper in crystal_store.py. used by:
crystal_read, crystal_edit, crystal_delete handlers.


### ToolExecutionResult signature (critical)

the CORRECT kwargs are:

  ToolExecutionResult(
      tool_id=tool_data.get("id", "unknown"),
      tool_type="crystal_search",
      success=True,
      output="result text here",
  )

for errors:

  ToolExecutionResult(
      tool_id=tool_data.get("id", "unknown"),
      tool_type="crystal_search",
      success=False,
      error="error message here",
  )

DO NOT use tool_name= or result= kwargs. those are wrong
and will crash at runtime. the old vault_write handler had
this bug and was fixed 2026-04-13.


### extraction function -> tool_data field mapping

each regex extraction function returns a dict. the handler
receives that dict as tool_data. field names MUST match.

  crystal_search extractor returns:
    {"query": str, "limit": int}
  crystal_search handler reads:
    tool_data.get("query", "")
    tool_data.get("limit", 5)

  crystal_read extractor returns:
    {"entry_id": str}
  crystal_read handler reads:
    tool_data.get("entry_id", "")

  crystal_list extractor returns:
    {"limit": int, "offset": int}
  crystal_list handler reads:
    tool_data.get("limit", 20)
    tool_data.get("offset", 0)

  note: <crystal_list/> with no attrs is the common case.
  extractor must handle missing groups gracefully:
    limit group missing -> default 20
    offset group missing -> default 0

  crystal_edit extractor returns:
    {"entry_id": str, "content": str,
     "summary": str or None, "keywords": list or None}
  crystal_edit handler reads:
    tool_data.get("entry_id", "")
    tool_data.get("content", "")
    tool_data.get("summary")       -- None if attr absent
    tool_data.get("keywords")      -- None if attr absent

  keyword signaling:
    keywords attr absent entirely -> extractor returns None
      -> handler passes None to update_entry()
      -> update_entry re-extracts from new body
    keywords attr present but empty (keywords="") -> extractor
      returns [] -> handler passes []
      -> update_entry clears keywords (edge case, unlikely)
    keywords attr present (keywords="a,b,c") -> extractor
      splits on comma, returns ["a", "b", "c"]
      -> update_entry replaces keywords entirely

  summary signaling: same pattern.
    absent -> None -> update_entry keeps existing summary
    present -> str -> update_entry replaces summary

  crystal_delete extractor returns:
    {"entry_id": str, "reason": str}
  crystal_delete handler reads:
    tool_data.get("entry_id", "")
    tool_data.get("reason", "")


### changes needed

1. crystal_store.py -- add methods:

   normalize_crystal_id(raw: str) -> str
     ID normalization (bare digits, crys- prefix, zero-pad)

   delete_entry(entry_id: str) -> Optional[CrystalEntry]
     normalize ID, remove entry by ID, save to disk,
     return removed entry or None

   update_entry(entry_id: str, body: str,
       summary: str = None, keywords: list = None)
       -> Optional[CrystalEntry]
     normalize ID, update entry fields. if keywords not
     explicitly provided, re-extract from new body using
     extract_keywords() (same as add_entry). if keywords
     ARE provided, replace entirely (not union). save to disk.

2. plugin.py -- register 5 new pipeline tags:
   - crystal_search (self-closing, query + limit attrs)
   - crystal_read (self-closing, id attr)
   - crystal_list (self-closing, limit + offset attrs)
   - crystal_edit (body tag, id + summary + keywords attrs)
   - crystal_delete (self-closing, id + reason attrs)

   register via response_parser.register_plugin_tag() and
   tool_executor.register_plugin_handler().

   pattern for each tag:
     1. compile regex
     2. define _extract_<name>(m) -> dict
     3. register_plugin_tag(name, pattern, name, extractor)
     4. register_plugin_handler(name, self._handle_<name>_tool)

   update the registered tag count log line (currently 33).

3. plugin.py -- add 5 handler methods:
   - _handle_crystal_search_tool(tool_data)
   - _handle_crystal_read_tool(tool_data)
   - _handle_crystal_list_tool(tool_data)
   - _handle_crystal_edit_tool(tool_data)
   - _handle_crystal_delete_tool(tool_data)

   each handler:
     - imports ToolExecutionResult from kollabor_agent.tool_executor
     - checks crystal_store initialized
     - validates required attrs (empty query, empty body, etc)
     - normalizes entry IDs where applicable
     - calls crystal_store method
     - truncates output to context limit
     - returns ToolExecutionResult(tool_id=..., tool_type=...,
       success=..., output=...) -- NOT tool_name/result kwargs
     - logs deletions to vault stream via self._vault.append_stream()

4. get_injection_context() in crystal_store.py line 449:
   - replace "(use /hub vault read <id>)" with
     "(use crystal_read to see full entry)"
   - this appears in the system prompt injection when
     crystal entries overflow the budget

5. _crystal_nudge_on_input() in plugin.py line 3886:
   - replace "use /hub vault read <id> to see full details
     of any entry." with "use crystal_read to see full
     details of any entry."
   - this appears in the [crystal nudge] system messages
     injected on keyword match. both locations reference
     the same dead-end slash command -- both must be updated.

6. agent prompt docs (optional, phase 2):
   - add tool reference doc at
     bundles/agents/_base/sections/tool-reference/crystal.md
   - document all 6 vault/crystal tags (including vault_write)


### tag count after implementation

current: 33 registered hub pipeline tags
new:     38 (33 + 5 new crystal tags)


### slash commands

/hub vault read|search|list|stats stay for human debugging.
no changes needed there. they're useful for maintainers to inspect
agent memories from the CLI.


### error handling

all handlers must:
  - return graceful error in ToolExecutionResult if crystal_store
    is not initialized (vault not started)
  - return "no crystal entry '<id>'" for missing entries
  - return "empty query" for crystal_search with blank query
  - return "empty body" for crystal_edit with blank content
  - return "no crystal entries" for crystal_list on empty store
  - catch and log exceptions, never crash
  - follow the corrected vault_write handler pattern


### testing

unit tests in tests/unit/test_crystal_tags.py:
  - crystal_search: empty query, no matches, partial match,
    limit enforcement, output truncation
  - crystal_read: valid ID, missing ID, bare digit ID ("3"),
    body truncation
  - crystal_list: empty store, pagination, offset bounds
  - crystal_edit: valid edit, missing ID, keyword update,
    summary update, body re-extraction, empty body rejection
  - crystal_delete: valid delete, missing ID, audit stream log
  - context limits: verify all outputs stay within bounds
  - ID normalization: "3"->"crys-003", "110"->"crys-110",
    "crys-003"->"crys-003", "foo"->passthrough

integration test in tests/tmux/specs/:
  - agent uses vault_write then crystal_search to find it
  - agent reads full entry with crystal_read
  - agent edits entry with crystal_edit
  - agent deletes entry with crystal_delete


### audit findings incorporated

from audit 1 (2026-04-13, AuditCrystalSpec1):
  - 0 factual errors, all code refs verified correct
  - 6 edge cases added to error handling + testing sections
  - context limits confirmed reasonable
  - no tag name conflicts
  - vault_write vs crystal_* naming inconsistency noted,
    accepted as cosmetic (vault_write already shipped)

from audit 2 (2026-04-13, AuditCrystalSpec2):
  - ToolExecutionResult fields verified correct
  - no circular dependency risk
  - no regex pattern conflicts
  - thread safety: pre-existing narrow lock pattern, low risk
    (all callers on same async event loop)
  - _generate_id() race: pre-existing, not introduced here
  - crystal_edit is the only new body tag -- regex MUST use
    _re.DOTALL | _re.IGNORECASE (same as vault_write)

from maintainers (2026-04-13):
  - ToolExecutionResult kwargs fixed (tool_name/result -> 
    tool_id/tool_type/success/output)
  - ID normalization pattern added
  - extraction->handler field mapping documented
