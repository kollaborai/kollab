"""File operation tool definitions.

All 14 file/terminal tools from mcp_integration.py _get_file_operation_tools()
and the 15 hand-written regex patterns in response_parser.py.

Canonical names use hyphenated form (file-read, file-edit) matching
the existing bundle agent.json conventions.
"""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry


# --- file-read ---
file_read = ToolDefinition(
    name="file-read",
    description=(
        "Read content from a file. Use this to examine existing "
        "files before editing."
    ),
    category="file_ops",
    risk_level="low",
    requires_permission=False,
    xml_tag="read",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="file",
            type="string",
            description="Relative file path to read",
            required=True,
        ),
        ToolParameter(
            name="offset",
            type="integer",
            description="Line offset to start reading from (0-indexed, optional)",
            required=False,
        ),
        ToolParameter(
            name="limit",
            type="integer",
            description="Number of lines to read (optional)",
            required=False,
        ),
    ],
    examples=[
        "<read><file>plugins/hub/plugin.py</file></read>",
        "<read><file>plugins/hub/plugin.py</file><offset>100</offset><limit>50</limit></read>",
    ],
    result_format=(
        "On success: file content with a success header showing "
        "path and line count. On error: 'error: <reason>'"
    ),
    error_modes=[
        "File not found: <path>",
        "File too large (max 10MB)",
        "Cannot read binary file",
    ],
    notes=(
        "File paths may be relative to the kollabor project root or absolute. "
        "Binary files are rejected."
    ),
    key_rules=[
        "use <read> instead of terminal cat — safer, tracked, validated",
        "use offset+limit for large files instead of reading the whole thing",
        "re-reading a file already in context returns a short 'stale hit' marker; use force=\"true\" for a fresh re-read",
    ],
    anti_patterns=[
        "WRONG:   <terminal>cat file.py</terminal>",
        "CORRECT: <read><file>file.py</file></read>",
    ],
)

# --- file-edit ---
file_edit = ToolDefinition(
    name="file-edit",
    description=(
        "Find and replace text in a file. Replaces ALL occurrences "
        "of the pattern."
    ),
    category="file_ops",
    risk_level="medium",
    requires_permission=False,
    xml_tag="edit",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="file",
            type="string",
            description="Relative file path to edit",
            required=True,
        ),
        ToolParameter(
            name="find",
            type="string",
            description="Text pattern to find (exact match)",
            required=True,
        ),
        ToolParameter(
            name="replace",
            type="string",
            description="Text to replace with",
            required=True,
        ),
    ],
    examples=[
        "<edit><file>src/main.py</file><find>old_func</find><replace>new_func</replace></edit>",
    ],
    result_format=(
        "Reports number of replacements made. Creates .bak backup."
    ),
    error_modes=[
        "File not found",
        "Pattern not found in file",
        "Syntax error after edit (auto-rollback)",
    ],
    safety_features=[
        "auto backups: .bak before edits",
        "python syntax validation with automatic rollback on errors",
        "protected files: kollabor/, main.py, .git/, venv/",
        "file size limits: 10MB edit, 5MB create",
    ],
    key_rules=[
        "replaces ALL matches — use surrounding context to make pattern unique",
        "whitespace in <find> must match exactly",
        "use file operations for code changes, terminal for git/pip/pytest",
    ],
    anti_patterns=[
        "WRONG:   <terminal>sed -i 's/old/new/' file.py</terminal>",
        "CORRECT: <edit><file>file.py</file><find>old</find><replace>new</replace></edit>",
    ],
)

# --- file-create ---
file_create = ToolDefinition(
    name="file-create",
    description="Create a new file with content. Fails if file already exists.",
    category="file_ops",
    risk_level="medium",
    requires_permission=False,
    xml_tag="create",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="file",
            type="string",
            description="Relative file path to create",
            required=True,
        ),
        ToolParameter(
            name="content",
            type="string",
            description="Content to write to the file",
            required=True,
        ),
    ],
    examples=[
        "<create><file>src/new_module.py</file><content>\"\"\"New module.\"\"\"</content></create>",
    ],
    result_format="Confirms file created with path.",
    error_modes=[
        "File already exists",
        "Cannot write to path",
    ],
    safety_features=[
        "auto backups: .bak before edits, .deleted before deletion",
        "file size limits: 5MB create",
    ],
    anti_patterns=[
        "WRONG:   <terminal>cat > file.py << 'EOF'\ncontent\nEOF</terminal>",
        "CORRECT: <create><file>file.py</file><content>content</content></create>",
    ],
)

