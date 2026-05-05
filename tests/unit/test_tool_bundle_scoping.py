"""Test bundle tool scoping with registry."""

from kollabor_agent.tool_registry import ToolRegistry, get_registry


class TestBundleScoping:
    """Verify bundle tool lists map correctly to registry entries."""

    @classmethod
    def setup_class(cls):
        ToolRegistry.reset()
        cls.registry = get_registry()

    def test_research_bundle_read_only(self):
        """Research bundle has no write tools."""
        tools = self.registry.get_for_bundle(["terminal", "file-read", "file-grep"])
        names = {t.name for t in tools}
        assert "file-read" in names
        assert "file-grep" in names
        assert "terminal" in names
        # Should NOT have write tools
        assert "file-edit" not in names
        assert "file-create" not in names
        assert "file-delete" not in names

    def test_coder_bundle_has_all_registry_tools(self):
        """Coder bundle covers all 15 registry tools."""
        bundle_tools = [
            "terminal", "file-read", "file-edit", "file-create",
            "file-create-overwrite", "file-delete", "file-append",
            "file-insert-after", "file-insert-before", "file-move",
            "file-copy", "file-copy-overwrite", "file-grep",
            "directory", "directory-remove",
        ]
        tools = self.registry.get_for_bundle(bundle_tools)
        assert len(tools) == 15  # All registry tools covered

    def test_git_is_registered_as_doc_tool(self):
        """'git' is registered as a doc-only tool (maps to terminal)."""
        tools = self.registry.get_for_bundle(["git"])
        assert len(tools) == 1
        assert tools[0].name == "git"
        assert tools[0].xml_tag_name == "terminal"  # delegates to terminal

    def test_unknown_tools_are_silently_skipped(self):
        """Unknown tool names don't cause errors, just warnings."""
        tools = self.registry.get_for_bundle(["file-read", "nonexistent", "also-fake"])
        assert len(tools) == 1
        assert tools[0].name == "file-read"
