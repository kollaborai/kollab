---
title: "PhoneLink"
created: 2026-10-20
modified: 2026-10-21
status: active
---
# PhoneLink

PhoneLink opens this computer's Kollab app in a phone browser through the same
signed discovery and outbound encrypted relay transport used by `/connect`.
The normal flow is **Settings > PhoneLink > Link a Phone**, scan with the phone's
camera, compare the pairing code and approve on the computer. No VPN app,
router changes, API-key transfer, or personal server setup is required.

Under the QR code, **Open on This Phone** opens the same invitation link in a new
tab of the current browser and **Copy Link** copies it (with a visible, selectable
fallback when the browser blocks the clipboard), for linking the phone you are
already holding. It is the exact link the QR encodes, so it carries no extra
secret, works five minutes and still needs approval of the pairing code on the
computer. Only share it with yourself. Both buttons work from the keyboard (Tab,
Enter or Space) with a visible focus ring; screenshots at
[390](../screenshots/phone-access/phonelink-link-390.png),
[820](../screenshots/phone-access/phonelink-link-820.png) and
[1280](../screenshots/phone-access/phonelink-link-1280.png).

The computer and its engine must remain running and online. The selected
`/connect` directory must serve the PhoneLink browser portal. Source support is
not a claim that a public directory has been updated: the app checks its
capability endpoint before offering a QR code.

## PhoneLink contract

- Pairing uses a fresh private relay room per phone, separate from the agent
  network's room. The QR carries an expiring invitation in the URL fragment,
  never a query parameter, engine token, or phone private key. The browser
  generates its own identity and keeps it on that phone.
- Scanning requests access. Only an explicit local approval pins that phone's
  key. An invitation admits at most one phone and expires after five minutes.
- A phone is a trusted app viewer/controller, not an agent-network member.
  It is not enrolled for network configuration sync or given an agent identity.
  Provider calls and tools still run on the computer; no credential setup is
  needed on the phone. Approve only phones you trust with the app's controls.
- The existing authenticated, session-bound relay envelopes carry a dedicated
  phone channel. A fresh ephemeral key exchange protects the HTTP content per
  connection; reconnect invalidates the old session and its requests.
- The engine dispatches allowed app API calls internally. No public engine
  listener, arbitrary destination proxy, or phone-management API is exposed.
  Chat streaming, explicit Stop, history, and normal tool approvals retain
  their existing engine behavior. Dropping a stream does not cancel agent work.
- Saved phone identities reconnect after an engine restart, even without a
  running terminal agent. Revoke immediately
  withdraws that phone's access. No status says connected until the phone has
  completed its authenticated session.
- One engine owns PhoneLink for this computer at a time. Another running engine
  directs you to manage phones in the owner instead of accepting changes to
  stale phone state. Closing the owner releases it. Start the next engine, or
  open PhoneLink in an already-running engine, to restore saved identities and
  approvals without pairing again. Phone retries alone do not activate a
  standby engine.
- The PhoneLink tab owns linking and saved phones.
- The QR display preserves the SVG's full drawing frame when resized, including
  all three corner markers and the surrounding white margin.

Verification must cover the real encrypted browser-to-engine path, rejected and
expired invitations, wrong keys, replay, reconnect, revoke, streamed responses,
and 390px/820px/desktop layouts. Public-service deployment and a physical phone
scan remain separate acceptance gates.

## Signed app served from the computer

The connection service never supplies the phone app. A QR opens a small
**loader** from the directory (`/relay/v1/phone/`: `index.html`, `loader.js`,
icons, manifest; built into `dist/phonelink/loader`). The loader holds no app
UI. It joins the pairing room, waits for local approval, then fetches the app
**from this computer** over the encrypted session (`GET /phone-app/...`, served
by PhoneLink itself, never by the engine's routes, read-only, confined to the
built files) and runs it from a verified copy.

