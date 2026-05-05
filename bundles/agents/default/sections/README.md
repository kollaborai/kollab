# Default Agent Sections

This agent uses shared base sections from `../_base/sections/` and overrides only agent-specific content.

## Local Overrides

| File | Description |
|------|-------------|
| 00-header.md | Agent identity and philosophy |

## Inherited from _base

All other sections (tools, protocols, practices, meta) are inherited from `bundles/agents/_base/sections/`.

See `_base/sections/README.md` for the full shared section inventory.

## Adding Agent-Specific Sections

1. Create a new file in this `sections/` directory
2. Add a trender include to `system_prompt.md`:
   ```markdown
   <trender type="include" path="sections/my-section.md" />
   ```

## Overriding a Base Section

To override a base section for this agent only:
1. Create a local file with the same content you want
2. Change the include path in `system_prompt.md` from `../_base/sections/...` to `sections/...`
