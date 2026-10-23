---
title: "Manual WireGuard phone access: removal review"
created: 2026-10-21
status: historical snapshot; removal landed in release/0.18.0 (fb952f10)
---

# Manual WireGuard phone access: removal review

This is a historical inventory and proposed change list for the audited checkout,
not an implementation. Since this review, manual WireGuard phone access has been
removed from the 0.18.0 release line by `fb952f10`. No feature code, screenshots,
saved phone state, VPN configuration or running processes were removed or changed
by this audit; only this review document was added to the audited checkout.

Audited checkout: `main` at `13e939037afb2a7efed2953480b3f4820c58bee3`.
Three Luna agents independently covered UI/docs, backend/security, and history/
host state. Their findings were reconciled against current source and read-only
host checks. At that time, implementation and every proposed deletion were
pending Marco's review and explicit approval.

## What was added, and where

The manual phone feature was introduced on October 20 in
`87ba5c8d7503fe1603bed0b58d693afa998b224e`, titled
`feat(phone-access): add private WireGuard phone provisioning`, and reached this
history through `bfb8629b1` with additional boundary hardening.

It added application code that could generate a WireGuard client configuration,
store phone public peer records, and ask a host-admin helper to apply those peers.
It did not install a WireGuard server or configure the router, DNS, firewall or
the operator's base VPN configuration. The helper and server were prerequisites.

At the audited checkout, the manual path was:

- `PhoneLinkPanel.tsx` imports `PhoneAccessSection.tsx` and mounts it under
  **Advanced Settings** (lines 369–384).
- `PhoneAccessSection.tsx` calls the manual methods in `frontend/src/api.ts`:
  `phoneState`, `savePhoneSettings`, `addPhone`, and `revokePhone`.
- The engine registers `routes/phone.py` from `server.py` (lines 214 and 222).
  It serves `GET /phone`, `PUT /phone/settings`, `POST /phone/peers`, and
  `DELETE /phone/peers/{peer_id}` through the authenticated local engine.
- `phone_access.py` generates client keys and QR configurations, saves settings
  and public peer snippets, and invokes `KOLLAB_PHONE_ACCESS_APPLY_COMMAND`.
  The generated file is also supplied through `KOLLAB_PHONE_ACCESS_PEERS_FILE`.
- The WebUI's `server.py:136` reads the manual manager's state to add individual
  phone addresses to its sensitive-request allowlist. The shared
  `network_security.py:290` contains that phone-specific helper.

PhoneLink is a separate path: its browser portal and encrypted relay connect to
`phone_link.py` and `/phonelink` routes. It does not require the manual manager,
the `/phone` endpoints, a WireGuard app, or a user-managed VPN server.

## What is configured on this Mac

Read-only checks on October 21 established:

- `~/.kollab/phone-access/`, `state.json` and `peers.conf` are absent. The path
  comes from `phone_access.py:72` and `kollabor_config/config_utils.py:24`.
- Neither the running WebUI nor its engine has
  `KOLLAB_PHONE_ACCESS_APPLY_COMMAND` configured. No helper was invoked.
- The separate PhoneLink state file, `~/.kollab/phonelink/phones.json`, exists.
  Its contents were not exposed or changed.
- WireGuard executables and one WireGuard interface are present independently.
  `~/.config/wireguard/wg0.conf` exists. Its contents were not read. The audit
  does not establish the tunnel's server/client role, peer count or provenance.

Therefore the manual Kollab integration is unconfigured here, while an existing
OS-level VPN must be preserved. No VPN uninstall, interface shutdown, peer
revocation, configuration deletion or router change belongs in this proposal.
Other machines' manual peer state and helper configuration were not inspected.

## Proposed core file changes: 24 paths

These changes remove the manual phone feature while retaining PhoneLink.

### Delete four dedicated source/test files, after approval

- `packages/kollabor-webui/frontend/src/components/panels/PhoneAccessSection.tsx`
  — remove the endpoint/key/subnet/routes/DNS form, manual phone peer controls
  and WireGuard configuration QR display.
- `packages/kollabor-engine/src/kollabor_engine/phone_access.py`
  — remove manual settings, key generation, address allocation, peer persistence,
  apply-helper execution and the manual manager singleton.
- `packages/kollabor-engine/src/kollabor_engine/routes/phone.py`
  — remove the four manual `/phone` endpoints, not `/phonelink`.
- `packages/kollabor-engine/tests/test_phone_access.py`
  — remove tests dedicated to the retired manager and its endpoints.

### Edit six application files

- `packages/kollabor-webui/frontend/src/components/panels/PhoneLinkPanel.tsx`
  — remove the manual component import, the WireGuard **Advanced Settings** block
  and unused chevron icon; update the phone-side message that promises advanced
  connection settings. Retain QR pairing, approval, reconnect, revoke and links.
