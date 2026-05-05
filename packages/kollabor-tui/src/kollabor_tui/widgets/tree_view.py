"""Tree view widget for displaying hierarchical data."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from kollabor_tui.design_system import T, TagBox
from kollabor_tui.key_parser import KeyPress
from kollabor_tui.visual_effects import ColorPalette

from .base_widget import BaseWidget


@dataclass
class TreeNode:
    """Represents a node in the tree.

    Attributes:
        id: Unique identifier for the node.
        label: Display text for the node.
        children: List of child nodes.
        expanded: Whether the node is expanded (showing children).
        icon: Optional icon character to display.
        data: Optional user data associated with the node.
    """

    id: str
    label: str
    children: List["TreeNode"] = field(default_factory=list)
    expanded: bool = False
    icon: str = ""
    data: Any = None

    def has_children(self) -> bool:
        """Check if node has children.

        Returns:
            True if node has at least one child.
        """
        return len(self.children) > 0

    def add_child(self, child: "TreeNode"):
        """Add a child node.

        Args:
            child: Child node to add.
        """
        self.children.append(child)

    def find_child(self, child_id: str) -> "TreeNode | None":
        """Find child node by ID.

        Args:
            child_id: ID of child to find.

        Returns:
            Child node or None if not found.
        """
        for child in self.children:
            if child.id == child_id:
                return child
            found = child.find_child(child_id)
            if found:
                return found
        return None


class TreeViewWidget(BaseWidget):
    """Widget for displaying and navigating hierarchical data.

    Supports expand/collapse, cursor navigation, and selection.
    Ideal for project structure, conversation history, and plugin dependencies.

    Example config:
    {
        "label": "Project Structure",
        "tree_data": {...},  # TreeNode or nested dict structure
        "expand_depth": 1,    # Auto-expand to this depth
        "show_icons": True,
        "selectable": True
    }
    """

    def __init__(self, config: dict, config_path: str, config_service=None):
        """Initialize tree view widget.

        Args:
            config: Widget configuration with tree data and display options.
            config_path: Dot-notation path to config value.
            config_service: ConfigService instance for reading/writing values.
        """
        super().__init__(config, config_path, config_service)

        # Tree data
        tree_data = self.config.get("tree_data")
        if isinstance(tree_data, TreeNode):
            self.root = tree_data
        elif isinstance(tree_data, dict):
            self.root = self._dict_to_tree(tree_data)
        else:
            self.root = TreeNode(id="root", label="Empty Tree")

        # Display options
        self.expand_depth = self.config.get("expand_depth", 0)
        self.show_icons = self.config.get("show_icons", True)
        self.selectable = self.config.get("selectable", True)
        self.show_root = self.config.get("show_root", True)

        # Icons
        self.folder_icon = self.config.get("folder_icon", "📁")
        self.file_icon = self.config.get("file_icon", "📄")
        self.expanded_icon = self.config.get("expanded_icon", "▼")
        self.collapsed_icon = self.config.get("collapsed_icon", "▶")

        # State
        self.cursor_path: List[str] = []  # Path to cursor node
        self.selected_ids: Set[str] = set()
        self.scroll_row = 0

        # Colors
        self.colors = ColorPalette()

        # Auto-expand to configured depth
        self._auto_expand(self.root, 0, self.expand_depth)

    def render(self) -> List[str]:
        """Render tree view widget.

        Returns:
            List of strings representing widget display lines.
        """
        lines = []

        # Label line
        label = self.get_label()
        if label:
            label_color = (
                self.colors.accent_color if self.focused else self.colors.primary_color
            )
            lines.append(f"{label_color}{label}{self.colors.reset}")

        # Build visible nodes
        visible_nodes = self._build_visible_nodes()

        # Render each visible node
        for i, (node, depth, is_last) in enumerate(
            visible_nodes[self.scroll_row : self.scroll_row + 15]
        ):
            # Build indentation
            indent = "  " * depth

            # Expand/collapse indicator
            if node.has_children():
                indicator = (
                    self.expanded_icon if node.expanded else self.collapsed_indicator()
                )
                connector = "├─" if not is_last else "└─"
            else:
                indicator = " "
                connector = "├─" if not is_last else "└─"

            # Icon
            if self.show_icons:
                icon = (
                    node.icon
                    if node.icon
                    else (self.folder_icon if node.has_children() else self.file_icon)
                )
            else:
                icon = ""

            # Highlight if selected or focused
            is_cursor = self._is_cursor_node(node)
            is_selected = node.id in self.selected_ids

            if is_cursor and self.focused:
                prefix = f"{self.colors.highlight}{self.colors.background}▶ "
            elif is_selected:
                prefix = f"{self.colors.success_color}* "
            else:
                prefix = "  "

            # Node label
            label_text = f"{indent}{prefix}{connector}{indicator} {icon} {node.label}"

            # Apply colors
            if is_cursor and self.focused:
                label_text = f"{label_text}{self.colors.reset}"
            elif is_selected:
                label_text = f"{label_text}{self.colors.reset}"
            elif node.has_children():
                label_text = (
                    f"{self.colors.primary_color}{label_text}{self.colors.reset}"
                )

            lines.append(label_text)

        # Scroll indicator
        if len(visible_nodes) > 15:
            show_start = self.scroll_row + 1
            show_end = min(self.scroll_row + 15, len(visible_nodes))
            scroll_info = (
                f"{self.colors.muted_color}Showing {show_start}-{show_end} of "
                f"{len(visible_nodes)}{self.colors.reset}"
            )
            lines.append(scroll_info)

        # Help text when focused
        if self.focused:
            if self.selectable:
                help_text = (
                    f"{self.colors.muted_color}↑↓: Navigate | Enter/Space: "
                    f"Expand/Select | Esc: Collapse{self.colors.reset}"
                )
            else:
                help_text = (
                    f"{self.colors.muted_color}↑↓: Navigate | Enter: "
                    f"Expand/Collapse{self.colors.reset}"
                )
            lines.append(help_text)

        return lines

    def render_modern(self, width: int = 50) -> List[str]:  # type: ignore[override]
        """Render tree view with modern design system styling.

        Uses TagBox for label and tree nodes with expand/collapse indicators.

        Args:
            width: Total width of tree view widget (default: 50).

        Returns:
            List containing tree view display lines with modern styling.
        """
        lines = []
        label = self.get_label()
        tag_width = 3
        content_width = width - tag_width

        # Label line with tree icon
        if label:
            icon = " 🌳 " if self.focused else "   "
            tag_bg = T().primary[0] if self.focused else T().dark[0]
            tag_fg = T().text_dark if self.focused else T().text_dim
            content_colors = T().input_bg if self.focused else T().dark[0]
            content_fg = T().text if self.focused else T().text_dim

            content = f" {label}"
            label_line = TagBox.render(
                lines=[content],
                tag_bg=tag_bg,
                tag_fg=tag_fg,
                tag_width=tag_width,
                content_colors=content_colors,
                content_fg=content_fg,
                content_width=content_width,
                tag_chars=[icon],
                use_gradient=self.focused,
            )
            lines.append(label_line)

        # Build visible nodes
        visible_nodes = self._build_visible_nodes()

        # Render each visible node
        for i, (node, depth, is_last) in enumerate(
            visible_nodes[self.scroll_row : self.scroll_row + 15]
        ):
            # Build indentation
            indent = "  " * depth

            # Expand/collapse indicator
            if node.has_children():
                indicator = (
                    self.expanded_icon if node.expanded else self.collapsed_indicator()
                )
            else:
                indicator = " "

            # Icon
            if self.show_icons:
                icon = (
                    node.icon
                    if node.icon
                    else (self.folder_icon if node.has_children() else self.file_icon)
                )
            else:
                icon = ""

            # Highlight if selected or focused
            is_cursor = self._is_cursor_node(node)
            is_selected = node.id in self.selected_ids

            # Build tag based on state
            if is_cursor and self.focused:
                tag_icon = " ▶ "
                tag_bg = T().primary[0]
                tag_fg = T().text_dark
            elif is_selected:
                tag_icon = " ★ "
                tag_bg = T().success[0]
                tag_fg = T().text_dark
            else:
                tag_icon = "   "
                tag_bg = T().dark[0]
                tag_fg = T().text_dim

            # Content colors
            if is_cursor and self.focused:
                content_colors = T().input_bg
                content_fg = T().text
            elif is_selected:
                content_colors = T().success
                content_fg = T().text_dark
            elif node.has_children():
                content_colors = T().primary
                content_fg = T().text
            else:
                content_colors = T().dark[0]
                content_fg = T().text_dim

            # Node label with indentation and indicator
            node_label = f"{indent}{indicator} {icon} {node.label}"
            content = f" {node_label}"

            node_line = TagBox.render(
                lines=[content],
                tag_bg=tag_bg,
                tag_fg=tag_fg,
                tag_width=tag_width,
                content_colors=content_colors,
                content_fg=content_fg,
                content_width=content_width,
                tag_chars=[tag_icon],
                use_gradient=(is_cursor and self.focused) or is_selected,
            )
            lines.append(node_line)

        # Scroll indicator
        if len(visible_nodes) > 15:
            show_start = self.scroll_row + 1
            show_end = min(self.scroll_row + 15, len(visible_nodes))
            scroll_info = (
                f"     {T().fg(T().dim[0])}Showing {show_start}-{show_end} of "
                f"{len(visible_nodes)}{T().reset}"
            )
            lines.append(scroll_info)

        # Help text when focused
        if self.focused:
            if self.selectable:
                help_text = (
                    f"     {T().fg(T().dim[0])}↑↓: Navigate | Enter/Space: "
                    f"Expand/Select | Esc: Collapse{T().reset}"
                )
            else:
                help_text = (
                    f"     {T().fg(T().dim[0])}↑↓: Navigate | Enter: "
                    f"Expand/Collapse{T().reset}"
                )
            lines.append(help_text)

        return lines

    def handle_input(self, key_press: KeyPress) -> bool:
        """Handle keyboard input.

        Args:
            key_press: Key press event to handle.

        Returns:
            True if key press was handled by this widget.
        """
        # Navigation
        if key_press.key == "up" and key_press.is_cursor_key:
            self._move_cursor(-1)
            return True

        if key_press.key == "down" and key_press.is_cursor_key:
            self._move_cursor(1)
            return True

        if key_press.key == "left" and key_press.is_cursor_key:
            self._collapse_or_move_parent()
            return True

        if key_press.key == "right" and key_press.is_cursor_key:
            self._expand_or_move_child()
            return True

        # Expand/collapse with space
        if key_press.key == " ":
            cursor_node = self._get_cursor_node()
            if cursor_node and cursor_node.has_children():
                cursor_node.expanded = not cursor_node.expanded
            return True

        # Select with Enter
        if key_press.key == "enter":
            cursor_node = self._get_cursor_node()
            if cursor_node:
                if self.selectable:
                    self._toggle_selection(cursor_node.id)
                elif cursor_node.has_children():
                    cursor_node.expanded = not cursor_node.expanded
            return True

        # Collapse all with Ctrl+C
        if key_press.key == "c" and key_press.ctrl:
            self._collapse_all(self.root)
            return True

        # Expand all with Ctrl+E
        if key_press.key == "e" and key_press.ctrl:
            self._expand_all(self.root)
            return True

        return False

    def _dict_to_tree(self, data: Dict, parent_id: str = "root") -> TreeNode:
        """Convert nested dict to TreeNode structure.

        Args:
            data: Nested dictionary with tree data.
            parent_id: Parent node ID.

        Returns:
            Root TreeNode.
        """
        root = TreeNode(id=parent_id, label=str(data.get("label", parent_id)))

        for key, value in data.items():
            if key == "label":
                continue

            if isinstance(value, dict):
                child = self._dict_to_tree(value, key)
                root.add_child(child)
            else:
                # Leaf node
                leaf = TreeNode(id=key, label=str(value))
                root.add_child(leaf)

        return root

    def _build_visible_nodes(self) -> List[tuple]:
        """Build list of visible nodes with depth and position info.

        Returns:
            List of (node, depth, is_last) tuples.
        """
        visible = []

        def traverse(node: TreeNode, depth: int):
            for i, child in enumerate(node.children):
                is_last = i == len(node.children) - 1
                visible.append((child, depth, is_last))

                if child.expanded and child.has_children():
                    traverse(child, depth + 1)

        if self.show_root:
            visible.append((self.root, 0, True))

        if self.root.expanded:
            traverse(self.root, 1)

        return visible

    def _move_cursor(self, direction: int):
        """Move cursor up or down.

        Args:
            direction: -1 for up, 1 for down.
        """
        visible_nodes = self._build_visible_nodes()

        if not self.cursor_path:
            # No cursor, set to first or last node
            if visible_nodes:
                if direction > 0:
                    self.cursor_path = [visible_nodes[0][0].id]
                else:
                    self.cursor_path = [visible_nodes[-1][0].id]
            return

        # Find current cursor position
        current_index = -1
        for i, (node, _, _) in enumerate(visible_nodes):
            if node.id == self.cursor_path[-1]:
                current_index = i
                break

        if current_index == -1:
            return

        # Move cursor
        new_index = current_index + direction
        if 0 <= new_index < len(visible_nodes):
            new_node = visible_nodes[new_index][0]
            self.cursor_path = self._get_path_to_node(new_node)

            # Adjust scroll if needed
            if new_index < self.scroll_row:
                self.scroll_row = new_index
            elif new_index >= self.scroll_row + 15:
                self.scroll_row = new_index - 14

    def _collapse_or_move_parent(self):
        """Collapse current node or move to parent."""
        cursor_node = self._get_cursor_node()
        if not cursor_node:
            return

        if cursor_node.expanded and cursor_node.has_children():
            cursor_node.expanded = False
        elif len(self.cursor_path) > 1:
            # Move to parent
            self.cursor_path.pop()

    def _expand_or_move_child(self):
        """Expand current node or move to first child."""
        cursor_node = self._get_cursor_node()
        if not cursor_node:
            return

        if not cursor_node.expanded and cursor_node.has_children():
            cursor_node.expanded = True
        elif cursor_node.has_children():
            # Move to first child
            self.cursor_path.append(cursor_node.children[0].id)

    def _toggle_selection(self, node_id: str):
        """Toggle selection for node.

        Args:
            node_id: ID of node to toggle.
        """
        if node_id in self.selected_ids:
            self.selected_ids.remove(node_id)
        else:
            self.selected_ids.add(node_id)

    def _get_cursor_node(self) -> TreeNode | None:
        """Get node at current cursor position.

        Returns:
            TreeNode at cursor or None.
        """
        if not self.cursor_path:
            return None

        node: TreeNode | None = self.root
        for node_id in self.cursor_path[1:]:
            node = node.find_child(node_id) if node else None
            if not node:
                return None

        return node

    def _is_cursor_node(self, node: TreeNode) -> bool:
        """Check if node is at cursor position.

        Args:
            node: Node to check.

        Returns:
            True if node is cursor position.
        """
        return bool(self.cursor_path) and node.id == self.cursor_path[-1]

    def _get_path_to_node(self, target_node: TreeNode) -> List[str]:
        """Get path from root to node.

        Args:
            target_node: Target node.

        Returns:
            List of node IDs from root to target.
        """
        path = []

        def find_path(node: TreeNode, target_id: str) -> bool:
            path.append(node.id)

            if node.id == target_id:
                return True

            for child in node.children:
                if find_path(child, target_id):
                    return True

            path.pop()
            return False

        find_path(self.root, target_node.id)
        return path

    def _auto_expand(self, node: TreeNode, current_depth: int, max_depth: int):
        """Auto-expand nodes to specified depth.

        Args:
            node: Node to expand.
            current_depth: Current depth in tree.
            max_depth: Maximum depth to auto-expand.
        """
        if current_depth < max_depth and node.has_children():
            node.expanded = True
            for child in node.children:
                self._auto_expand(child, current_depth + 1, max_depth)

    def _collapse_all(self, node: TreeNode):
        """Collapse all nodes.

        Args:
            node: Node to collapse (recursively).
        """
        node.expanded = False
        for child in node.children:
            self._collapse_all(child)

    def _expand_all(self, node: TreeNode):
        """Expand all nodes.

        Args:
            node: Node to expand (recursively).
        """
        if node.has_children():
            node.expanded = True
            for child in node.children:
                self._expand_all(child)

    def collapsed_indicator(self) -> str:
        """Get indicator for collapsed node.

        Returns:
            Collapsed indicator character.
        """
        return str(self.collapsed_icon)

    def get_value(self) -> Any:
        """Get selected node IDs.

        Returns:
            List of selected node IDs.
        """
        return list(self.selected_ids)