# --- file-create-overwrite ---
file_create_overwrite = ToolDefinition(
    name="file-create-overwrite",
    description=(
        "Create or overwrite a file with content. Creates backup "
        "if file exists."
    ),
    category="file_ops",
    risk_level="medium",
    requires_permission=False,
    xml_tag="create_overwrite",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="file",
            type="string",
            description="Relative file path to create/overwrite",
            required=True,
        ),
        ToolParameter(
            name="content",
            type="string",
            description="Content to write to the file",
            required=True,
        ),
    ],
    examples=[
        "<create_overwrite><file>config.json</file><content>{}</content></create_overwrite>",
    ],
    safety_features=[
        "creates backup if file already exists",
    ],
    result_format="Confirms file created or overwritten with path.",
)

# --- file-delete ---
file_delete = ToolDefinition(
    name="file-delete",
    description="Delete a file. Creates backup before deletion.",
    category="file_ops",
    risk_level="high",
    requires_permission=False,
    xml_tag="delete",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="file",
            type="string",
            description="Relative file path to delete",
            required=True,
        ),
    ],
    examples=[
        "<delete><file>src/old_module.py</file></delete>",
    ],
    safety_features=[
        "auto backups: .deleted before deletion",
    ],
    result_format="Confirmation that file was deleted.",
)

# --- file-move ---
file_move = ToolDefinition(
    name="file-move",
    description="Move or rename a file.",
    category="file_ops",
    risk_level="medium",
    requires_permission=False,
    xml_tag="move",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="from",
            type="string",
            description="Source file path",
            required=True,
        ),
        ToolParameter(
            name="to",
            type="string",
            description="Destination file path",
            required=True,
        ),
    ],
    examples=[
        "<move><from>src/old.py</from><to>src/new.py</to></move>",
    ],
    result_format="Confirmation that file was moved/renamed.",
)

# --- file-copy ---
file_copy = ToolDefinition(
    name="file-copy",
    description="Copy a file. Fails if destination exists.",
    category="file_ops",
    risk_level="low",
    requires_permission=False,
    xml_tag="copy",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="from",
            type="string",
            description="Source file path",
            required=True,
        ),
        ToolParameter(
            name="to",
            type="string",
            description="Destination file path",
            required=True,
        ),
    ],
    examples=[
        "<copy><from>src/template.py</from><to>src/new_file.py</to></copy>",
    ],
    result_format="Confirmation that file was copied. Fails if destination exists.",
)

# --- file-copy-overwrite ---
file_copy_overwrite = ToolDefinition(
    name="file-copy-overwrite",
    description="Copy a file, overwriting destination if it exists.",
    category="file_ops",
    risk_level="medium",
    requires_permission=False,
    xml_tag="copy_overwrite",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="from",
            type="string",
            description="Source file path",
            required=True,
        ),
        ToolParameter(
            name="to",
            type="string",
            description="Destination file path",
            required=True,
        ),
    ],
    examples=[
        "<copy_overwrite><from>src/a.py</from><to>src/b.py</to></copy_overwrite>",
    ],
    result_format="Confirmation that file was copied, overwriting destination.",
)

# --- file-append ---
file_append = ToolDefinition(
    name="file-append",
    description="Append content to the end of a file.",
    category="file_ops",
    risk_level="low",
    requires_permission=False,
    xml_tag="append",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="file",
            type="string",
            description="Relative file path to append to",
            required=True,
        ),
        ToolParameter(
            name="content",
            type="string",
            description="Content to append",
            required=True,
        ),
    ],
    examples=[
        "<append><file>src/main.py</file><content>\n# Added later</content></append>",
    ],
    result_format="Confirmation that content was appended.",
)

