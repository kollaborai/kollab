"""Hub tool definitions.

All hub pipeline tags registered in plugins/hub/plugin.py.
Organized by subcategory:
- Messaging: hub_msg, hub_broadcast
- Agent management: hub_stop, hub_status, hub_spawn, hub_agents, hub_capture
- Work queue: hub_queue, hub_claim, hub_work, claims
- Vault: hub_vault, hub_vaults, vault_write, crystal_search/read/list/edit/delete
- Cron: hub_cron_add, hub_cron_list, hub_cron_delete
- Work lanes: lane_claim, lane_release
- Change feed: file_changed, file_watch, file_unwatch, feed_recent, feed_file
- State: state_update
"""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry


# ============================================================
# MESSAGING
# ============================================================

hub_msg = ToolDefinition(
    name="hub-msg",
    description="Send a message to another agent on the hub.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="hub_msg",
    xml_form="mixed",
    xml_attributes=["to", "wait", "force"],
    xml_body_param="message",
    parameters=[
        ToolParameter(
            name="to",
            type="string",
            description="Identity name of recipient, or 'all' to broadcast",
            required=True,
        ),
        ToolParameter(
            name="message",
            type="string",
            description="Message content",
            required=True,
        ),
        ToolParameter(
            name="wait",
            type="string",
            description="Set 'true' to stop after sending (no re-invocation)",
            required=False,
        ),
        ToolParameter(
            name="force",
            type="string",
            description="Force delivery even if would be deduped",
            required=False,
        ),
    ],
    examples=[
        '<hub_msg to="lapis">standby. waiting for next task.</hub_msg>',
        '<hub_msg to="all" wait="true">phase B shipped. standing by.</hub_msg>',
    ],
    result_format="Delivery confirmation.",
    key_rules=[
        "use identity names from the roster (lapis, sapphire, etc), not agent type names",
        "all messages are visible to all peers — no private DMs",
        "wait='true' means you are done talking after this message — use when you have nothing else to do",
        "without wait='true' the system will re-invoke you after delivery — correct when you have more work to do but causes loops when you're just chatting",
        "be concise — other agents have limited context too",
    ],
)

hub_broadcast = ToolDefinition(
    name="hub-broadcast",
    description="Broadcast an announcement to all peers.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="hub_broadcast",
    xml_form="mixed",
    xml_attributes=["force"],
    xml_body_param="message",
    parameters=[
        ToolParameter(
            name="message",
            type="string",
            description="Announcement content",
            required=True,
        ),
        ToolParameter(
            name="force",
            type="string",
            description="Force delivery even if would be deduped",
            required=False,
        ),
    ],
    examples=[
        "<hub_broadcast>phase B migration complete. all 28 tools registered.</hub_broadcast>",
    ],
    result_format="Broadcast delivery confirmation.",
    key_rules=[
        "hub_broadcast is for announcements — use hub_msg to='all' for conversations",
        "all broadcasts are visible to all peers — no targeting",
        "use force='true' to break through cooldown if the message is critical",
    ],
    safety_features=[
        "cooldown protection prevents spam — use force sparingly",
        "all tags stripped from displayed output (user won't see raw XML)",
    ],
)


# ============================================================
# AGENT MANAGEMENT
# ============================================================

hub_stop = ToolDefinition(
    name="hub-stop",
    description="Stop a specific agent or all agents.",
    category="hub",
    risk_level="high",
    requires_permission=True,
    xml_tag="hub_stop",
    xml_form="body",
    xml_body_param="target",
    parameters=[
        ToolParameter(
            name="target",
            type="string",
            description="Agent identity name, or 'all' to stop everyone",
            required=True,
        ),
    ],
    examples=[
        "<hub_stop>lapis</hub_stop>",
        "<hub_stop>all</hub_stop>",
    ],
    result_format="Confirmation that agent(s) were stopped.",
    key_rules=[
        "hub_stop kills the agent's subprocess session",
        "use 'all' to stop everyone — use sparingly",
    ],
)

