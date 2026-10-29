# Prompt Libraries and Roles

The web UI's **Settings → Prompts** tab builds each role's system prompt from shared sections.

## Libraries

- **Defaults** is the shipped `bundles/agents/_base/sections` set. An edit here reaches every role that sends the section. **Revert to Shipped** drops the edit.
- **Your libraries** (for example `local-lite`) hold sections you write. A role sends one only when you tick it.
- **Used by** counts the roles whose All Models variant sends the section.

## Roles

A role is an agent bundle (`default`, `coder`, `koordinator`...). Its list follows render order: the role's own sections, then library sections, then the per-turn **Session Context**, which every role sends ahead of the newest message and which cannot be unticked.

- Tick or untick a section to change what the role sends. It saves at once.
- Editing a Defaults section from a role saves a copy for that role only. **Reset to Library** goes back to the shared text.
- **Variants** tailor a role per model. All Models is the base. **+ Variant** copies it and names the providers that switch to it (for example `ollama` for Local Models). The active profile's provider picks the variant at render time.
- **Full Prompt** previews the assembled prompt for the open variant.

## Files

| What | Where |
|------|-------|
| Defaults edits | `~/.kollab/prompt-libraries/defaults/<key>.md` |
| Your library sections | `~/.kollab/prompt-libraries/<library>/<key>.md` |
| A role's picks | `~/.kollab/prompt-roles/<role>.json` |
| A role's own copy | `~/.kollab/prompt-overrides/<role>/<key>.md` |

A missing or unreadable picks file means the role sends everything, as shipped.

```json
{"variants": {
  "all":   {"exclude": ["notifications"], "add": []},
  "local": {"providers": ["ollama"], "exclude": ["tool-workflow"], "add": ["local-lite/tool-workflow-lite"]}
}}
```

Engine routes: `GET/POST /prompts/libraries`, `GET/PUT/DELETE /prompts/libraries/{library}/sections/{key}`, `GET/PUT /prompts/{role}/composition`, `GET /prompts/{role}/preview?variant=`. Code: `packages/kollabor-ai/src/kollabor_ai/prompt_composition.py`.
