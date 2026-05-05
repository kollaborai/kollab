---
title: "Documentation"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Documentation

## Getting Started

- [Getting Started](getting-started.md) - Installation, first run, quick start
- [Configuration](configuration.md) - Config files, environment variables, settings
- [Profiles](features/profiles.md) - LLM profile management and switching
- [Providers](providers.md) - Anthropic, OpenAI, Gemini, Azure, OpenRouter, Ollama, custom
- [Release Process](release-process.md) - Maintainer checklist for public releases

## Features

- [Slash Commands](features/slash-commands.md) - All commands and how to add custom ones
- [Agents](features/agents.md) - Agent system, bundles, custom agents
- [Tools](features/tools.md) - Tool calling, built-in tools, custom tools
- [MCP](features/mcp.md) - Model Context Protocol integration
- [Permissions](features/permissions.md) - Tool approval modes and risk assessment
- [Pipe Mode](features/pipe-mode.md) - Scripting and automation
- [Conversations](features/conversations.md) - Logging, storage, and resume
- [Tmux](features/tmux.md) - Terminal session management
- [Theming](features/theming.md) - Color modes, design system, terminal compatibility
- [Kollaborate](features/kollaborate.md) - Multi-agent parallel development framework
- [Config Hooks](features/config-hooks.md) - JSON-based hooks (no Python needed)
- [Dynamic System Prompts](features/dynamic-system-prompts.md) - Runtime prompt rendering
- [Question Gate Protocol](features/question-gate-protocol.md) - Tool suspension on agent questions

## Plugins

- [Overview](plugins/overview.md) - What plugins can do
- [Development Guide](plugins/development.md) - How to write a plugin
- [Hooks Reference](plugins/hooks-reference.md) - Event types, priorities, context objects

## Architecture

- [Architecture Index](architecture/README.md) - Canonical architecture docs plus ADRs, RFCs, records, and archive
- [Overview](architecture/architecture-overview.md) - Monorepo structure, packages, data flow
- [Terminal Rendering](architecture/terminal-rendering-architecture.md) - Render system, input, design system
- [Event System](architecture/event-system-architecture.md) - Event bus, hook registry, execution

## MCP Reference

- [MCP Setup](mcp/MCP_SETUP.md) - Server configuration
- [MCP Server Examples](mcp/MCP_SERVER_EXAMPLES.md) - Example server implementations

## Other

- [FAQ](faq.md) - Frequently asked questions
- [Troubleshooting](troubleshooting.md) - Common issues, logs, debugging
- [Contributing](../CONTRIBUTING.md) - How to contribute
- [Support](../SUPPORT.md) - Where to ask for help and what information to include
- [Agent Guide](../AGENTS.md) and [Architecture Details](../CLAUDE.md) - Developer references and coding standards