hub_status = ToolDefinition(
    name="hub-status",
    description="Get current hub status — roster and coordinator info.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="hub_status",
    xml_form="attributes",
    parameters=[],
    examples=[
        "<hub_status />",
    ],
    result_format="Hub status with roster of online agents.",
    key_rules=[
        "returns roster + coordinator info — use to discover who's online before sending messages",
        "use identity names from the roster (lapis, sapphire, etc), not agent type names",
    ],
)

hub_spawn = ToolDefinition(
    name="hub-spawn",
    description=(
        "Spawn a new agent on the hub. Three modes:\n"
        "1. By identity: name='lapis' -> uses pool's agent_type for lapis\n"
        "2. By agent_type: name='coder' -> picks next available coder from pool\n"
        "3. Explicit: name='lapis' type='research' -> identity + type override\n"
        "Returns 'already online' if identity is running. No discovery needed."
    ),
    category="hub",
    risk_level="medium",
    requires_permission=False,
    xml_tag="hub_spawn",
    xml_form="mixed",
    xml_attributes=["name", "type"],
    xml_body_param="task",
    parameters=[
        ToolParameter(
            name="name",
            type="string",
            description=(
                "Either a pool identity (e.g. 'lapis') or an agent type "
                "(e.g. 'coder'). If identity, uses pool's agent_type. "
                "If agent_type, picks next available gem from pool."
            ),
            required=True,
        ),
        ToolParameter(
            name="task",
            type="string",
            description="Task description for the spawned agent",
            required=True,
        ),
        ToolParameter(
            name="type",
            type="string",
            description=(
                "Optional agent type override. Use with name=identity to "
                "override the pool's default type. e.g. name='lapis' type='research'"
            ),
            required=False,
        ),
    ],
    examples=[
        '<hub_spawn name="coder">fix the bug in foo.py</hub_spawn>',
        '<hub_spawn name="lapis">investigate the auth module</hub_spawn>',
        '<hub_spawn name="lapis" type="research">deep dive on performance</hub_spawn>',
    ],
    result_format=(
        "Confirmation with resolved identity and agent type. "
        "e.g. 'Created agent nephrite (agent type: coder)'"
    ),
    key_rules=[
        "use hub_spawn to create NEW agents",
        "use hub_msg to assign work to EXISTING agents already on the hub",
        "if identity is already online, returns 'already online' with instructions to use hub_msg",
        "spawning by agent_type picks the next available gem from pool.json",
    ],
)

hub_agents = ToolDefinition(
    name="hub-agents",
    description="List all online agents.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="hub_agents",
    xml_form="attributes",
    parameters=[],
    examples=[
        "<hub_agents />",
    ],
    result_format="List of online agents with their status.",
    key_rules=[
        "check who's online before sending hub_msg — don't message offline agents",
        "use identity names from the roster, not agent type names",
    ],
)

hub_capture = ToolDefinition(
    name="hub-capture",
    description="View an agent's recent output.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="hub_capture",
    xml_form="attributes",
    xml_attributes=["name", "lines"],
    parameters=[
        ToolParameter(
            name="name",
            type="string",
            description="Agent identity name to capture",
            required=True,
        ),
        ToolParameter(
            name="lines",
            type="integer",
            description="Number of recent lines to capture (default: 50)",
            required=False,
        ),
    ],
    examples=[
        '<hub_capture name="lapis" lines="100" />',
    ],
    result_format="Recent output from the agent.",
    key_rules=[
        "useful for checking what another agent is working on before assigning new tasks",
        "default 50 lines — increase if you need more context",
        "only captures recent output, not full history",
    ],
)


# ============================================================
# WORK QUEUE
# ============================================================

hub_queue = ToolDefinition(
    name="hub-queue",
    description="Add a work item to the shared queue.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="hub_queue",
    xml_form="body",
    xml_body_param="description",
    parameters=[
        ToolParameter(
            name="description",
            type="string",
            description="Description of the work item",
            required=True,
        ),
    ],
    examples=[
        "<hub_queue>refactor plugin loading in hub/plugin.py</hub_queue>",
    ],
    result_format="Work item ID and confirmation.",
    key_rules=[
        "add work items to the queue for any agent to pick up",
        "use hub_claim to claim a queued item — first come first served",
        "description should be specific enough for any agent to execute",
    ],
)

