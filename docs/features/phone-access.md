---
title: "PhoneLink"
created: 2026-10-20
modified: 2026-10-20
status: active
---
# PhoneLink

PhoneLink opens this computer's Kollab app in a phone browser through the same
signed discovery and outbound encrypted relay transport used by `/connect`.
The normal flow is **Settings > PhoneLink > Link a Phone**, scan with the phone's
camera, compare the pairing code and approve on the computer. No WireGuard app,
router changes, API-key transfer, or personal server setup is required.

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
- The PhoneLink tab owns linking and saved phones. **Advanced Settings** starts
  collapsed and contains the existing manual WireGuard access controls below.

Verification must cover the real encrypted browser-to-engine path, rejected and
expired invitations, wrong keys, replay, reconnect, revoke, streamed responses,
and 390px/820px/desktop layouts. Public-service deployment and a physical phone
scan remain separate acceptance gates.

PhoneLink currently supports returning through the same phone browser's bookmark.
Standalone Home Screen installation needs a separate identity handoff and is not
claimed as working: Safari copies cookies, but not local storage, into a new
Home Screen app ([WebKit's Safari 17.2 guidance](https://webkit.org/blog/14787/webkit-features-in-safari-17-2/)).
The manual WireGuard flow below does not depend on this browser pairing identity.

## Advanced: manual WireGuard access

Phone Access creates one WireGuard peer per phone and presents its client
configuration as a one-time QR code in the WebUI. The phone can then reach the
Kollab portal over the home network and install it as a standalone home-screen
web app.

Kollab manages only its generated phone peer snippet and the WebUI flow. It does
not install or configure WireGuard, change an existing server or router config,
set up DNS, open a firewall, or create a router UDP port-forward.

## Prerequisites

Before adding a phone, an operator must provide:

- A WireGuard server reachable by the phone, with a private tunnel subnet. Kollab
  uses the first usable address for the server and assigns phone addresses after
  it. The default is `10.77.0.0/24`; use a different private IPv4 subnet if it
  conflicts with the home LAN or another tunnel.
- The server's public key (never its private key) and a reachable endpoint in
  `host:UDP-port` form. Bracket IPv6 endpoints, for example
  `[2001:db8::1]:51820`.
- A home router/firewall configured by its owner to forward the selected UDP port
  to the WireGuard server, and permit that traffic. The server's listen port and
  the endpoint port must match. Kollab never edits the router or firewall.
- A public DNS name that resolves to the home's public address if the address is
  dynamic or should not be typed into each phone. Configure dynamic DNS outside
  Kollab. If the ISP uses carrier-grade NAT, ordinary inbound port forwarding may
  not work; arrange a reachable VPN endpoint with the network provider.
- A Kollab WebUI address reachable from the phone after the tunnel is up. For a
  phone on the home Wi-Fi, that is usually the Kollab host's LAN address and the
  WebUI port. For access over a VPN, use an address routed by that VPN.
- A trusted host-admin apply helper, as described below. The helper must reconcile
  Kollab's current peer snippet with the server and disconnect a peer that is no
  longer present. Without it, add and revoke are disabled.

The endpoint, port-forward, WebUI portal address, and WireGuard tunnel are
separate pieces: forwarding UDP makes the WireGuard server reachable; it does not
make the Kollab WebUI public. Keep the WebUI on private addresses only.

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
bind interface or the `/32` address of an active phone peer. Other addresses in
the WireGuard pool and arbitrary RFC1918 networks do not inherit access.

Phone access supports direct private binding only. Uvicorn proxy-header rewriting
is disabled, and WebUI requests carrying `Forwarded` or `X-Forwarded-*` client
chains are rejected. Do not place this WebUI behind a reverse proxy or public
port-forward. A request must come from a configured private subnet and use the
matching Host/Origin. The engine requires bearer authentication and accepts
phone routes only through its loopback WebUI connection. `/api/config` provides
the token only to allowed clients and every success/error response is `no-store`.

If the WebUI host is not the WireGuard server, configure the phone's `AllowedIPs`
to include the home LAN network containing the WebUI host. Prefer the narrow LAN
CIDR (for example `192.168.1.0/24`) instead of a broad route. Set the portal URL
to the numeric private WebUI address and port, for example
`http://192.168.1.20:8080`; DNS names are for the WireGuard endpoint, not the
WebUI Host in this direct-bind mode. If the server itself hosts the WebUI, its
tunnel address may be sufficient. Set the optional DNS field to an IP address
reachable through the selected routes.

This direct-bind mode serves HTTP on the private network. Kollab does not install
TLS or change the router. If a phone's OS requires trusted HTTPS to add a home
screen app, do not add a public reverse proxy; the TLS-terminating proxy path is
not supported by this phone-access security boundary.

## Configure the trusted server apply helper

The engine reads `KOLLAB_PHONE_ACCESS_APPLY_COMMAND` from its host environment.
This is intentionally not editable from the phone settings API: allowing a LAN
client to choose an executable would turn peer management into remote command
execution on the Kollab host.

Configure it only for an operator-owned executable that reconciles the latest
peer file with the intended WireGuard server. Each invocation receives the
absolute path to `peers.conf` as its final argument and in the
`KOLLAB_PHONE_ACCESS_PEERS_FILE` environment variable. The peer file contains
only phone `[Peer]` blocks with public keys and tunnel addresses. The helper
must preserve the operator's non-phone peers, apply the current phone peers, and
remove a revoked phone peer from the running server before returning success.


For a WireGuard interface on the Kollab host, a reviewed root-owned helper can
combine the normal server config with the generated phone peer file and sync the
complete peer set. For example, adapt this helper to the interface and config
path on that host:

```sh
#!/usr/bin/env bash
set -euo pipefail
umask 077
interface=wg0
server_config=/etc/wireguard/wg0.conf
phone_peers=${1:?expected the Kollab peers.conf path}
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
cat "$server_config" > "$workdir/combined.conf"
printf '\n' >> "$workdir/combined.conf"
cat "$phone_peers" >> "$workdir/combined.conf"
wg-quick strip "$workdir/combined.conf" > "$workdir/wg.conf"
wg syncconf "$interface" "$workdir/wg.conf"
```

Keep all non-phone peers in the normal server config so `syncconf` preserves
them. Install the helper with the least privileges needed; if the engine calls
a narrow `sudo` wrapper, restrict that authorization to this reviewed helper.
Arrange for the same peer set to load after a server reboot. This example is
not installed or run by Kollab, and does not change the router or server config
file.


The engine executes the configured argv without a shell, captures output without
logging it, and imposes a 30-second timeout. A missing or invalid helper disables
add/revoke. A non-zero exit is reported as a failure and Kollab restores the
previous local peer file; if a helper partially changed a remote server before
failing, inspect that server before retrying. Return success only after the server
has applied the complete peer set. Kollab does not edit systemd, WireGuard service
files, or existing server configs.

The helper may need elevated privileges. Install and authorize it separately
using the narrowest host policy appropriate for that machine. Never point this
setting at an unreviewed script or accept its value from an API request. To
change it, update the engine's trusted environment locally and restart the
engine/WebUI process.

## Add and revoke phones

1. Open the WebUI from the private home network or a permitted VPN.
2. Open the session settings panel and enter the server endpoint, server public
   key, private tunnel subnet, phone routes, optional DNS, and portal address.
   Save settings. Kollab validates IP networks, keys, ports, and peer names.
3. Add a phone. The engine generates a fresh client keypair, writes only the
   public-key peer block, asks the trusted helper to apply it, then returns the QR
   once. The QR response is marked `no-store`; the private key is not included as
   plaintext in the JSON response, stored in state, written to the server peer
   file, included in later API responses, or logged. Do not screenshot or share
   the QR: it contains the phone's private key in encoded form.
4. On the phone, scan the QR from the WireGuard app and activate its tunnel. Then
   open the configured private portal address in Safari and use Share > Add to
   Home Screen. The Web App Manifest and Apple touch icon request standalone
   full-screen launch. The WebUI still needs a network connection to work.
5. Revoke a lost phone from its peer row. Kollab removes only that peer from its
   snippet and requires the host helper to apply the reduced peer set. The API
   returns success only after the helper exits successfully; on failure it keeps
   the previous local peer record and shows an actionable error.

The QR is held only in the browser component's in-memory state after creation.
Hide it or leave the settings panel when it has been scanned. A later state,
settings, or revoke response never contains that QR or client private key.