# --- file-insert-after ---
file_insert_after = ToolDefinition(
    name="file-insert-after",
    description=(
        "Insert content after a pattern in a file. Pattern must "
        "match exactly."
    ),
    category="file_ops",
    risk_level="medium",
    requires_permission=False,
    xml_tag="insert_after",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="file",
            type="string",
            description="Relative file path",
            required=True,
        ),
        ToolParameter(
            name="pattern",
            type="string",
            description="Pattern to find (exact match)",
            required=True,
        ),
        ToolParameter(
            name="content",
            type="string",
            description="Content to insert after pattern",
            required=True,
        ),
    ],
    examples=[
        '<insert_after><file>main.py</file><pattern>class MyClass:</pattern><content>    """Docstring."""</content></insert_after>',
    ],
    key_rules=[
        "pattern must be UNIQUE — errors if 0 or 2+ matches",
        "whitespace in pattern must match exactly",
    ],
    result_format="Confirmation that content was inserted after the pattern.",
)

# --- file-insert-before ---
file_insert_before = ToolDefinition(
    name="file-insert-before",
    description=(
        "Insert content before a pattern in a file. Pattern must "
        "match exactly."
    ),
    category="file_ops",
    risk_level="medium",
    requires_permission=False,
    xml_tag="insert_before",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="file",
            type="string",
            description="Relative file path",
            required=True,
        ),
        ToolParameter(
            name="pattern",
            type="string",
            description="Pattern to find (exact match)",
            required=True,
        ),
        ToolParameter(
            name="content",
            type="string",
            description="Content to insert before pattern",
            required=True,
        ),
    ],
    examples=[
        '<insert_before><file>main.py</file><pattern>def main():</pattern><content># Entry point\n</content></insert_before>',
    ],
    key_rules=[
        "pattern must be UNIQUE — errors if 0 or 2+ matches",
        "whitespace in pattern must match exactly",
    ],
    result_format="Confirmation that content was inserted before the pattern.",
)

# --- directory-create ---
directory_create = ToolDefinition(
    name="directory",
    description="Create a directory (including parent directories).",
    _native_name_override="file_mkdir",
    category="file_ops",
    risk_level="low",
    requires_permission=False,
    xml_tag="mkdir",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="Directory path to create",
            required=True,
        ),
    ],
    examples=[
        "<mkdir><path>src/new_package</path></mkdir>",
    ],
    result_format="Confirmation that directory was created.",
)

# --- directory-remove ---
directory_remove = ToolDefinition(
    name="directory-remove",
    description="Remove an empty directory.",
    _native_name_override="file_rmdir",
    category="file_ops",
    risk_level="medium",
    requires_permission=False,
    xml_tag="rmdir",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description="Directory path to remove",
            required=True,
        ),
    ],
    examples=[
        "<rmdir><path>src/old_package</path></rmdir>",
    ],
    result_format="Confirmation that directory was removed. Fails if not empty.",
)

# --- file-grep ---
file_grep = ToolDefinition(
    name="file-grep",
    description=(
        "Search for a pattern in a file and return matching lines."
    ),
    category="file_ops",
    risk_level="low",
    requires_permission=False,
    xml_tag="grep",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="file",
            type="string",
            description="Relative file path to search",
            required=True,
        ),
        ToolParameter(
            name="pattern",
            type="string",
            description="Text pattern to search for",
            required=True,
        ),
        ToolParameter(
            name="case_insensitive",
            type="boolean",
            description="Whether to ignore case (default: false)",
            required=False,
        ),
    ],
    examples=[
        "<grep><file>src/main.py</file><pattern>def process</pattern></grep>",
    ],
    result_format="Matching lines from the file with line numbers.",
)


def register_all():
    """Register all file_ops tool definitions."""
    registry = get_registry()
    registry.register(file_read)
    registry.register(file_edit)
    registry.register(file_create)
    registry.register(file_create_overwrite)
    registry.register(file_delete)
    registry.register(file_move)
    registry.register(file_copy)
    registry.register(file_copy_overwrite)
    registry.register(file_append)
    registry.register(file_insert_after)
    registry.register(file_insert_before)
    registry.register(directory_create)
    registry.register(directory_remove)
    registry.register(file_grep)


# Auto-register on import
register_all()