hub_claim = ToolDefinition(
    name="hub-claim",
    description="Claim a work item from the queue.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="hub_claim",
    xml_form="attributes",
    xml_attributes=["id"],
    parameters=[
        ToolParameter(
            name="claim_id",
            type="string",
            description="Work item ID to claim (optional — claims next available if omitted)",
            required=False,
        ),
    ],
    examples=[
        '<hub_claim id="abc123" />',
        "<hub_claim />",
    ],
    result_format="Claimed work item details.",
    key_rules=[
        "omit id to claim the next available item in the queue",
        "once claimed, the item is yours — other agents won't grab it",
        "report progress with task_checkpoint when done",
    ],
)

hub_work = ToolDefinition(
    name="hub-work",
    description="View current work queue and assignments.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="hub_work",
    xml_form="attributes",
    parameters=[],
    examples=[
        "<hub_work />",
    ],
    result_format="Current work queue state.",
    key_rules=[
        "check the queue before claiming to see what's available",
        "shows both queued items and active assignments",
    ],
)

claims = ToolDefinition(
    name="claims",
    description="List all active claims.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="claims",
    xml_form="attributes",
    xml_attributes=["identity"],
    parameters=[
        ToolParameter(
            name="identity",
            type="string",
            description="Filter claims by agent identity (optional)",
            required=False,
        ),
    ],
    examples=[
        "<claims />",
        '<claims identity="lapis" />',
    ],
    result_format="List of active claims.",
    key_rules=[
        "filter by identity to see your own claims",
        "unclaimed work stays in the queue for any agent to pick up",
    ],
)


# ============================================================
# VAULT / CRYSTAL MEMORY
# ============================================================

hub_vault = ToolDefinition(
    name="hub-vault",
    description="Get an agent's vault summary.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="hub_vault",
    xml_form="attributes",
    xml_attributes=["name"],
    parameters=[
        ToolParameter(
            name="name",
            type="string",
            description="Agent identity name (optional — shows own vault if omitted)",
            required=False,
        ),
    ],
    examples=[
        "<hub_vault />",
        '<hub_vault name="lapis" />',
    ],
    result_format="Vault summary for the requested agent.",
    key_rules=[
        "omit name to see your own vault summary",
        "use hub_vaults to see all vaults across agents at a glance",
    ],
)

hub_vaults = ToolDefinition(
    name="hub-vaults",
    description="List all agent vaults.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="hub_vaults",
    xml_form="attributes",
    parameters=[],
    examples=[
        "<hub_vaults />",
    ],
    result_format="List of all vaults with entry counts.",
    key_rules=[
        "useful for discovering which agents have accumulated knowledge",
        "drill into a specific vault with hub_vault name='identity'",
    ],
)

vault_write = ToolDefinition(
    name="vault-write",
    description="Save an insight to persistent vault memory.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="vault_write",
    xml_form="mixed",
    xml_attributes=["keywords"],
    xml_body_param="content",
    parameters=[
        ToolParameter(
            name="content",
            type="string",
            description="Insight text to persist",
            required=True,
        ),
        ToolParameter(
            name="keywords",
            type="string",
            description="Comma-separated keywords for retrieval",
            required=False,
        ),
    ],
    examples=[
        "<vault_write>hub routing uses peer identity names, not agent types</vault_write>",
        '<vault_write keywords="cron,timeout,daemon">cron bug fixed — triggers agent re-invocation now</vault_write>',
    ],
    result_format="Unique vault entry ID (e.g. crys-001).",
    key_rules=[
        "write to vault when you discover something important: architectural decisions, bugs, patterns, config quirks",
        "add explicit keywords for better retrieval — the nudge system uses them to surface relevant entries",
        "vault persists across sessions — scratchpad does not",
    ],
)

