# Changelog

All notable changes to Kollab will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Agent Skills:** `Skill` loading now strictly enforces the published directory
  contract (`SKILL.md` frontmatter; `name` matches folder; required `description`;
  validated `metadata`, `allowed-tools`, and `compatibility`). Bundled caches bump
  to `CACHE_VERSION` 3 — delete `~/.kollab/agent_metadata.cache` only if stale
  agent metadata causes issues after upgrade.

## [1.0.1] - 2026-05-05

### Fixed

- Fixed first-run installed environments so bundled agents and skills are seeded
  from packaged wheel data, preventing fallback system-prompt mode.
- Fixed the installer `uv` path to install a persistent `kollab` tool instead
  of only running the command ephemerally.
- Added Docker smoke coverage for first-run bundled agent prompt seeding.

## [1.0.0] - 2026-05-05

### Added
- Added an installed-user Docker runtime validation guide and helper script/smoke tests
  for reproducible UI checks.

### Changed
- Expanded session and agent launch plumbing so agent profiles and MCP context pass
  through consistently for spawned agents and per-session tool execution.
- Hardened workspace handling for file operations and session/profile paths to reduce
  invalid or unsafe path usage in stricter trust modes.

### Fixed
- Improved conversation persistence by tracking file interactions and improving tool
  result metadata (`tool_result` + `tool_use_id`) in transcripts.
- Fixed `wait_for_user` parking behavior so bookkeeping tools can park cleanly without
  forcing unnecessary follow-up turns.
- Improved `/save` robustness with a fallback path when state services are not yet
  available during startup or attach mode.
- Fixed development entrypoint imports so `python main.py` prefers the current
  checkout's workspace packages instead of stale editable installs.

## [0.5.7] - 2026-04-24

### Added
- `kollabor-engine` README now documents the local-daemon service model, current
  API surface, known gaps, and service-hardening roadmap.
- Package READMEs now use the same current-role, architecture, known-gaps, and
  roadmap structure across `kollabor-ai`, `kollabor-agent`, `kollabor-config`,
  `kollabor-events`, `kollabor-plugins`, `kollabor-rpc`, `kollabor-tui`, and
  `kollabor-webui`.
- `kollabor-rpc` is now included in root package dependencies and the
  tag-publish workflow so attach-mode RPC installs and releases with the rest
  of the workspace.
- Pre-compaction checkpoints for auditability.
- Web attach protocol spec for structured event streams for programmatic clients.

### Changed
- Architecture docs reorganized into canonical taxonomy: archive, decisions,
  records, reference, and RFCs.
- CLI docs now expose the `--as` flag, document environment variables, and remove
  stale `-d` references.

### Fixed
- Session widget state refresher now populates `session_id` in daemon/attach mode.
- Dynamic prompt rendering skips `bd` commands when `.beads/` is absent, avoiding
  roughly 10 seconds of daemon startup delay.

## [0.5.6] - 2026-04-21

### Added
- **Phase 4.5 Daemon Transparency**: Unified state_service abstraction for local and attach mode
  - StateService protocol (LocalStateService, RemoteStateService) for daemon state access
  - Multi-context daemon architecture with `--context NAME` launch flag
  - ConversationContext + ContextRegistry with snapshot-and-swap (preserves list identity)
  - RPC methods: state.set_agent, state.activate_skill, state.deactivate_skill,
    state.set_system_prompt, state.clear_agent, state.restart_session,
    state.resume_conversation, state.enable/disable_mcp_server, state.test_mcp_server,
    state.get_mcp_tools, state.clear_session_approvals, state.clear_project_approvals,
    state.list_project_approvals, state.list_contexts, state.get_active_context,
    state.create_context, state.attach_to_context, state.archive_context,
    state.get_hub_status_text, state.get_hub_whoami_text, state.get_hub_work_text
  - CLI launch-flag routing: `--profile`, `--agent`, `--skill`, `--system-prompt`,
    `--save`, `--context` cross attach-client boundary
  - Thin-client attach path: RemoteStateService registered first in attach mode
  - 4 JSON tmux specs for local-mode smoke testing (18/18 assertions pass)

