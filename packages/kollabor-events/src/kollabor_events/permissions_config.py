"""Configuration defaults for permission system."""

# Permission configuration defaults
PERMISSION_CONFIG_DEFAULTS = {
    "kollabor": {
        "permissions": {
            # Master switch
            "enabled": True,
            # Default approval mode
            "approval_mode": "default",  # default, confirm_all, auto_approve_edits, trust_all
            # default = confirm high/unknown risk tools only
            # Audit logging
            "audit_log_enabled": True,
            "audit_log_path": "~/.kollab/logs/permissions.log",
            # Risk assessment
            "risk_assessment": {
                # Custom high-risk patterns (regex)
                "high_risk_patterns": [],
                # Custom medium-risk patterns (regex)
                "medium_risk_patterns": [],
                # Trusted tools (always auto-approve)
                "trusted_tools": [
                    "read_file",
                    "list_directory",
                    "search_file_content",
                    "glob",
                ],
                # Blocked tools (always deny)
                "blocked_tools": [],
                # Trust all MCP servers by default
                "trust_mcp_servers": False,
                # Trusted MCP server names
                "trusted_mcp_servers": [],
            },
            # UI settings
            "ui": {
                # Show risk level in confirmation
                "show_risk_level": True,
                # Show matched pattern in confirmation
                "show_matched_pattern": True,
                # Confirmation timeout (seconds, 0 = no timeout)
                "confirmation_timeout": 0,
                # Default response on timeout
                "timeout_response": "deny",
            },
        },
    }
}