crystal_search = ToolDefinition(
    name="crystal-search",
    description="Search vault entries by keyword.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="crystal_search",
    xml_form="attributes",
    xml_attributes=["query", "limit"],
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Search query to match against vault entries",
            required=True,
        ),
        ToolParameter(
            name="limit",
            type="integer",
            description="Max results to return (default: 5, max: 10)",
            required=False,
        ),
    ],
    examples=[
        '<crystal_search query="hub routing" />',
        '<crystal_search query="vault" limit="3" />',
    ],
    result_format="Matching vault entries.",
    key_rules=[
        "search by keyword to find relevant memories — keywords are matched against entry text and metadata",
        "default returns 5 results, max 10 — use limit to adjust",
        "system auto-nudges relevant vault entries when conversation matches keywords",
    ],
)

crystal_read = ToolDefinition(
    name="crystal-read",
    description="Read full entry body by ID.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="crystal_read",
    xml_form="attributes",
    xml_attributes=["id"],
    parameters=[
        ToolParameter(
            name="entry_id",
            type="string",
            description="Entry ID (e.g. 'crys-003' or just '3')",
            required=True,
        ),
    ],
    examples=[
        '<crystal_read id="crys-003" />',
        '<crystal_read id="3" />',
    ],
    result_format="Full vault entry body.",
    key_rules=[
        "accepts full ID (crys-003) or bare number (3)",
        "use to follow up on crystal nudge breadcrumbs like [crys-003]",
    ],
)

crystal_list = ToolDefinition(
    name="crystal-list",
    description="List vault entries with pagination.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="crystal_list",
    xml_form="attributes",
    xml_attributes=["limit", "offset"],
    parameters=[
        ToolParameter(
            name="limit",
            type="integer",
            description="Entries per page (default: 20)",
            required=False,
        ),
        ToolParameter(
            name="offset",
            type="integer",
            description="Pagination offset",
            required=False,
        ),
    ],
    examples=[
        "<crystal_list />",
        '<crystal_list limit="10" offset="20" />',
    ],
    result_format="Paginated list of vault entries.",
    key_rules=[
        "default 20 per page — use limit and offset for pagination",
        "useful for browsing all entries when you don't know what to search for",
    ],
)

crystal_edit = ToolDefinition(
    name="crystal-edit",
    description="Update a vault entry's body and/or metadata.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="crystal_edit",
    xml_form="mixed",
    xml_attributes=["id", "summary", "keywords"],
    xml_body_param="body",
    parameters=[
        ToolParameter(
            name="entry_id",
            type="string",
            description="Entry ID to edit",
            required=True,
        ),
        ToolParameter(
            name="body",
            type="string",
            description="New body text",
            required=True,
        ),
        ToolParameter(
            name="summary",
            type="string",
            description="New summary (optional)",
            required=False,
        ),
        ToolParameter(
            name="keywords",
            type="string",
            description="New comma-separated keywords (optional)",
            required=False,
        ),
    ],
    examples=[
        '<crystal_edit id="crys-003">updated body text</crystal_edit>',
    ],
    result_format="Confirmation of edit.",
    key_rules=[
        "body is required — summary and keywords are optional",
        "edit is logged for audit — don't rewrite history to hide mistakes",
        "use to refine entries when you learn more about a topic",
    ],
)

crystal_delete = ToolDefinition(
    name="crystal-delete",
    description="Delete a vault entry (logged for audit).",
    category="hub",
    risk_level="medium",
    requires_permission=False,
    xml_tag="crystal_delete",
    xml_form="attributes",
    xml_attributes=["id", "reason"],
    parameters=[
        ToolParameter(
            name="entry_id",
            type="string",
            description="Entry ID to delete",
            required=True,
        ),
        ToolParameter(
            name="reason",
            type="string",
            description="Reason for deletion (optional)",
            required=False,
        ),
    ],
    examples=[
        '<crystal_delete id="crys-003" />',
        '<crystal_delete id="crys-003" reason="superseded by crys-010" />',
    ],
    result_format="Deletion confirmation.",
    key_rules=[
        "deletion is logged for audit — include a reason when deleting",
        "prefer crystal_edit over delete for corrections",
    ],
    safety_features=[
        "deletions are audit-logged — not silently removed",
    ],
)


# ============================================================
# CRON
# ============================================================