- **Tool Permission System**: Comprehensive approval and permission system for controlling tool execution
  - 4 approval modes: CONFIRM_ALL (default), DEFAULT, AUTO_APPROVE_EDITS, TRUST_ALL
  - Risk-based assessment with pattern matching for dangerous commands
  - Inline permission prompts in thinking/executing area (no modal interruption)
  - Single keypress responses (a/s/d/c/t/A) for quick approval/denial
  - Session-scoped approvals with "remember this session" option
  - `/permissions` command for runtime mode switching and statistics
  - Color-coded risk levels (HIGH=red, MEDIUM=yellow, LOW=green)
  - Event bus integration at SECURITY priority (900)
  - Statistics tracking (auto-approved, user-approved, denied, blocked)
  - Configuration via `kollabor.permissions.*` with defaults
  - 8 core files: manager, risk_assessor, hook, models, config, response_handler, UI component
  - Complete specification in `docs/specs/tool-permission-system-spec.md`
- **Hub socket authentication**: peer credentials, Ed25519 handshake support, and
  coordinator gatekeeper permissions.
- **Hub console and memory**: live socket feed, project/global crystal memory
  stores, project-scoped hub config, and auto-grant/revoke environment queue
  producers.
- **Context and notification services**: context-service hub bridge MVP,
  file-read deduplication metadata, and agent notification queue/render/tag/wake
  phases.
- **Parser and daemon defaults**: daemon mode is now the default for interactive
  sessions, with parser protections that strip code/backtick blocks before XML
  tag scanning.

### Changed
- /agent, /skills, /restart, /mcp, /permissions, /resume, /status all route through state_service
- Status widgets prefer remote_state when available (attach mode shows daemon state)
- Legacy fallbacks removed from step 7 commands (state_service is the only path)
- HubStatusView migrated to read-only state.get_hub_status_text RPC
- Hub auth defaults off by default for raw-client compatibility.
- Hub loop thresholds, coordinator breakthrough behavior, and default configs now
  wire to actual runtime behavior.

### Fixed
- `--profile openai-oauth` silently fell back to default (ProfileManager init race condition)
- MCP plugin passed app=None to MCPCommandHandler (masked by pre-step-7 fallback)
- /resume broke conversation_history list identity (clear+extend preserves cached references)
- /hub -h / --help / no-args now prints hub help instead of generic help
- Config defaults that were missing or wired to the wrong keys.
- Slash command handler smoke coverage for all 21 command handlers.
- Parser code-span preservation for file operations and tool outputs.
- Attach-mode tool-call boxes when assistant content is empty.
- Context-service event-loop capture and file_path propagation in tool metadata.
- Hub bridge initialization races, waiting-state scheduling, vault scoping, and
  crystal tag parsing edge cases.
- Text utility regression by restoring `generate_ngrams` and relevance scoring.

### Deferred to Phase 4.6
- /login OAuth browser split (client runs browser, daemon stores token)
- /hub msg, broadcast, stop, spawn, org cross-process messaging from attach client
- /terminal view, attach streaming transport
- /sub completion notification (MessageInjector deprecation)
- /resume modal, search, branch, filter paths
- MCP hot-reload on config change
- Full thin-client refactor (skip ProfileManager/AgentManager in attach)
- 176-reference hot-path rewrite (option A of multi-context)

## [0.5.5] - 2026-04-16

### Added
- OpenRouter model metadata fetcher with dynamic max_tokens capping
  - Fetches /api/v1/models on provider init, caches with 1h TTL
  - Caps max_tokens to fit within model context_length (prevents 400 errors)
  - Graceful fallback if metadata fetch fails
- Spawn identity resolution from pool — three modes
  - By identity: `name="lapis"` uses pool's agent_type
  - By agent_type: `name="coder"` picks next available gem from pool
  - Explicit: `name="lapis" type="research"` overrides type
  - Returns resolved identity immediately (no discovery/polling)
  - Returns "already online" if identity is running
- Pool identity schema now supports agent_type and skills fields on all 24 gems

