# site-deploy report (2026-09-30)
Shipped only the Agent Network docs page to https://kollabor.ai (alzan-prod, /home/almazan/kollabor.ai, service kollabor-web). No git push, edge not touched.
## Compared first
- Copied the live tree to the Mac, built it as is with npm (its Dockerfile uses npm + package-lock.json), served it locally.
- Visible text of /, /docs, /docs/quickstart, /docs/commands, /pricing: 0 differing lines vs live. Only HTML delta: runtime siteUrl (compose env). A rebuild carried no unshipped server edits.
## Files changed (3, copied into /home/almazan/kollabor.ai)
- pages/docs/network.vue (new): 96a5b5c content, same 12 sections, placeholders only (XXXX-XXXX, kollabor.ai/c/0123456789abcdef). Dropped links to /docs/hub and /docs/discovery (404 live); Next Steps points at installation, configuration, commands; title "... - Kollabor Documentation" like siblings.
- layouts/docs.vue: "Agent Network" entry in the CLI nav group.
- pages/docs/index.vue: Agent Network card, third card in the product grid, same markup as the CLI card.
- .env, docker-compose.yml, Dockerfile: sha256 identical before and after.
## Checks
- Local build: /docs/network 200, h1 + 12 h2; 390 and 820 px on /docs/network and /docs: no horizontal overflow, 0 unclipped offenders (Browser pane, JS measure; screenshots only showed the sidebar, body not eyeballed).
- Live after deploy: /docs/network 200, same 12 headings, text identical to the local build. / and /pricing unchanged; /docs, /docs/quickstart, /docs/commands differ only by the new nav entry (plus the card on /docs). All 20 existing docs routes (19 pages + /docs) 200; /docs/hub still 404 (not shipped).
- Container kollaborai-kollabor-web-1 recreated 2026-09-30 20:29 MST, up, 3002->3000, image 6890eca0deb6 -> 0151aef120ab, log "Listening on http://0.0.0.0:3000".
## Flags
- PyPI latest kollab is 0.10.7; the page says 0.11.0 is required, so the docs are live ahead of the release.
- Pre-existing: text-cli-primary has no CSS rule on the live site (16 sibling pages use it), so inline code shows in the inherited color. Live /docs/commands still lacks /connect and lists /profile.
## Rollback
- Backup: /home/almazan/kollabor.ai.bak-2026-09-30 (full copy). Old image also tagged kollaborai-kollabor-web:pre-network-docs-2026-09-30.
- Fast: ssh alzan-prod 'cd /home/almazan/kollabor.ai && docker tag kollaborai-kollabor-web:pre-network-docs-2026-09-30 kollaborai-kollabor-web:latest && docker compose up -d --force-recreate --no-build kollabor-web'
- Full: restore the 3 files from the .bak dir (delete pages/docs/network.vue), then docker compose up -d --build kollabor-web.