hub_cron_add = ToolDefinition(
    name="hub-cron-add",
    description="Schedule a recurring task.",
    category="hub",
    risk_level="medium",
    requires_permission=False,
    xml_tag="hub_cron_add",
    xml_form="mixed",
    xml_attributes=["interval"],
    xml_body_param="message",
    parameters=[
        ToolParameter(
            name="message",
            type="string",
            description="Message to send on each interval",
            required=True,
        ),
        ToolParameter(
            name="interval",
            type="string",
            description="Interval (e.g. '5m', '1h', '30s')",
            required=True,
        ),
    ],
    examples=[
        '<hub_cron_add interval="5m">check build status</hub_cron_add>',
    ],
    result_format="Cron job ID.",
    key_rules=[
        "interval format: '30s', '5m', '1h' — seconds, minutes, hours",
        "cron sends the message to you on each interval — you get re-invoked",
        "use for periodic checks like build status, test runs, or health checks",
        "delete cron jobs when done to avoid unnecessary re-invocations",
    ],
    safety_features=[
        "each cron invocation re-invokes the agent — too many crons wastes resources",
        "always clean up cron jobs when the task is complete",
    ],
)

hub_cron_list = ToolDefinition(
    name="hub-cron-list",
    description="List scheduled cron jobs.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="hub_cron_list",
    xml_form="attributes",
    parameters=[],
    examples=[
        "<hub_cron_list />",
    ],
    result_format="List of active cron jobs.",
    key_rules=[
        "check before adding new cron jobs to avoid duplicates",
        "delete stale cron jobs with hub_cron_delete",
    ],
)

hub_cron_delete = ToolDefinition(
    name="hub-cron-delete",
    description="Delete a scheduled cron job.",
    category="hub",
    risk_level="medium",
    requires_permission=False,
    xml_tag="hub_cron_delete",
    xml_form="body",
    xml_body_param="job_id",
    parameters=[
        ToolParameter(
            name="job_id",
            type="string",
            description="Cron job ID to delete",
            required=True,
        ),
    ],
    examples=[
        "<hub_cron_delete>6127886a</hub_cron_delete>",
    ],
    result_format="Deletion confirmation.",
    key_rules=[
        "requires the job_id returned by hub_cron_add or shown in hub_cron_list",
        "always clean up cron jobs when the periodic task is no longer needed",
    ],
)


# ============================================================
# WORK LANES (FILE OWNERSHIP)
# ============================================================

lane_claim = ToolDefinition(
    name="lane-claim",
    description="Claim ownership of a file to prevent conflicts.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="lane_claim",
    xml_form="mixed",
    xml_attributes=["task"],
    xml_body_param="path",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="File path to claim",
            required=True,
        ),
        ToolParameter(
            name="task",
            type="string",
            description="Description of what you're doing with the file",
            required=False,
        ),
    ],
    examples=[
        "<lane_claim>path/to/file.py</lane_claim>",
        '<lane_claim task="refactoring">path/to/file.py</lane_claim>',
    ],
    result_format="Claim confirmation or conflict warning.",
    key_rules=[
        "claim before editing to prevent conflicts with other agents",
        "release when done to unblock peers",
    ],
)

lane_release = ToolDefinition(
    name="lane-release",
    description="Release file ownership.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="lane_release",
    xml_form="body",
    xml_body_param="path",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="File path to release",
            required=True,
        ),
    ],
    examples=[
        "<lane_release>path/to/file.py</lane_release>",
    ],
    result_format="Release confirmation.",
    key_rules=[
        "release files when done to unblock other agents",
        "forgetting to release blocks other agents from editing that file",
    ],
)


# ============================================================
# CHANGE FEED
# ============================================================

file_changed = ToolDefinition(
    name="file-changed",
    description="Notify peers about a file change.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="file_changed",
    xml_form="body",
    xml_body_param="path",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="Path of the changed file",
            required=True,
        ),
    ],
    examples=[
        "<file_changed>path/to/file.py</file_changed>",
    ],
    result_format="Change notification sent.",
    key_rules=[
        "call after modifying a file so watching agents get notified",
        "other agents watching the file will receive the notification automatically",
    ],
)