### Fixed
- hub_capture returns entries newest-first (was oldest-first)
- hub_capture truncation raised from 2k to 10k chars (old entries consumed entire budget)
- Slash command /hub spawn now parses identity=X and type=X kwargs
- Removed dead code from openrouter_model_info.py
- Fire-and-forget warm_cache() task now properly handles exceptions

## [0.4.18] - 2026-01-19

### Added
- **Interactive Widget Status System**: Complete rewrite of status bar with interactive, customizable widgets
  - Tab-based navigation mode with widget selection and activation
  - Edit mode for adding/removing/configuring widgets
  - Inline editors (slider, text, dropdown) for real-time value adjustment
  - Script-based widget support for zero-code customization
  - Widget picker modal for easy widget discovery and selection
  - Visual effects (shimmer, pulse, ultra-shimmer) for enhanced UX
  - Widget background color customization (5 color options)
  - 18+ built-in widgets (cwd, profile, model, git, tmux, skills, tasks, etc.)
  - Undo/redo support (Ctrl+Z) for widget layout changes
  - Quick jump to widgets with digit keys (1-9)
  - Comprehensive documentation (user guide, technical reference, developer guide)

- **Parallel Agent Spawning**: Non-blocking agent execution for improved performance
  - Agent spawning now runs in background tasks
  - Reduced spawn time from 50s to 14s for 5 agents (3.6x speedup)
  - Message injection when background agents complete
  - Pipe mode waits for full initialization

- **Widget Documentation**: Three comprehensive documentation files
  - User guide with quick start and examples
  - Technical reference with architecture and API
  - Developer guide with design system integration

### Fixed
- **Widget background color rendering**: Fixed alignment issues where colored widgets showed visible gaps
- **Toggle widget persistence**: Toggle states now properly persist across application restarts
- **Label widget inline editing**: Fixed context attribute and row indexing bugs
- **Widget color toggle**: Fixed row background painting over widget colors
- **Edit mode selection state**: Fixed selection state when exiting edit mode to status focus
- **Script widget registration**: Script widgets now properly discovered and registered in widget picker
- **Agent orchestrator blocking**: Fixed blocking `asyncio.sleep()` calls that froze UI during agent spawning
- **Modal controller state**: Improved state management in modal controller

### Changed
- Replaced fixed 3-area status system with flexible multi-row widget layout (up to 6 rows)
- Widget layout configuration now persisted to config file
- Navigation mode has three modes: INPUT, STATUS_FOCUS, EDIT
- Modal system integrated with widget navigation

### Technical Details
- New files: `kollabor/io/status/widget_registry.py`, `layout_manager.py`, `navigation_manager.py`, etc.
- Script widget manager with metadata parsing and execution engine
- Inline editor service with consistent API across all editor types
- Widget interaction handler for keyboard routing and modal display
- Tmux verification tests for all widget features
- Known issues tracking system with templates

## [0.4.11] - 2025-12-27

### Added
- **Terminal color capability detection**: Automatic detection of terminal color support with intelligent fallbacks
  - Detects TRUE_COLOR (24-bit), EXTENDED (256-color), BASIC (16-color), and NONE modes
  - Checks `COLORTERM`, `TERM_PROGRAM`, and `TERM` environment variables
  - Apple Terminal.app correctly detected as 256-color only
  - `KOLLAB_COLOR_MODE` environment variable for manual override
  - Programmatic control via `set_color_support()` and `reset_color_support()`

### Fixed
- **Color rendering on non-true-color terminals**: Gradients and colors now display correctly on terminals that don't support 24-bit true color
  - `ColorPalette` now uses dynamic color code generation based on terminal capabilities
  - `GradientRenderer` automatically uses 256-color fallback
  - Plugin `ColorEngine` updated to use color support detection
  - Fixes broken gradient display in macOS Terminal.app and other 256-color terminals

### Technical Details
- Refactored `ColorPalette` class to use metaclass for dynamic color generation
- Added `ColorSupport` enum, `get_color_support()`, `rgb_to_256()`, and `color_code()` utilities
- Updated `status_renderer.py` and `message_display_service.py` to use dynamic colors
- Updated `plugins/enhanced_input/color_engine.py` with fallback support

## [0.4.10] - 2025-12-10

