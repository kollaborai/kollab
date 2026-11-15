- The bundled model directory (`bundles/data/models.json`) now regenerates
  itself from external catalogs (models.dev and the OpenRouter public API):
  release prep runs `scripts/update_model_directory.py` and a weekly
  automation opens a draft refresh PR, so newly released models ship with
  current context windows, pricing, and capability flags.
- Model lookups resolve OpenRouter vendor-prefixed names (for example
  `openai/gpt-6-luna`) to their underlying directory entries, so capability
  flags like image support now apply to vendored model strings instead of
  silently treating them as unknown models.
