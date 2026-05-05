"""ToolDefinition — single source of truth for tool metadata.

Every tool in kollab is described by one ToolDefinition
instance. From this one instance, kollabor generates:

- The native OpenAI/Anthropic function schema (JSON dict)
- The XML regex pattern used by response_parser
- The markdown documentation rendered into agent system prompts

See tool_generators/ for the individual generators.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolParameter:
    """One parameter on a tool."""

    name: str
    type: str  # JSON schema type: "string", "integer", "boolean", "array", "object"
    description: str
    required: bool = False
    default: Any = None
    enum: Optional[List[Any]] = None
    items: Optional[Dict[str, Any]] = None  # For array types
    properties: Optional[Dict[str, Any]] = None  # For object types

    def to_json_schema(self) -> Dict[str, Any]:
        """Convert to a JSON schema fragment (for native mode)."""
        schema: Dict[str, Any] = {
            "type": self.type,
            "description": self.description,
        }
        if self.default is not None:
            schema["default"] = self.default
        if self.enum is not None:
            schema["enum"] = self.enum
        if self.items is not None:
            schema["items"] = self.items
        if self.properties is not None:
            schema["properties"] = self.properties
        return schema


@dataclass
class ToolDefinition:
    """Complete definition of a kollabor tool.

    Canonical names use hyphenated form matching XML tags:
    file-read, file-edit, terminal, hub-msg, etc.

    The native_name property converts to underscore form for
    OpenAI/Anthropic native tool calls (file_read, file_edit).
    """

    # Identity
    name: str
    """Canonical name. Hyphenated form matching XML tag.
    E.g. 'file-read', 'terminal', 'hub-msg'."""

    description: str
    """One-line description. Appears in native JSON and markdown."""

    # Parameters
    parameters: List[ToolParameter] = field(default_factory=list)
    """All parameters this tool accepts."""

    # XML form
    xml_tag: str = ""
    """XML tag name. Defaults to name if empty.
    E.g. file-read -> <read>, terminal -> <terminal>"""

    xml_form: str = "body"
    """How the XML tag is structured:
    - 'body': single-value tag body, e.g. <terminal>cmd</terminal>
    - 'nested': nested sub-elements for each param,
      e.g. <read><file>x</file></read>
    - 'attributes': XML attributes for each param
    - 'mixed': some params as attributes, some nested
    """

    xml_body_param: Optional[str] = None
    """For xml_form='body'/'mixed', the param name the body text maps to."""

    xml_attributes: List[str] = field(default_factory=list)
    """Params encoded as XML attributes in mixed/attributes form."""

    # Scope / permissions
    category: str = "general"
    """Grouping: 'file_ops', 'terminal', 'hub', 'scratchpad', etc."""

    risk_level: str = "low"
    """'low', 'medium', 'high'. Used by permission system."""

    requires_permission: bool = False
    """True if permission system prompts before each invocation."""

    # Docs
    examples: List[str] = field(default_factory=list)
    """XML usage examples for markdown docs."""

    notes: str = ""
    """Extended notes for markdown docs."""

    result_format: str = ""
    """Description of what the tool returns on success."""

    error_modes: List[str] = field(default_factory=list)
    """Known error cases for docs."""

    safety_features: List[str] = field(default_factory=list)
    """Safety guarantees the tool provides.
    E.g. 'auto backups: .bak before edits'"""

    key_rules: List[str] = field(default_factory=list)
    """Critical usage rules the agent must follow.
    E.g. 'replaces ALL matches — use surrounding context to make pattern unique'"""

    anti_patterns: List[str] = field(default_factory=list)
    """Wrong vs correct usage examples.
    Each entry is a two-line string: WRONG line then CORRECT line.
    E.g. 'WRONG:   sed -i ...\\nCORRECT: {edit}...'"""

    # Execution
    handler: Optional[Callable] = None
    """Async handler: async def handler(params, context) -> ToolResult"""

    # Override for native name if different from auto-derived
    _native_name_override: Optional[str] = field(default=None, repr=False)
    """Explicit override for native_name. Used when native name differs
    from the hyphen-to-underscore conversion. E.g. directory -> file_mkdir."""

    @property
    def native_name(self) -> str:
        """Convert canonical name to native tool name (underscore).
        file-read -> file_read, terminal -> terminal.
        Uses _native_name_override if set."""
        if self._native_name_override:
            return self._native_name_override
        return self.name.replace("-", "_")

    @property
    def xml_tag_name(self) -> str:
        """Get the actual XML tag name. Falls back to name."""
        return self.xml_tag or self.name

    def to_json_schema(self) -> Dict[str, Any]:
        """Generate native JSON schema for OpenAI/Anthropic."""
        properties = {p.name: p.to_json_schema() for p in self.parameters}
        required = [p.name for p in self.parameters if p.required]
        schema: Dict[str, Any] = {
            "name": self.native_name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": properties,
            },
        }
        if required:
            schema["parameters"]["required"] = required
        return schema

    def get_xml_regex(self) -> str:
        """Generate the regex pattern for this tool's XML form."""
        from .tool_generators.xml_regex import build_regex_for_tool
        return build_regex_for_tool(self)

    def to_markdown(self) -> str:
        """Generate markdown documentation for this tool."""
        from .tool_generators.markdown import render_tool_markdown
        return render_tool_markdown(self)