### Fixed
- **Slash command menu filtering prioritization**: Command menu now correctly prioritizes commands whose names start with the typed query over alias matches
  - When typing `/t`, `/terminal` now appears first (name match) instead of `/save` (alias "transcript" match)
  - Improved user experience with more intuitive command filtering and selection
  - Filtering now uses three-tier priority: name prefix → alias prefix → substring matches
  - Cursor selection automatically focuses on the most relevant match

### Technical Details
- Enhanced `SlashCommandRegistry.search_commands()` to separate matches into priority tiers
- Name matches have highest priority, followed by alias matches, then substring matches
- Each tier only returns results if no higher priority matches exist

## [0.4.9] - 2025-12-10

### Fixed
- **Tmux plugin keyboard shortcuts**: Fixed Option+Left/Right (Alt+Arrow) key handling in tmux plugin
  - Previously these shortcuts were not properly captured in tmux sessions
  - Now correctly handles Alt+Left/Right for word-level navigation
  - Improved tmux session viewing experience with proper keyboard support

### Added
- Alt+Arrow keyboard shortcuts support in tmux plugin for enhanced navigation

## [0.4.8] - 2025-12-08

### Added
- **Windows compatibility**: Full support for Windows operating systems
  - Platform-specific abstractions for terminal operations
  - Inline platform checks for cross-platform compatibility
  - Windows-specific terminal handling and input processing
  - Tested on Windows, macOS, and Linux

## [0.4.7] - 2025-12-08

### Fixed
- **Save command payload preservation**: `/save` command now preserves exact API payload structure
  - Conversation history saved with original message format
  - Tool calls and function results properly serialized
  - Metadata preserved across save/load cycles

## [0.4.6] - 2025-12-08

### Changed
- Dynamic version display system
- Improved OS classifier metadata in package configuration

## [0.4.5] - 2025-12-08

### Added
- Render cache optimization with smart invalidation
  - Cache automatically invalidates when clearing active display areas
  - Improved terminal rendering performance
  - Reduced screen flicker during updates

### Fixed
- Duplicate plugin instance initialization prevented
- Task concurrency increased for better performance
- Default timeout removed for more flexible operation

## [0.4.0] - 2025-12-08

### Added
- **Dynamic system prompt rendering with `<trender>` tags**
  - System prompts can now include dynamic content that renders at runtime
  - `<trender type="project_tree">` - Include project directory structure
  - `<trender type="file_list" pattern="**/*.py">` - Include filtered file lists
  - `<trender type="file_content" path="README.md">` - Include file contents
  - `<trender type="timestamp">` - Include current timestamp
  - Comprehensive documentation in `docs/features/dynamic-system-prompts.md`
  - Test suite for prompt rendering functionality

- **Environment variable configuration system**
  - Complete configuration via environment variables (see `ENV_VARS.md`)
  - API configuration: `KOLLAB_API_ENDPOINT`, `KOLLAB_API_TOKEN`, `KOLLAB_API_MODEL`, etc.
  - System prompt configuration: `KOLLAB_SYSTEM_PROMPT`, `KOLLAB_SYSTEM_PROMPT_FILE`
  - Environment variables take precedence over config files
  - Support for `.env` file loading

- **Save conversation plugin** (`/save` command)
  - Save conversations to file or clipboard
  - Multiple format support (JSON, markdown, text)
  - Preserves full conversation context and metadata

- **Tmux plugin with live modal viewing**
  - `/terminal` (aliases: `/tmux`, `/term`, `/t`) command for tmux session management
  - Create, view, list, and kill tmux sessions
  - Live modal viewing with real-time session output
  - Interactive session selection and management

### Changed
- Enhanced system prompt with comprehensive project context
- Improved modal UI layout and spacing
- Optimized status view performance
- Startup banner functionality removed for cleaner startup

### Fixed
- System prompt rendering now processes tags before storing in history
- Plugin configuration merging improved
- Modal rendering with better coordinate-based restoration

## [0.3.2] - 2025-01-15

