# site-docs report
Branch docs/agent-network-0.11, commit 96a5b5c, worktree /Users/malmazan/dev/kollabor.ai-network-docs. Not pushed, not deployed. Main checkout untouched.

## Pages changed
- NEW pages/docs/network.vue (/docs/network): terms, guided setup, join by code, what joining copies, messaging (hub_msg, kollab --hub msg, cron), trust, knocks, self-hosting, command reference, troubleshooting.
- Links and short sections: layouts/docs.vue (nav), docs/index.vue (card), hub.vue + agent-mesh.vue (Across Computers), discovery.vue (paragraph), commands.vue (/connect card + Network Commands), quickstart.vue (step 10).
- The existing pages had no old network commands or flows (no enroll/offer/K1 codes), so nothing needed removing. Placeholders only: XXXX-XXXX, kollabor.ai/c/0123456789abcdef.
- Left out on purpose: direct links/forwarding (constitution s10: live proof not run yet).

## Build
- HEAD does not build, before my change (Tailwind v4 commit bbec025): scoped @apply in pages/terms.vue, pages/privacy.vue, pages/docs/index.vue uses tokens the theme lacks ("unknown utility text-foreground / text-primary"). terms/privacy have in-flight edits in the main checkout, so I left them.
- Verified with those 3 style blocks stripped in the worktree (restored, not committed): bun install (--frozen-lockfile fails, bun.lock is stale), npm run build OK.

## Page checks (built .output served on :3099)
- curl 200: /docs/network, hub, agent-mesh, discovery, commands, quickstart, /docs. All 12 network headings present; new links/headings present on the other six.
- 390 and 820 px, iframe measure on all 7: no page overflow, 0 unclipped offenders. Screenshots only covered the sidebar (Agent Network listed); I did not eyeball the page body.

## Deploy today (read-only checks)
- kollabor.ai = edge nginx 50.116.8.243 (ssh -p 2222 deploy@) in front of alzan-prod (ssh arch-server): /home/almazan/kollabor.ai, docker compose service kollabor-web, container kollaborai-kollabor-web-1, 3002->3000, created 2026-04-18. Edge vhost not readable as deploy; edge->10.0.0.5:3002 is inferred.
- That checkout is at 117e060, has local edits, NO git remote, its own .env, compose port 3002 (repo HEAD says 3000, main checkout WIP says 4322). Never overwrite compose or .env.
- Live is far behind HEAD: /docs/hub, agent-mesh, discovery, coordinator, context are 404 today. Deploying ships all of HEAD since 117e060 (Tailwind v4, rebrand, 5 docs pages), not only network docs.
- deploy/DEPLOYMENT.md (PM2, /var/www/kollabor.ai) and .github/workflows/deploy.yml (push to main) describe a setup that is not running (no such dir or pm2 on the edge). Treat as dead.
- Steps, after the 3 build blockers are fixed and the branch is merged:
  1. Clean checkout of the merged branch on the Mac; npm run build must pass.
  2. ssh arch-server 'cp -a /home/almazan/kollabor.ai /home/almazan/kollabor.ai.bak-$(date +%F)'
  3. rsync -a --exclude .git --exclude .env --exclude docker-compose.yml --exclude node_modules --exclude .nuxt --exclude .output ./ arch-server:/home/almazan/kollabor.ai/
  4. ssh arch-server 'cd /home/almazan/kollabor.ai && docker compose up -d --build kollabor-web'
  5. Verify: curl -s -o /dev/null -w '%{http_code}' https://kollabor.ai/docs/network (404 today, expect 200), same for /docs/hub; docker ps shows a fresh kollaborai-kollabor-web-1.
  6. Rollback: restore the .bak dir over it and rerun step 4. Edge nginx and the relay container (the directory) are not touched.

## Left
- Fix the 3 build blockers (docs/index.vue: @reference the project main.css and use real tokens); then merge, then deploy per above. Push/PR not done.
- Visual pass on the whole site first: theme has no --color-primary, so text-primary / bg-primary/10 in docs templates render unstyled (pre-existing).
- commands.vue still lists /profile (gone in kollab; /llm replaces it).
- Remove the worktree after merge: git worktree remove ~/dev/kollabor.ai-network-docs.
