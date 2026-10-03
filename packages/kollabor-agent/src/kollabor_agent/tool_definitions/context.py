"""Context management tool definitions.

Tools for managing what stays in the context window.
"""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry

# --- curate ---
curate = ToolDefinition(
    name="curate",
    description=(
        "Replace verbose content in context with a summary. "
        "Use to manage context window when it gets heavy."
    ),
    category="context",
    risk_level="low",
    requires_permission=False,
    xml_tag="curate",
    xml_form="mixed",
    xml_attributes=["id", "decision"],
    parameters=[
        ToolParameter(
            name="id",
            type="string",
            description="Identifier for the content to curate",
            required=True,
        ),
        ToolParameter(
            name="decision",
            type="string",
            description="Action: 'keep' or 'summary'",
            required=True,
            enum=["keep", "summary"],
        ),
        ToolParameter(
            name="summary",
            type="string",
            description="Summary of heavy content to replace original",
            required=False,
        ),
    ],
    examples=[
        '<curate id="file-read-large" decision="summary">Large file contained X, Y, Z</curate>',
    ],
    result_format="Confirmation that content was curated.",
    notes="Use when context window is getting large and you want to compact.",
    key_rules=[
        "use 'keep' for files you're actively editing or data you need to reference exactly",
        "use 'summary' for material you've already extracted what you need from — "
        "your own summary is higher quality than the generic fallback",
        "last-write-wins — emit a new <curate> with the same id to change a prior decision",
    ],
    safety_features=[
        "free operation — no cache cost, in-memory flag flip only",
    ],
)

# --- context-query ---
context_query = ToolDefinition(
    name="context-query",
    description="Query the context ledger to see what content is loaded.",
    category="context",
    risk_level="low",
    requires_permission=False,
    xml_tag="context_query",
    xml_form="body",
    xml_body_param="query",
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Natural language query about context state",
            required=True,
        ),
    ],
    examples=[
        "<context_query>what files have I read?</context_query>",
    ],
    result_format="Results from context ledger matching the query.",
    safety_features=[
        "read-only, no side effects",
    ],
)

# --- evict ---
evict = ToolDefinition(
    name="evict",
    description="Remove content from the context window by identifier.",
    category="context",
    risk_level="low",
    requires_permission=False,
    xml_tag="evict",
    xml_form="body",
    xml_body_param="identifier",
    parameters=[
        ToolParameter(
            name="identifier",
            type="string",
            description="Identifier of content to remove from context",
            required=True,
        ),
    ],
    examples=[
        "<evict>large-file-content-1</evict>",
    ],
    result_format="Confirmation that content was evicted.",
    notes="Evicted content is gone from context window. Use curate instead if you need a summary.",
    key_rules=[
        "eviction breaks prefix cache from that message forward — "
        "only use when the session has >=10 more turns AND the entry is >=32KB",
        "prefer <curate> with summary over <evict> when possible",
    ],
)


# --- compact ---
compact = ToolDefinition(
    name="compact",
    description=(
        "Start context compaction now. Old messages are replaced by a summary; "
        "your notes are kept verbatim at the top of your context."
    ),
    category="context",
    risk_level="low",
    requires_permission=False,
    xml_tag="compact",
    xml_form="body",
    xml_body_param="notes",
    parameters=[
        ToolParameter(
            name="notes",
            type="string",
            description=(
                "What you must remember: current task, decisions, file paths, next step"
            ),
            required=False,
        ),
    ],
    examples=[
        "<compact>task: fix login bug in auth.py; done: repro + root cause; next: patch verify_token</compact>",
    ],
    result_format="Confirmation that compaction started; the turn ends.",
    notes="Curate heavy items first; unmarked ones are auto-summarized.",
    key_rules=[
        "use when asked by the compaction prompt or /compact, or when you are at a clean break",
        "your <compact> notes are the only text guaranteed to survive verbatim",
    ],
)


def register_all():
    """Register all context tool definitions."""
    registry = get_registry()
    registry.register(curate)
    registry.register(context_query)
    registry.register(evict)
    registry.register(compact)


# Auto-register on import
register_all()