### Fixed
- **System prompt now actually loads from file**: Fixed critical bug where `system_prompt/default.md` was bundled but never loaded into config
- Config generation now includes `kollabor.llm.system_prompt` section with:
  - `base_prompt`: Full content from `~/.kollab/system_prompt/default.md` (15k+ chars)
  - `include_project_structure`: Controls project tree inclusion (default: true)
  - `attachment_files`: List of files to attach to system prompt (default: [])
  - `custom_prompt_files`: Additional custom prompt files (default: [])
- LLM service now uses loaded system prompt instead of hardcoded fallback
- Both global and local configs properly generate with full system prompt content

### Technical Details
- Added `_load_system_prompt()` method to `ConfigLoader`
- System prompt resolution: local `.kollab/system_prompt/default.md` overrides global `~/.kollab/system_prompt/default.md`
- Fallback to "You are Kollab, an intelligent coding assistant." only if file read fails

## [0.3.1] - 2025-01-15

### Changed
- Updated repository URLs to point to https://github.com/kollaborai/kollab
- Updated all package metadata and documentation links

## [0.3.0] - 2025-01-15

### Changed
- Standardized the pre-release configuration directory on `.kollab`
  - Global default: `~/.kollab/`
  - Local override: `./.kollab/`
  - No public migration is required for this pre-release cleanup

### Added
- System prompt customization support
  - Bundled system prompt content included in package
  - Global and local prompt resolution supported
  - Local system prompt overrides global (follows same resolution as config.json)
  - Users can edit system prompts to customize LLM behavior
- New utility functions in `kollabor/utils/config_utils.py`:
  - `get_system_prompt_path()`: Get active system prompt path
  - `initialize_system_prompt()`: Seed default prompt assets into config directories

### Fixed
- Consistent use of configuration directory utility functions across all components
- Application initialization now uses `ensure_config_directory()` instead of hardcoded paths

## [0.2.1] - 2025-01-15

### Changed
- Enhanced input plugin default colors now use darker gradient theme
  - Border: dim (was default)
  - Text: dim (was default)
  - Gradient mode: enabled by default (was disabled)
  - Gradient colors: darker theme ["#333333", "#999999", "#222222"] (was blue theme)
  - Text gradient: enabled by default (was disabled)
  - Provides better visual consistency and reduced visual noise

## [0.2.0] - 2025-01-15

### Changed
- Configuration directory now defaults to global `~/.kollab/`
  - Global default: `~/.kollab/` (configuration shared across all directories)
  - Local override: Create `./.kollab/` in project directory for project-specific config
  - All data (config, conversations, logs, state) now stored globally by default
  - This matches standard CLI tool behavior (like git, docker, etc.)

### Added
- Configuration directory utility functions in `kollabor/utils/config_utils.py`

### Fixed
- Consistent configuration directory resolution across all components
- LLM service, conversation manager, and logging now use unified config directory logic

## [0.1.3] - 2025-01-15

### Added
- Version flag support: `kollab -v` or `kollab --version` now displays version number

## [0.1.2] - 2025-01-15

### Fixed
- **Critical bug**: Fixed fullscreen command discovery (missed in 0.1.1)
  - `/matrix` and other fullscreen commands now work when installed via pip
  - Fullscreen plugins now use same directory resolution as regular plugins
  - Fixes second instance of plugin directory bug

## [0.1.1] - 2025-01-15

### Fixed
- **Critical bug**: Plugin discovery now works correctly when installed via pip
  - Previously plugins were only discovered in current working directory
  - Now searches package installation directory first, falls back to cwd for development
  - Fixes missing plugin features (enhanced input styling, /matrix command, etc.)
  - Users who installed v0.1.0 should upgrade to get full plugin functionality

## [0.1.0] - 2025-01-15

### Added
- Initial PyPI release
- Core LLM chat functionality with streaming support
- Event-driven plugin system
- Terminal UI with status indicators
- Enhanced input box with gradient borders
- Hook system for extensibility
- Configuration management with hot reload
- Conversation logging and persistence
- MCP (Model Context Protocol) integration
- File operations with automatic backups
- Command system with modal UI
- Visual effects (matrix rain, shimmer animations)