file_watch = ToolDefinition(
    name="file-watch",
    description="Watch a file for changes.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="file_watch",
    xml_form="body",
    xml_body_param="path",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="Path of the file to watch",
            required=True,
        ),
    ],
    examples=[
        "<file_watch>path/to/file.py</file_watch>",
    ],
    result_format="Watch registered.",
    key_rules=[
        "you'll be notified when the file changes — useful for coordinating with other agents",
        "watch files you depend on or review frequently",
        "stop watching with file_unwatch when you no longer need updates",
    ],
)

file_unwatch = ToolDefinition(
    name="file-unwatch",
    description="Stop watching a file for changes.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="file_unwatch",
    xml_form="body",
    xml_body_param="path",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="Path of the file to stop watching",
            required=True,
        ),
    ],
    examples=[
        "<file_unwatch>path/to/file.py</file_unwatch>",
    ],
    result_format="Watch removed.",
    key_rules=[
        "clean up watches when you're done with a file to reduce noise",
    ],
)

feed_recent = ToolDefinition(
    name="feed-recent",
    description="View recent file changes.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="feed_recent",
    xml_form="attributes",
    xml_attributes=["limit"],
    parameters=[
        ToolParameter(
            name="limit",
            type="integer",
            description="Number of recent changes to show",
            required=False,
        ),
    ],
    examples=[
        '<feed_recent limit="10" />',
    ],
    result_format="Recent file changes.",
    key_rules=[
        "use to catch up on what changed while you were away or busy",
        "shows changes across all agents, not just your own",
    ],
)

feed_file = ToolDefinition(
    name="feed-file",
    description="View changes for a specific file.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="feed_file",
    xml_form="attributes",
    xml_attributes=["path"],
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="File path to check changes for",
            required=True,
        ),
    ],
    examples=[
        '<feed_file path="path/to/file.py" />',
    ],
    result_format="Change history for the file.",
    key_rules=[
        "check before editing to see if another agent recently modified the file",
        "combine with lane_claim to coordinate file ownership",
    ],
)


# ============================================================
# STATE
# ============================================================

state_update = ToolDefinition(
    name="state-update",
    description="Update your agent state visible to peers.",
    category="hub",
    risk_level="low",
    requires_permission=False,
    xml_tag="state_update",
    xml_form="body",
    xml_body_param="state",
    parameters=[
        ToolParameter(
            name="state",
            type="string",
            description="Current activity description",
            required=True,
        ),
    ],
    examples=[
        "<state_update>working on phase B tool registry migration</state_update>",
    ],
    result_format="State updated.",
    key_rules=[
        "your state is visible to other agents via hub_agents and hub_status",
        "keep it current — helps other agents know if you're busy or available",
        "be descriptive — 'working on phase E tool registry' is better than 'busy'",
    ],
)


def register_all():
    """Register all hub tool definitions."""
    registry = get_registry()

    # Messaging
    registry.register(hub_msg)
    registry.register(hub_broadcast)

    # Agent management
    registry.register(hub_stop)
    registry.register(hub_status)
    registry.register(hub_spawn)
    registry.register(hub_agents)
    registry.register(hub_capture)

    # Work queue
    registry.register(hub_queue)
    registry.register(hub_claim)
    registry.register(hub_work)
    registry.register(claims)

    # Vault / crystal
    registry.register(hub_vault)
    registry.register(hub_vaults)
    registry.register(vault_write)
    registry.register(crystal_search)
    registry.register(crystal_read)
    registry.register(crystal_list)
    registry.register(crystal_edit)
    registry.register(crystal_delete)

    # Cron
    registry.register(hub_cron_add)
    registry.register(hub_cron_list)
    registry.register(hub_cron_delete)

    # Work lanes
    registry.register(lane_claim)
    registry.register(lane_release)

    # Change feed
    registry.register(file_changed)
    registry.register(file_watch)
    registry.register(file_unwatch)
    registry.register(feed_recent)
    registry.register(feed_file)

    # State
    registry.register(state_update)


# Auto-register on import
register_all()