- `packages/kollabor-webui/frontend/src/api.ts`
  — remove `PhonePeer`, `PhoneSettings`, `PhoneState` and the four manual API
  methods. Retain all `PhoneLink*` types and `/phonelink` methods.
- `packages/kollabor-webui/frontend/src/learn/catalog.ts`
  — remove the note promising optional WireGuard controls. Keep the PhoneLink
  topic and its existing `phone-access` route ID; current callers use it.
- `packages/kollabor-engine/src/kollabor_engine/server.py`
  — remove only the manual router import and registration. Keep PhoneLink's
  separate router, owner lifecycle and engine authentication.
- `packages/kollabor-webui/src/kollabor_webui/server.py`
  — remove the manual manager lookup and peer-address merge from
  `current_client_networks`. Use the configured interface CIDRs for the private
  WebUI boundary. Preserve token bootstrap, Host/Origin validation, no-store
  responses, forwarded-header rejection and authenticated proxy behavior.
- `packages/kollabor-webui/src/kollabor_webui/network_security.py`
  — remove only `networks_for_phone_peers` and its obsolete description. Keep
  literal private-bind validation, interface discovery and other shared guards.

### Edit two existing test files

- `tests/unit/test_webui_remote.py`
  — remove manual peer assertions from
  `test_interface_cidr_and_active_phone_peer_addresses_are_exact`, retaining its
  interface-CIDR assertions; remove the manual provisioning/revocation bootstrap
  test. Preserve private-bind, unauthorized-subnet, forged Host/Origin,
  forwarded-header, token, no-store, proxy and redaction coverage. Add focused
  coverage that a former peer address gets no special exemption.
- `packages/kollabor-webui/frontend/tests/learn-catalog.test.ts`
  — check that current PhoneLink guidance does not promise manual WireGuard
  controls; retain topic ID, link and browser-portal route assertions.

### Edit eleven documentation/changelog files

- `README.md` — replace **Private Phone Access** and its server/helper setup with
  the PhoneLink QR/approval flow; correct the Learn reference to WireGuard access
  and the adjacent Settings tab count/list, which currently omits PhoneLink.
- `packages/kollabor-webui/README.md` — remove the optional manual flow under
  **Advanced Settings** and change the following "both contracts" sentence to
  refer to PhoneLink's deployment/physical-phone gates alone. Keep PhoneLink and
  direct private WebUI binding guidance.
- `docs/README.md` — describe the PhoneLink document as QR/relay pairing only.
- `docs/features/phone-access.md` — keep the canonical PhoneLink document and
  filename. Remove the advanced-controls contract and the manual WireGuard guide
  starting at **Advanced: manual WireGuard access**. Keep pairing, identity,
  replay protection, ownership, reconnect, revoke and QR rendering requirements.
- `docs/features/learn.md` — remove the optional manual setup/prerequisites claim.
- `docs/features/web-settings-panels.md` — update the PhoneLink row to omit manual
  **Advanced Settings** while retaining phone identity and credential boundaries.
- `docs/faq.md` — replace manual setup references with the PhoneLink browser flow.
- `docs/reference/env-vars.md` — remove the apply-helper environment contract;
  remove manual-peer exceptions from WebUI descriptions. Retain
  `KOLLAB_WEBUI_HOSTS`, port/engine settings and general private-network security.
- `docs/specs/agent-network-simple-flow.md` — remove the optional manual branch
  from section 16. Keep its PhoneLink contract and unrelated infrastructure
  examples that happen to mention WireGuard.
- `CHANGELOG.md` — record the removal in Unreleased and describe the retained QR
  fix accurately. Preserve versioned change history; this audit makes no claim
  about publication of a listed version.
- `kollabor/updates/CHANGELOG.md` — mirror the root Unreleased wording.

### Refresh one current screenshot

- `docs/screenshots/learn-page-1280x900-phone-access.png`
  — refresh the retained PhoneLink Learn topic after its copy changes. Keep its
  current filename and the acceptance script's topic route.

## Optional cleanup: six additional paths for separate review

These are excluded from the core removal above unless explicitly approved.

Four old simulated manual setup images could be deleted after checking their
references and confirming whether they should remain as historical evidence:

- `docs/screenshots/phone-access/phone-access-1280-simulated.png`
- `docs/screenshots/phone-access/phone-access-390-simulated.png`
- `docs/screenshots/phone-access/phone-access-820-simulated.png`
- `docs/screenshots/phone-access/phone-access-loading-390-simulated.png`

Two dependency files could be edited to remove the engine's direct
`cryptography>=43` dependency edge:

- `packages/kollabor-engine/pyproject.toml`
- `uv.lock`