### Security
- Fixed command injection vulnerability in MCP integration
- Added input validation for tool names
- Changed subprocess execution from shell=True to shell=False
- Updated aiohttp dependency to >=3.10.11 (patches CVE vulnerabilities)

[0.1.1]: https://github.com/kollaborai/kollab/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/kollaborai/kollab/releases/tag/v0.1.0


## [0.5.0] - 2026-01-16

### Added
- **Modern Design System (V2 UI) - Complete UI Overhaul**
  - Comprehensive redesign using unified design system (TagBox, Box, T, S, C)
  - Themed rendering with gradient support (lime, ocean, sunset, mono themes)
  - Consistent 76-character width across all UI elements
  - Segmented colored sections for status bars and containers
  - Proper visual hierarchy with modern styling

- **Modern Message Renderer**
  - New `ModernMessageRenderer` class with TagBox-based message display
  - `user_message()` - themed user messages with arrow icon (❯)
  - `assistant_message()` - single-line AI responses with diamond icon (◆)
  - `response_block()` - multi-line AI responses with continuous styling

- **Text Wrapping Utility**
  - `wrap_text()` function in `kollabor.ui.design_system.components`
  - Preserves ANSI color codes during text wrapping
  - Word-boundary and character-boundary wrapping modes
  - Helper functions: `_visible_len()`, `_wrap_words()`, `_wrap_chars()`

- **Modern Status Rendering**
  - `render_horizontal_layout_v2()` method with segmented design
  - Hint bars with solid backgrounds and proper color theming
  - Cycling hint for multi-view status (Opt/Alt+Left/Right)
  - Provider styles using design system colors

- **Modern Thinking Animation**
  - `get_display_lines_modern()` with TagBox styling
  - Timer display with token count support
  - Consistent with other UI elements

### Changed
- **Command Menu Renderer**
  - Migrated from legacy Agnoster/Gradient to TagBox design system
  - Refactored `_make_empty_state()`, `_make_scroll_indicator()`, `_make_footer()`
  - Removed `apply_bg_gradient()`, `make_bg_color()`, `make_fg_color()`
  - Updated imports to use design system components

- **Core Status Views**
  - Updated `PROVIDER_STYLES` to use design system color tuples
  - `_format_agent_skills_line()` now uses segmented status_v2 style
  - Changed from single-line to multi-line agent/skills display
  - Application now calls `register_all_views_v2()` instead of `register_all_views()`

- **Terminal Renderer**
  - Added `use_modern_ui` flag for new vs legacy rendering
  - `_render_input_modern()` method for TagBox input rendering
  - Shell command display handling (! prefix stripped from display)
  - Thinking animation switched to modern renderer

- **Message Coordinator**
  - Updated to use `ModernMessageRenderer` for message display
  - Improved cursor handling after atomic message display

- **All UI Widgets**
  - Migrated from `ColorPalette` to design system (T, S, TagBox)
  - Updated: `CheckboxWidget`, `DropdownWidget`, `LabelWidget`, `SliderWidget`, `TextInputWidget`
  - Removed `ColorPalette` imports from all widgets

- **Modal Renderers**
  - `LiveModalRenderer` and `ModalRenderer` now use `Box` component
  - Modernized title and footer rendering with half-block style
  - Removed legacy border character rendering

- **Banner Renderer**
  - Removed legacy `KOLLAB_ASCII` constant
  - `create_kollabor_banner()` now uses TagBox with primary gradient
  - Matches `banner_v2` design from preview-modern-ui.py

- **Enhanced Input Plugin**
  - `BoxRenderer` completely refactored to use TagBox
  - Removed legacy top/bottom border and content line rendering
  - `GeometryCalculator` box width now capped at 76 chars for consistency
  - Diamond icon (◆) in tag for first line

### Technical Details
- Design system exports: `wrap_text` added to `kollabor.ui.design_system.__all__`
- Theme colors accessed via `T()` - e.g., `T().ai_tag`, `T().user_tag`, `T().primary`
- Style codes via `S` - e.g., `S.BOLD`, `S.DIM`, `S.RESET`
- Gradient functions: `gradient()`, `gradient_fg()`, `solid()`, `solid_fg()`
- TagBox pattern for consistent tag + content layout across all components
