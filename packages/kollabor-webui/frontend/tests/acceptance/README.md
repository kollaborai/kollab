# System Prompts browser acceptance (mock API)

This focused Playwright check runs the real `PromptsPage` in Chromium with
`fixtureTransport`. It never starts or contacts an engine and never writes to
a user's prompt override store. Engine persistence is covered separately by
`tests/unit/test_engine_prompts_routes.py` and `tests/unit/test_prompt_overrides.py`.

From `packages/kollabor-webui/frontend`, install Playwright without changing
the package manifest or lockfile, then install its Chromium browser:

    npm install --no-save --package-lock=false playwright
    npx playwright install chromium
    node tests/acceptance/system-prompts.browser.mjs

The runner starts and stops its own Vite server on `127.0.0.1:4174`. It checks
responsive layout at 390, 820, and 1280 pixels, stale section-request ordering,
section/preview errors, save/remove refresh failures, read-only override removal,
and visible mutation error states. Screenshots are saved
under the OS temp directory at `system-prompts-browser`; set
`PROMPTS_ARTIFACT_DIR` to choose another output directory.