- **Signature.** The computer lists the app files (name, SHA-256, size) with a
  build serial, hashes that list, and signs `kollab-phonelink-app/1`, the host
  key and the hash with the room's Ed25519 device key: the host key from the QR.
  The phone recomputes the hash itself (it never trusts the sender's), verifies
  the signature against the QR's host key, then checks every file's SHA-256
  before running anything. A wrong key, a changed file list or serial, a
  changed file, a malformed list or an older serial than the copy already on
  the phone is refused with a plain message and nothing runs.
- **Fingerprint.** Both screens show the host key's fingerprint
  (`XXXX-XXXX-XXXX-XXXX-XXXX`) beside a 12-character pairing code. Compare both
  before approving.
- **Updates.** A new signed app replaces the stored copy only after it
  verifies. The verified copy is kept in the phone browser (IndexedDB) and is
  checked again at every launch, so a modified copy is re-fetched from the
  computer, or refused when the computer is out of reach.
- **Home Screen.** The loader is installable (manifest and icons), and the
  verified app opens from its stored copy. Safari's Home Screen app has its own
  storage (WebKit copies cookies, not local storage), so a phone linked in the
  Safari tab is not linked inside a new iOS Home Screen app. On Android and
  desktop browsers the installed app shares the link.
- **Last seen and revoke.** The computer shows each phone's last-seen time and
  a Revoke button per phone; revoke closes that phone's room at once.

Screenshots (a scratch relay and host, a stubbed engine): Settings > PhoneLink
at [390](../screenshots/phone-access/phonelink-panel-390.png),
[820](../screenshots/phone-access/phonelink-panel-820.png) and
[1280](../screenshots/phone-access/phonelink-panel-1280.png); the phone's pairing
screen ([390](../screenshots/phone-access/phonelink-pairing-390.png)), the app
served by the computer ([390](../screenshots/phone-access/phonelink-app-390.png),
[820](../screenshots/phone-access/phonelink-app-820.png),
[1280](../screenshots/phone-access/phonelink-app-1280.png)) and a refused copy
([390](../screenshots/phone-access/phonelink-refused-390.png)).

### Security notes

- The first page, the loader, still comes from the directory: a browser cannot
  verify the code that does the verifying. It is small, contains no app UI or
  secrets, and the relay can no longer change what runs after it, but a
  malicious directory could serve a modified loader to a new phone. Use a
  directory you trust for the QR scan; compare the fingerprint on both screens.
- The relay still sees only encrypted packets; it cannot sign for the computer.
- The signature proves the computer built the file list, not that it is
  harmless; approve only phones you trust with the app's controls.
- Fingerprints and signatures use the room's device key, so they change when a
  new invitation (a new room) is created.
- The downgrade check is per phone: the signed serial must not go down. After
  reinstalling an older Kollab, link the phone again.

Verification: `tests/unit/test_phone_link_app.py` (signature, tampered bundle,
wrong key, path confinement, last seen), `frontend/tests/phone-app-verify.test.ts`
(the phone's verifier), `frontend/tests/phonelink-browser-e2e.mjs` (real
Chromium, real relay service and PhoneLink host on scratch ports and a scratch
HOME) and `tests/tmux/specs/phonelink_signed_app.json`. A physical phone scan on
a public directory remains a separate acceptance gate.

## Make the WebUI reachable on the home network

By default, the WebUI binds to `127.0.0.1`. To serve it on the host's LAN
address, list that explicit private interface IP in `KOLLAB_WEBUI_HOSTS` before
starting Kollab:

```sh
KOLLAB_WEBUI_HOSTS=192.168.1.20 kollab --web-ui
```

The WebUI port defaults to `8080` and can be changed with `KOLLAB_WEBUI_PORT`.
The engine remains on loopback; browser requests reach it through the WebUI proxy.
`KOLLAB_WEBUI_HOSTS` accepts literal loopback, RFC1918, IPv6 ULA, and link-local
addresses only. Wildcards, unspecified/public addresses, and DNS names fail at
startup with an actionable error. The WebUI verifies the Host and same-origin
API request, then allows only clients in the exact CIDR assigned to a configured
bind interface. Other addresses, including arbitrary RFC1918 networks, do not
inherit access.

The WebUI supports direct private binding only. Uvicorn proxy-header rewriting
is disabled, and WebUI requests carrying `Forwarded` or `X-Forwarded-*` client
chains are rejected. Do not place this WebUI behind a reverse proxy or public
port-forward. A request must come from a configured private subnet and use the
matching Host/Origin. The engine requires bearer authentication and accepts
engine routes only through its loopback WebUI connection. `/api/config` provides
the token only to allowed clients and every success/error response is `no-store`.