Only the manual manager imports cryptography directly in the engine package.
However, `packages/kollabor-ai/src/kollabor_ai/providers/security.py:29` uses it
for encrypted key storage, and `:228` rejects that capability without the library.
The root Kollab package
also declares it independently. The main install retains it, but standalone
engine packaging/security was not executed in this audit. Dependency pruning is
not necessary to remove the manual UI/API; keep the current edge unless that
separate packaging boundary is verified. Never delete the shared cryptography
package or the root dependency as part of this cleanup.

## Preserve these shared pieces

Do not revert the original provisioning commit wholesale. It also added useful
shared pieces, and some of its files now serve PhoneLink or general WebUI safety.

- Keep `packages/kollabor-engine/src/kollabor_engine/phone_link.py`,
  `packages/kollabor-engine/src/kollabor_engine/routes/phonelink.py`,
  `plugins/hub/phone_portal.py`, the relay transport and their tests.
- Keep `packages/kollabor-webui/frontend/src/phonelink/`,
  `packages/kollabor-webui/frontend/src/utils/qr-svg.ts` and its tests. PhoneLink
  still imports `segno`; its QR scaling fix must remain.
- Keep shared PyNaCl and `segno` dependencies. Keep WebUI `psutil` for interface
  checks. Keep `packages/kollabor-webui/pyproject.toml` unchanged: the WebUI still
  imports the engine's token reader independently of manual phone access.
- Keep `packages/kollabor-webui/frontend/index.html` and all current public mobile
  assets: `public/manifest.webmanifest`, `public/apple-touch-icon.png`,
  `public/icon-192.png`, `public/icon-512.png`, and
  `public/icon-maskable-512.png` under that frontend directory. The current HTML
  and manifest reference them; they are not dedicated WireGuard assets.
- Keep `packages/kollabor-webui/frontend/src/App.tsx` and
  `packages/kollabor-webui/frontend/src/components/panels/SessionSettings.tsx`;
  the live manual component is mounted from PhoneLinkPanel instead.
- Keep `packages/kollabor-engine/src/kollabor_engine/__main__.py` and
  `packages/kollabor-webui/src/kollabor_webui/__init__.py` private-bind and
  proxy-header protections. Keep `tests/unit/test_webui_auth_wiring.py`,
  `tests/unit/test_webui_static_bundle.py`, and
  `tests/unit/test_webui_update_check.py`.
- Keep `packages/kollabor-webui/frontend/tests/learn-browser-acceptance.mjs` and
  its `phone-access` topic checks. Changing the topic copy does not require
  deleting or renaming its routing tests.
- Keep operator infrastructure references and generated network diagrams in
  `docs/architecture/reference/agent-dns-reference.md`,
  `docs/guides/hub-quick-start.md`, `docs/reference/commands.md`,
  `scripts/build_network_diagrams.py`, `docs/diagrams/agent-network/`,
  `tests/live/m4/env.sh`, `tests/live/m4/serve_up.sh`, and the Hub tool examples in
  `packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py`.
  Their tunnels are unrelated to phone provisioning.
- Preserve `~/.kollab/phonelink/` in full. Do not automatically delete
  `~/.kollab/phone-access/` if it appears later, invoke its helper, revoke external
  peers, or touch `~/.config/wireguard/`. Recheck state before any later removal;
  externally applied peers need a separately approved operational cleanup.

## Execution and evidence gate after approval

1. Recheck the branch, changed files and manual state before applying only the
   approved file list. Preserve concurrent work and private state.
2. Remove the manual callers and registration together. Do not replace them with
   compatibility routes or weaken shared authentication/network checks.
3. Run focused frontend type/QR/Learn checks and backend WebUI-boundary and
   PhoneLink tests. Retain `tests/unit/test_phone_link.py`,
   `tests/unit/test_phone_link_portal.py`,
   `tests/unit/test_phone_link_engine_streams.py` and their negative cases.
   Check that authenticated manual `/phone` API requests are no longer served.
4. Build the selected frontend once for the actual app. Check PhoneLink/Learn on
   desktop and at 390px/820px, with no WireGuard form and a complete readable QR.
   Refresh only the reviewed screenshot, preserving other assets.
5. Verify pairing, saved identity, reconnect, revoke and streamed responses.
   Physical cellular access remains a separate acceptance check. Preserve all
   running agents; coordinate any engine restart before live activation.
6. No relay protocol, website, DNS, proxy or connection-service deployment change
   is expected from removing this local manual feature. Do not roll those systems
   or alter the existing VPN as an incidental part of implementation.

This audit ran source/history searches and sanitized host-state checks, not tests,
builds, helpers, API mutations or deployments. The report is ready for review;
the proposed removal has not been implemented.
