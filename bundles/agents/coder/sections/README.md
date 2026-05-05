# Modular System Prompt Structure

This directory contains the modular sections for the coder agent's system prompt.

## File Structure

```
sections/
├── 00-header.md                          # Agent identity and philosophy
├── 01-session-context.md                 # Environment detection (git, docker, python, etc.)
├── 02-tool-workflow.md                   # Tool-first workflow methodology
├── 03-response-patterns.md               # Response type classification
├── 04-question-gate.md                   # Question gate protocol
├── 05-investigation-examples.md          # Example investigations
├── 06-task-planning.md                   # Todo list system
├── 07-tool-reference.md                  # Command and file operation reference
├── 08-communication-protocol.md          # Response templates and structure
├── 09-quality-assurance.md               # QA checklists
├── 10-thoroughness-mandate.md            # Completeness requirements
├── 11-tool-execution-protocol.md         # Tool usage workflow
├── 12-file-ops-reference.md              # File operation best practices
├── 13-resource-limits.md                 # Tool call limits and constraints
├── 14-error-handling.md                  # Error recovery strategies
├── 15-git-workflow.md                    # Git best practices
├── 16-testing-strategy.md                # Testing methodology
├── 17-debugging.md                       # Debugging techniques
├── 18-dependency-management.md           # Dependency handling
├── 19-security.md                        # Security considerations
├── 20-performance.md                     # Performance optimization
├── 21-communication-best-practices.md    # Communication style
├── 22-advanced-troubleshooting.md        # Advanced debugging
├── 23-final-reminders.md                 # Summary and principles
└── 99-terminal-formatting.md             # Terminal output formatting rules
```

## Usage

These sections are included by the main `system_prompt.md`:

```markdown
<trender type="include" path="sections/00-header.md" />
<trender type="include" path="sections/01-session-context.md" />
<!-- ... etc ... -->
```

## Modifying Sections

To edit a section:
1. Open the section file (e.g., `sections/02-tool-workflow.md`)
2. Make your changes
3. Save the file
4. The agent will automatically use the updated content on next load

No need to edit the main `system_prompt.md` - it just includes these files.

## Adding New Sections

1. Create a new file in `sections/` with appropriate numbering:
   ```
   sections/24-new-section.md
   ```

2. Add content to the file

3. Add include to `system_prompt.md`:
   ```markdown
   <trender type="include" path="sections/24-new-section.md" />
   ```

## Naming Convention

Use two-digit prefixes to control loading order:
- `00-*` - Always first (header, identity)
- `01-09*` - Core content
- `10-19*` - Secondary content
- `20-29*` - Advanced topics
- `99-*` - Always last (formatting rules, summaries)

## Reusing Sections

Sections can be shared between agents:

```markdown
<!-- In another agent's system_prompt.md -->
<trender type="include" path="../default/sections/15-git-workflow.md" />
```
