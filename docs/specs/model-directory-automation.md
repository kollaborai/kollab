# Model directory automation and provider-prefixed lookup

Issue: #1107  
Status: Implemented  
Owner: Kollab maintainers

## Problem

The bundled model directory can drift from the published model catalogs, and
manually curated entries make full replacement unsafe. Provider-prefixed model
IDs can also fail capability lookup even when the underlying catalog entry is
known. This leaves release preparation dependent on manual catalog updates and
can make supported model capabilities unavailable to callers.

## Goal and non-goals

Goal: refresh `bundles/data/models.json` from models.dev and OpenRouter during
release preparation while preserving curated data, and resolve known
provider-prefixed IDs against their catalog entry.

Non-goals: update the directory through a self-pushing scheduled workflow,
change provider compatibility policy, or infer capabilities for unknown model
IDs.

## What people see

There is no new interactive surface. Maintainers run the model-directory
generator as part of release preparation. The generator reports additions or
field changes; `--check` reports drift with a nonzero exit, and `--dry-run`
does not write the registry.

## Contract

- `scripts/update_model_directory.py` reads models.dev and OpenRouter JSON and
  merges sourced fields into `bundles/data/models.json`.
- Existing catalog entries keep their curated fields; sourced values update
  only fields supplied by the sources. New entries are marked
  `Auto-generated` for review.
- Unsupported models.dev providers, deprecated models.dev entries, malformed
  OpenRouter rows without IDs, and entries without a positive context window
  are skipped. The latter are not chat models representable by this schema.
- `--source-models-dev` and `--source-openrouter` accept local JSON payloads;
  `--registry` selects a registry file. Without source payload paths, the
  generator fetches the published catalogs.
- `--check` reports whether the registry differs and exits 1 on drift;
  `--dry-run` plans changes without writing.
- `scripts/validate_models.py` validates the resulting directory. Release
  preparation runs the generator and validator and includes the JSON in the
  release PR; no workflow pushes generated changes on its own.
- Model capability lookup may resolve a known provider-prefixed ID to its
  catalog entry. An exact catalog entry takes precedence over prefix
  inheritance, and provider scoping remains in force.

## Scenarios

- If both source catalogs contain a chat model absent from the registry, then
  the generator adds it and marks it as auto-generated.
- If a source provides values for a model already in the registry, then only
  sourced fields are refreshed and curated fields remain intact.
- If a source row is unsupported, deprecated, lacks an ID, or has no positive
  context window, then it is excluded from the generated chat-model directory.
- If the registry already matches both sources, then the generator reports no
  changes and leaves the registry current.
- If a caller looks up a known vendor-prefixed model ID, then the model's
  catalog capabilities resolve while provider boundaries are preserved.
- If an exact model entry exists with an explicit capability value, then it
  shadows inherited prefix data.
- If a model ID is unknown or its vendor/provider pairing is unsupported, then
  lookup does not infer capabilities from an unrelated catalog entry.

## Failure modes

- A source endpoint is unavailable or returns invalid JSON: generation fails
  before a refreshed directory is produced; release preparation cannot claim
  the directory is refreshed.
- A local source-payload path is invalid or contains invalid JSON: generation
  fails instead of silently substituting a different source.
- A source model has no positive context window: it is skipped because it does
  not fit the chat-model schema.
- A models.dev provider is not in the supported provider map: its entries are
  skipped.
- An OpenRouter row is not an object or lacks an ID: it is skipped.
- The registry has drift under `--check`: the command exits 1 and reports the
  planned changes so the release owner can review them.
- A provider-prefixed lookup is unknown or mismatched: capability lookup does
  not borrow the matching model name from an unrelated provider.

## Decisions

- Keep regeneration in release preparation, decided by the maintainers, so
  generated changes are reviewed and included with a release rather than
  pushed independently by automation.
- Use the published catalogs as sources while retaining curated fields,
  decided by the maintainers, to reduce manual drift without surrendering
  catalog-specific judgment.
- Require a positive context window for imported entries, decided by the
  maintainers, because the bundled schema represents chat models.
- Preserve exact-entry precedence and provider scoping in capability lookup,
  decided by the maintainers, so prefix fallback does not override explicit
  declarations or cross provider boundaries.

## Test plan

- `tests/unit/test_update_model_directory.py` covers source mapping, curated
  merge behavior, filtering, dry-run/check behavior, and idempotence.
- `tests/unit/test_models_registry.py` covers vendor-prefixed resolution and
  exact-entry precedence.
- Release preparation runs
  `python scripts/update_model_directory.py && python scripts/validate_models.py`
  before changelog generation.
- Sweep source filtering, existing-entry merge behavior, prefixed lookup,
  suffix handling, and provider boundaries.

## Docs and changelog fragment

- `docs/release-process.md` documents release-time refresh and validation.
- `changes/model-directory-automation.added.md` records the user-facing
  capability.

## Open questions

None.
