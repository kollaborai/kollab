# ruff: noqa: E501
"""Diagrams of the agent network for README.md and docs/architecture/agent-network.md.

    python scripts/build_network_diagrams.py

writes docs/diagrams/agent-network/<figure>-light.svg and -dark.svg (GitHub picks
one with <picture> and prefers-color-scheme). Every label states the current
code: change the code, change the label here, run this again.
"""

import html
import re
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "diagrams" / "agent-network"


def e(s):
    return html.escape(str(s), quote=False)


def t(x, y, s, cls="", size=None, anchor=None, weight=None):
    a = [f'x="{x}"', f'y="{y}"']
    if cls:
        a.append(f'class="{cls}"')
    if size:
        a.append(f'font-size="{size}"')
    if anchor:
        a.append(f'text-anchor="{anchor}"')
    if weight:
        a.append(f'font-weight="{weight}"')
    return f'<text {" ".join(a)}>{e(s)}</text>'


def rect(x, y, w, h, cls="", rx=8, extra=""):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" class="{cls}" {extra}/>'


def line(x1, y1, x2, y2, cls="ln", extra=""):
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="{cls}" {extra}/>'


def path(d, cls="ln", extra=""):
    return f'<path d="{d}" class="{cls}" fill="none" {extra}/>'


def markers(p):
    out = ["<defs>"]
    for name, cls in (("a", "mk"), ("s", "mk-seal"), ("g", "mk-agent"), ("d", "mk-dim"), ("w", "mk-warn")):
        out.append(
            f'<marker id="{p}-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="10" '
            f'markerHeight="10" markerUnits="userSpaceOnUse" orient="auto-start-reverse">'
            f'<path d="M0,1 L9,5 L0,9 z" class="{cls}"/></marker>'
        )
    out.append("</defs>")
    return "".join(out)


def svg(vb_w, vb_h, label, body, min_w):
    return (
        f'<svg viewBox="0 0 {vb_w} {vb_h}" role="img" aria-label="{html.escape(label)}" '
        f'style="min-width:{min_w}px">{body}</svg>'
    )


# ---------------------------------------------------------------- figure 1
def fig_topology():
    p = "f1"
    o = [markers(p)]
    # ssh session: not part of kollab
    o.append(path("M 155 52 V 30 H 845 V 116", "lndim", 'stroke-dasharray="2 5"' + f' marker-end="url(#{p}-d)"'))
    o.append(t(500, 22, "your SSH session: only your keyboard. kollab never uses it.", "m dimf", 11.5, "middle"))
    for x, title, sub in ((20, "Your Mac", "kollab started in ~/work"),
                          (365, "kollabor.ai", "the directory, public"),
                          (710, "New VPS", "kollab started in its folder")):
        o.append(rect(x, 52, 270, 320, "fpanel sline", 14))
        o.append(t(x + 20, 82, title, "", 15, None, 700))
        o.append(t(x + 20, 100, sub, "m dimf", 11))
    # Mac + VPS internals
    for x, win_title, win_sub, dashed, agents, foot1, foot2 in (
        (40, "kollab window", "what you type into", False, ("koordinator", "lapis"),
         "start: kollab  ·  kollab -d", "always on: kollab service install"),
        (730, "kollab window (optional)", "over SSH, to set up or watch", True, ("koordinator", "infra"),
         "inbound ports needed: none", "its firewall can stay closed"),
    ):
        o.append(rect(x, 116, 230, 46, "fbg ln", 8, 'stroke-dasharray="5 4"' if dashed else ""))
        o.append(t(x + 16, 136, win_title, "", 13, None, 700))
        o.append(t(x + 16, 152, win_sub, "dimf", 11))
        o.append(line(x + 115, 162, x + 115, 186, "ln"))
        o.append(t(x + 123, 178, "unix socket", "m dimf", 10.5))
        o.append(rect(x, 186, 230, 66, "fbg ln thick", 8))
        o.append(t(x + 16, 207, "daemon", "", 13, None, 700))
        o.append(t(x + 16, 223, "holds the network link", "dimf", 11.5))
        o.append(t(x + 16, 240, "key: ~/.kollab/network/<id>/", "m dimf", 10.5))
        cx = x
        for name in agents:
            w = 16 + len(name) * 7
            o.append(rect(cx, 266, w, 22, "fbg lnagent", 11))
            o.append(t(cx + w / 2, 281, name, "m agentf", 11, "middle"))
            cx += w + 8
        o.append(t(x, 306, "agents run inside the daemon", "dimf", 11))
        o.append(t(x, 340, foot1, "m dimf", 11))
        o.append(t(x, 357, foot2, "m dimf", 11))
    # directory internals
    o.append(rect(385, 116, 230, 62, "fbg ln", 8))
    o.append(t(401, 136, "signed key file", "", 13, None, 700))
    o.append(t(401, 152, "TXT _agent.kollabor.ai", "m dimf", 10.5))
    o.append(t(401, 168, "→ /.well-known/agent-keys", "m dimf", 10.5))
    o.append(rect(385, 196, 230, 46, "fbg ln thick", 8))
    o.append(t(500, 215, "TLS proxy :443", "", 13, "middle", 700))
    o.append(t(500, 232, "TLS ends here", "dimf", 11, "middle"))
    o.append(line(500, 242, 500, 260, "ln", f'marker-end="url(#{p}-a)"'))
    o.append(rect(385, 262, 230, 58, "fbg ln", 8))
    o.append(t(500, 281, "relay", "", 13, "middle", 700))
    o.append(t(500, 297, "forwards frames by device key", "dimf", 11, "middle"))
    o.append(t(500, 312, "can't open them", "sealf", 11, "middle", 700))
    o.append(rect(385, 330, 112, 34, "fbg sline", 6))
    o.append(t(441, 344, "mailbox", "", 11, "middle", 700))
    o.append(t(441, 358, "join codes, 5 min", "dimf", 10, "middle"))
    o.append(rect(503, 330, 112, 34, "fbg sline", 6))
    o.append(t(559, 344, "presence", "", 11, "middle", 700))
    o.append(t(559, 358, "who's online", "dimf", 10, "middle"))
    # the two outbound links
    o.append(line(270, 219, 383, 219, "ln thick", f'marker-end="url(#{p}-a)"'))
    o.append(t(327, 210, "dials out", "m", 11.5, "middle"))
    o.append(t(327, 236, "wss :443", "m dimf", 11.5, "middle"))
    o.append(line(730, 219, 617, 219, "ln thick", f'marker-end="url(#{p}-a)"'))
    o.append(t(672, 210, "dials out", "m", 11.5, "middle"))
    o.append(t(672, 236, "wss :443", "m dimf", 11.5, "middle"))
    return svg(1000, 384,
               "Both the Mac's and the VPS's kollab daemons dial out to kollabor.ai on port 443; "
               "the relay forwards frames between them without opening them.",
               "".join(o), 760)


# ---------------------------------------------------------------- figure 2
MAC, DIR, VPS = 170, 470, 770
NX = 880
W2 = 1230

ROWS = [
    ("phase", "A", "Before you start · on the Mac"),
    ("arrow", 1, MAC, DIR, "wss /relay/v1/ws", "solid", None,
     ["The Mac's daemon dials out on port 443 and", "stays on. It signs a nonce and joins its room."]),
    ("phase", "B", "Make a code · on the Mac"),
    ("you", 2, MAC, "you: /connect",
     ["The Connect screen opens and the daemon", "starts a one-device offer."]),
    ("self", 3, MAC, "make code + keys", None,
     ["XXXX-XXXX: 40 bits, 5 minutes, one device.", "From it: lookup tag, verifier, sealing key."]),
    ("arrow", 4, MAC, DIR, "POST /enrollment/offers", "solid", None,
     ["Hashes of the tag and verifier, Mac key, room,", "expiry. Signed. The code itself never leaves."]),
    ("you", 5, MAC, "screen: 7QK4-M2XP · 4:59",
     ["Shown on a private screen only. Never in a", "command, a log or the chat."]),
    ("arrow", 6, MAC, DIR, "POST …/poll · every 15 s", "dashed", None,
     ["The Mac keeps asking the mailbox for a joiner."]),
    ("phase", "C", "Type the code · on the VPS, over SSH"),
    ("you", 7, VPS, "you: type the code",
     ["Install kollab, run it, pick Join with a code", "(or /connect). Masked form, never argv."]),
    ("arrow", 8, VPS, DIR, "TXT _agent.kollabor.ai", "solid", None,
     ["Reads the directory's signed key file and pins", "its key. A changed key is refused later."],
     "GET /.well-known/agent-keys"),
    ("arrow", 9, VPS, DIR, "POST /enrollment/lookup", "solid", None,
     ["VPS key + lookup tag → offer id. A wrong code", "finds nothing; 10 tries a minute per IP."]),
    ("arrow", 10, VPS, DIR, "POST …/request", "solid", None,
     ["VPS key, device name, verifier, and a request", "sealed with the code key. Now it waits."]),
    ("phase", "D", "Prove it · automatic, both sides"),
    ("arrow", 11, DIR, MAC, "poll → claimed", "dashed", "seal",
     ["Verifier matched. The Mac gets the sealed", "request on its next poll."]),
    ("arrow", 12, MAC, DIR, "POST …/challenge", "solid", "seal",
     ["Sealed with the code key, signed by the Mac,", "pinned to the VPS key."]),
    ("arrow", 13, DIR, VPS, "reply/poll → challenge", "dashed", "seal",
     ["Only the code holder could seal this, so the", "VPS now trusts the Mac's key. Polls 5–20 s."]),
    ("arrow", 14, VPS, DIR, "POST …/proof", "solid", "seal",
     ["The VPS signs the challenge with its own", "device key."]),
    ("arrow", 15, DIR, MAC, "poll → proof", "dashed", "seal",
     ["The Mac checks that signature."]),
    ("phase", "E", "You decide · on the Mac"),
    ("you", 16, MAC, "screen: vps-home wants to join",
     ["Device name + key fingerprint, and one line in", "the main pane if the screen is closed."]),
    ("you", 17, MAC, "you: press a",
     ["A person accepts. A guessed code still needs", "this key press. /connect accept works too."]),
    ("phase", "F", "Hand-over · automatic"),
    ("arrow", 18, MAC, DIR, "POST …/decision", "solid", "seal",
     ["Sealed: membership credential, room invite,", "network name, active LLM profile + API key."]),
    ("arrow", 19, DIR, VPS, "reply/poll → decision", "dashed", "seal",
     ["Ciphertext. kollabor.ai can't open it."]),
    ("self", 20, VPS, "install + sign receipt", "seal",
     ["Saves the profile privately, all or nothing,", "then signs a receipt with its device key."]),
    ("arrow", 21, VPS, DIR, "POST …/ack", "solid", "seal",
     ["The signed receipt, sealed with the code key."]),
    ("arrow", 22, VPS, DIR, "wss /relay/v1/ws", "solid", None,
     ["The VPS dials out and holds its own link in", "your room. Screen: joined macbook-kollab-net."]),
    ("arrow", 23, DIR, MAC, "ack/poll → receipt", "dashed", "seal",
     ["The Mac checks the receipt and approves the", "VPS key. Screen: accepted vps-home."]),
    ("phase", "G", "Connected · from now on"),
    ("both", 24, "peers",
     ["Each side sees the other online. Roster:", "koordinator@vps-home, infra@vps-home."]),
    ("thru", 25, "TLS 1.3 handshake, inside Box frames", "seal", True,
     ["Mutual, pinned to both device keys. Set up", "on first use; every message rides inside."]),
    ("thru", 26, "sealed config sync", "seal", False,
     ["Settings, loadouts, MCP, API keys, agents/,", "skills/. On change (checked every 10 s)."]),
    ("thru", 27, "lapis → infra@vps-home", "agent", False,
     ["A normal hub message. infra runs it with its", "own tools and answers the same way."]),
]

COLOR = {None: ("ln", "a"), "seal": ("lnseal", "s"), "agent": ("lnagent", "g")}


def note(o, n, cy, lines, cls=""):
    o.append(f'<circle cx="{NX + 10}" cy="{cy - 11}" r="10" class="fbg {cls or "ln"}"/>')
    o.append(t(NX + 10, cy - 7, str(n), "m numf", 11, "middle", 700))
    for i, s in enumerate(lines):
        o.append(t(NX + 28, cy - 7 + i * 16, s, "", 12.5))


def fig_sequence():
    p = "f2"
    for r in ROWS:
        notes = next((x for x in r if isinstance(x, list)), [])
        for s in notes:
            assert len(s) <= 47, (r[1], s, len(s))
    y = 92
    bands, rows = [], []
    for r in ROWS:
        if r[0] == "phase":
            y += 10
            bands.append((y, r[1], r[2]))
            y += 26 + 26
            continue
        rows.append((y, r))
        y += 52 if (r[0] == "arrow" and len(r) > 8) else 46
    height = y + 6
    o = [markers(p)]
    # lane headers
    for x, title, sub in ((MAC, "Your Mac", "daemon in ~/work"),
                          (DIR, "kollabor.ai", "relay + join-code mailbox"),
                          (VPS, "New VPS", "daemon in its folder")):
        o.append(rect(x - 100, 14, 200, 54, "fpanel ln", 10))
        o.append(t(x, 37, title, "", 14, "middle", 700))
        o.append(t(x, 55, sub, "m dimf", 11, "middle"))
    o.append(t(NX, 37, "What happens", "", 14, None, 700))
    o.append(t(NX, 55, "dashed = an answer to a poll", "m dimf", 11))
    # phase bands
    for by, letter, title in bands:
        o.append(rect(0, by, W2, 26, "fpanel", 0))
        o.append(t(16, by + 17, f"{letter}  {title}".upper(), "d dimf", 11))
    # lifelines
    for x in (MAC, DIR, VPS):
        o.append(line(x, 68, x, height - 6, "lnline lifeline"))
    for cy, r in rows:
        kind, n = r[0], r[1]
        if kind == "arrow":
            _, n, a, b, label, style, color, notes = r[:8]
            sub = r[8] if len(r) > 8 else None
            cls, mk = COLOR[color]
            end = b - 8 if b > a else b + 8
            dash = ' stroke-dasharray="6 5"' if style == "dashed" else ""
            o.append(line(a, cy, end, cy, f"{cls} med", f'marker-end="url(#{p}-{mk})"{dash}'))
            mid = (a + b) / 2
            o.append(t(mid, cy - 7, label, "m", 12, "middle"))
            if sub:
                o.append(t(mid, cy + 16, sub, "m dimf", 11.5, "middle"))
            note(o, n, cy, notes, "lnseal" if color == "seal" else "")
        elif kind == "you":
            _, n, lane, label, notes = r
            w = len(label) * 7.3 + 22
            o.append(rect(lane - w / 2, cy - 13, w, 24, "fbg lnyou med", 12))
            o.append(t(lane, cy + 3, label, "m youf", 12, "middle", 600))
            note(o, n, cy, notes, "lnyou")
        elif kind == "self":
            _, n, lane, label, color, notes = r
            cls, mk = COLOR[color]
            d = 1 if lane < DIR else -1
            o.append(path(f"M {lane} {cy - 10} H {lane + 36 * d} V {cy + 8} H {lane + 8 * d}",
                          f"{cls} med", f'marker-end="url(#{p}-{mk})"'))
            o.append(t(lane + 44 * d, cy + 4, label, "m", 12, "start" if d > 0 else "end"))
            note(o, n, cy, notes, "lnseal" if color == "seal" else "")
        elif kind == "both":
            _, n, label, notes = r
            o.append(f'<circle cx="{DIR}" cy="{cy}" r="4" class="fbg ln"/>')
            for b in (MAC, VPS):
                end = b + 8 if b < DIR else b - 8
                o.append(line(DIR + (-4 if b < DIR else 4), cy, end, cy, "ln med", f'marker-end="url(#{p}-a)"'))
                o.append(t((DIR + b) / 2, cy - 7, label, "m", 12, "middle"))
            note(o, n, cy, notes)
        elif kind == "thru":
            _, n, label, color, double, notes = r
            cls, mk = COLOR[color]
            ends = f'marker-end="url(#{p}-{mk})"' + (f' marker-start="url(#{p}-{mk})"' if double else "")
            o.append(line(MAC + 8, cy, VPS - 8, cy, f"{cls} thick", ends))
            o.append(rect(DIR - 6, cy - 6, 12, 12, "fbg ln", 2))
            o.append(t((MAC + DIR) / 2, cy - 8, label, "m", 12, "middle"))
            o.append(t((DIR + VPS) / 2, cy - 8, "via relay, unread", "m dimf", 11, "middle"))
            note(o, n, cy, notes, "lnseal" if color == "seal" else ("lnagent" if color == "agent" else ""))
    return svg(W2, height,
               "The join in 27 steps between your Mac, kollabor.ai and the new VPS: the Mac makes a code, "
               "the VPS uses it to claim a mailbox offer, both prove keys through sealed envelopes, you "
               "accept on the Mac, the VPS receives its credential and profile, then both hold relay links "
               "and talk over TLS 1.3 inside Box frames.",
               "".join(o), 1000)


# ---------------------------------------------------------------- figure 3
def fig_layers():
    o = []
    layers = [
        (20, 24, 680, 352, "fpanel ln", "1", "wss://kollabor.ai/relay/v1/ws", "TLS, ends at the proxy", ""),
        (44, 66, 632, 296, "fbg ln", "2", 'relay frame {type:"send", to:<VPS key>, ciphertext}', "", ""),
        (68, 108, 584, 240, "fpanel lnseal thick", "3", "NaCl Box · keys from both devices", "30 s expiry", "sealf"),
        (92, 150, 536, 184, "fbg lnseal thick", "4", "mutual TLS 1.3 · pinned device certs", "kollab-agent/1", "sealf"),
        (116, 192, 488, 128, "fpanel lnagent thick", "5", "hub message", "", "agentf"),
    ]
    for x, y, w, h, cls, n, lab, dim, numcls in layers:
        o.append(rect(x, y, w, h, cls, 12))
        o.append(t(x + 16, y + 26, n, f"m {numcls or 'numf'}", 13, None, 700))
        o.append(t(x + 34, y + 26, lab, "m", 12.5))
        if dim:
            o.append(t(x + w - 16, y + 26, dim, "m dimf", 11.5, "end"))
    for i, (k, v) in enumerate((("to:", "infra@vps-home"), ("from:", "lapis@macbook-kollab"),
                                ("text:", '"check the wireguard tunnel…"  ≤16 KB'))):
        o.append(t(150, 248 + i * 20, k, "m dimf", 12))
        o.append(t(204, 248 + i * 20, v, "m", 12))
    # brackets
    o.append(path("M 716 30 H 722 V 102 H 716", "ln"))
    o.append(t(736, 50, "kollabor.ai can read 1–2", "", 13, None, 700))
    o.append(t(736, 68, "your IP, both device keys,", "dimf", 12))
    o.append(t(736, 84, "frame size and timing", "dimf", 12))
    o.append(path("M 716 112 H 722 V 370 H 716", "lnseal thick"))
    o.append(t(736, 136, "only your two daemons", "sealf", 13, None, 700))
    o.append(t(736, 154, "can open 3–5", "sealf", 13, None, 700))
    for i, s in enumerate(("TLS 1.3 adds forward secrecy:", "a key stolen later can't", "decrypt old traffic.", "",
                           "The TLS session is dropped", "when either side reconnects", "or is revoked.")):
        if s:
            o.append(t(736, 186 + i * 17, s, "dimf", 12))
    return svg(1000, 392,
               "One connection, five layers: the wss link and relay frame are visible to kollabor.ai; "
               "the NaCl Box, the mutual TLS 1.3 session and the hub message inside are readable only "
               "by your two daemons.",
               "".join(o), 760)


# ---------------------------------------------------------------- figure 4
def fig_lifecycle():
    p = "f4"
    o = [markers(p)]
    states = [
        (40, "window + daemon", True, "you're in the TUI"),
        (385, "daemon only", True, "no window, SSH can close"),
        (730, "no daemon", False, "peers list it as offline"),
    ]
    for x, title, online, sub in states:
        o.append(rect(x, 170, 230, 72, "fpanel ln thick", 12))
        cx = x + 115
        o.append(t(cx, 195, title, "", 14, "middle", 700))
        word = "online" if online else "offline"
        o.append(f'<circle cx="{cx - 30}" cy="{210}" r="4.5" class="{"fseal" if online else "fbg ln"}"/>')
        o.append(t(cx - 20, 214, word, "m " + ("sealf" if online else "dimf"), 12, None, 700))
        o.append(t(cx, 232, sub, "dimf", 11.5, "middle"))
    arcs = [
        ("M 235 170 Q 327 112 420 170", "ln med", "a", [(327, 120, "Ctrl+Z", "m", 12.5, 700),
                                                     (327, 134, "terminal closes · SSH drops", "dimf", 11.5, None)]),
        ("M 580 170 Q 672 112 765 170", "lnwarn med dash", "w", [(672, 120, "reboot, no service", "m warnf", 12.5, 700),
                                                         (672, 134, "kollab --hub stop <name>", "m dimf", 11.5, None)]),
        ("M 845 170 C 845 40 155 40 155 170", "lnyou med", "a", [(500, 42, "kollab   (in the device's folder)", "m youf", 12.5, 700),
                                                              (500, 59, "reconnects on its own · same device · no new code", "dimf", 11.5, None)]),
        ("M 420 242 Q 327 300 235 242", "ln med", "a", [(327, 292, "kollab in that folder", "m", 12.5, 700),
                                                     (327, 307, "or kollab --attach <name>", "m dimf", 11.5, None)]),
        ("M 765 242 Q 672 300 580 242", "lnyou med", "a", [(672, 292, "kollab -d", "m youf", 12.5, 700),
                                                        (672, 307, "headless, no window", "dimf", 11.5, None)]),
        ("M 155 242 C 155 400 845 400 845 242", "lnwarn med dash", "w", [(500, 386, "Ctrl+C twice in a window opened by plain kollab", "m warnf", 12.5, 700),
                                                                 (500, 402, "or a reboot without kollab service", "dimf", 11.5, None)]),
        ("M 470 242 C 446 334 554 334 530 242", "lnseal med", "s", [(500, 334, "kollab service install", "m sealf", 12.5, 700),
                                                              (500, 350, "crash or reboot: back in 5 s", "dimf", 11.5, None)]),
    ]
    for d, cls, mk, labels in arcs:
        o.append(path(d, cls, f'marker-end="url(#{p}-{mk})"'))
        for x, y, s, c, size, w in labels:
            o.append(t(x, y, s, c, size, "middle", w))
    return svg(1000, 414,
               "Daemon lifecycle: plain kollab opens a window plus daemon, Ctrl+Z or a closed terminal leaves "
               "the daemon running, kollab -d starts it headless, kollab service install has systemd or launchd "
               "bring it back after a crash or reboot, and without a service Ctrl+C twice or a reboot stops it "
               "until kollab runs again in the same folder.",
               "".join(o), 760)


# ---------------------------------------------------------------- figure 4b
DEV, DNSL, SRV, NXD, WD = 110, 330, 550, 664, 1000

DISCOVERY = [
    ("phase", "Your device runs /connect agents.example.com"),
    ("arrow", 1, DEV, DNSL, "TXT _agent.<domain>", "solid", None,
     ["The device asks DNS where the key file is.", "One lookup, 5 s."]),
    ("arrow", 2, DNSL, DEV, "v=aid1;u=https://…", "dashed", None,
     ["A pointer, not proof: HTTPS on the same", "domain. No record? It tries that URL anyway."]),
    ("arrow", 3, DEV, SRV, "GET /.well-known/agent-keys.json", "solid", None,
     ["Real certificate, public address only (unless", "you allow a private range). 64 KiB, 10 s."]),
    ("arrow", 4, SRV, DEV, "signed key file · revision N", "dashed", "seal",
     ["Its Ed25519 key, the relay URL and roles, a", "revision, an expiry 5 min after signing."]),
    ("self", 5, DEV, "check", "seal",
     ["Signed by the key inside it, for this domain,", "not expired. Anything else stops here."]),
    ("self", 6, DEV, "pin", "seal",
     ["First sight: kept as the pin. Later a new key", "is key_changed, an older revision rollback."]),
    ("arrow", 7, DEV, SRV, "wss /relay/v1/ws", "solid", None,
     ["Only if the file names a ready relay. Then", "join codes work exactly as on kollabor.ai."]),
    ("phase", "Meanwhile · on your server"),
    ("self", 8, SRV, "re-sign every 60 s", "seal",
     ["kollab relay serve signs revision N+1. It", "names the relay only while the relay is ready."]),
    ("self", 9, SRV, "on stop", "warn",
     ["One last file, without the relay. Lose", "service.key and pinned devices refuse you."]),
]


def fig_discovery():
    p = "f7"
    o = [markers(p)]
    color = {None: ("ln", "a"), "seal": ("lnseal", "s"), "warn": ("lnwarn", "w")}
    y, bands, rows = 76, [], []
    for r in DISCOVERY:
        if r[0] == "phase":
            y += 8
            bands.append((y, r[1]))
            y += 52
            continue
        rows.append((y, r))
        y += 48
    height = y + 4
    for x, title, sub in ((DEV, "Your device", "/connect <domain>"), (DNSL, "DNS", "at your registrar"),
                          (SRV, "agents.example.com", "kollab relay serve")):
        o.append(rect(x - 92, 14, 184, 54, "fpanel ln", 10))
        o.append(t(x, 37, title, "", 14, "middle", 700))
        o.append(t(x, 55, sub, "m dimf", 11, "middle"))
    o.append(t(NXD, 37, "What it means", "", 14, None, 700))
    o.append(t(NXD, 55, "dashed = an answer", "m dimf", 11))
    for by, title in bands:
        o.append(rect(0, by, WD, 26, "fpanel", 0))
        o.append(t(16, by + 17, title.upper(), "d dimf", 11))
    for x in (DEV, DNSL, SRV):
        o.append(line(x, 68, x, height - 4, "lnline lifeline"))

    def note(n, cy, lines, cls):
        o.append(f'<circle cx="{NXD + 10}" cy="{cy - 11}" r="10" class="fbg {cls}"/>')
        o.append(t(NXD + 10, cy - 7, str(n), "m numf", 11, "middle", 700))
        for i, s in enumerate(lines):
            assert len(s) <= 46, s
            o.append(t(NXD + 28, cy - 7 + i * 16, s, "", 12.5))

    for cy, r in rows:
        if r[0] == "arrow":
            _, n, a, b, label, style, col, notes = r
            cls, mk = color[col]
            end = b - 8 if b > a else b + 8
            dash = ' stroke-dasharray="6 5"' if style == "dashed" else ""
            weight = "thick" if n == 7 else "med"
            o.append(line(a, cy, end, cy, f"{cls} {weight}", f'marker-end="url(#{p}-{mk})"{dash}'))
            o.append(t((a + b) / 2, cy - 7, label, "m", 12, "middle"))
        else:
            _, n, lane, label, col, notes = r
            cls, mk = color[col]
            d = 1 if lane < DNSL else -1
            o.append(path(f"M {lane} {cy - 10} H {lane + 36 * d} V {cy + 8} H {lane + 8 * d}",
                          f"{cls} med", f'marker-end="url(#{p}-{mk})"'))
            o.append(t(lane + 44 * d, cy + 4, label, "m " + {"seal": "sealf", "warn": "warnf"}.get(col, ""), 12,
                       "start" if d > 0 else "end", 600))
        note(n, cy, notes, {"seal": "lnseal", "warn": "lnwarn"}.get(r[6] if r[0] == "arrow" else r[4], "ln"))
    return svg(WD, height,
               "How /connect uses your domain: the device reads the _agent TXT record, which only points at "
               "the key file; it fetches the signed key file over HTTPS, checks the signature, domain and expiry, "
               "pins the key, and dials the relay the file names. kollab relay serve re-signs the file every "
               "60 seconds and drops the relay from it on stop.",
               "".join(o), 760)


# ---------------------------------------------------------------- figure 5a
def fig_selfhost():
    p = "f5"
    o = [markers(p)]
    o.append(rect(16, 96, 130, 72, "fbg ln", 10))
    o.append(t(81, 120, "your devices", "", 13, "middle", 700))
    o.append(t(81, 138, "/connect", "m dimf", 11, "middle"))
    o.append(t(81, 154, "agents.example.com", "m dimf", 10, "middle"))
    o.append(rect(16, 218, 176, 118, "fbg ln", 10))
    o.append(t(30, 240, "DNS, at your registrar", "", 12, None, 700))
    for i, s in enumerate(("_agent.agents.example.com", "TXT \"v=aid1;u=https://", "agents.example.com/", ".well-known/", "agent-keys.json\"")):
        o.append(t(30, 260 + i * 15, s, "m dimf", 10))
    o.append(line(81, 168, 81, 216, "lndim", f'stroke-dasharray="2 4" marker-end="url(#{p}-d)"'))
    o.append(t(89, 197, "TXT lookup", "m dimf", 10.5))
    o.append(rect(210, 30, 770, 316, "fpanel sline", 14))
    o.append(t(230, 56, "your server · agents.example.com", "", 14, None, 700))
    o.append(rect(230, 72, 238, 258, "fbg ln thick", 10))
    o.append(t(246, 94, "TLS proxy :443", "", 13, None, 700))
    o.append(t(246, 110, "nginx or Caddy, your cert", "dimf", 11))
    routes = ["GET  /.well-known/agent-keys.json", "GET  /relay/v1/health", "WS   /relay/v1/ws",
              "POST /relay/v1/enrollment/*"]
    for i, s in enumerate(routes):
        o.append(t(246, 138 + i * 19, s, "m", 10.5))
    o.append(t(246, 246, "never: /relay/v1/metrics", "m warnf", 10.5, None, 700))
    o.append(t(246, 272, "--print nginx | caddy", "m dimf", 10.5))
    o.append(t(246, 290, "writes the config for you", "dimf", 11))
    o.append(line(146, 132, 228, 132, "ln thick", f'marker-end="url(#{p}-a)"'))
    o.append(t(177, 124, "wss", "m dimf", 10, "middle"))
    o.append(t(177, 148, "HTTPS", "m dimf", 10, "middle"))
    o.append(line(468, 186, 538, 186, "ln med", f'marker-end="url(#{p}-a)"'))
    o.append(t(503, 178, "127.0.0.1", "m dimf", 10.5, "middle"))
    o.append(t(503, 202, ":9078", "m dimf", 10.5, "middle"))
    o.append(rect(540, 72, 260, 258, "fbg ln thick", 10))
    o.append(t(556, 94, "kollab relay serve", "m", 12.5, None, 700))
    o.append(t(556, 110, "--domain agents.example.com", "m dimf", 10.5))
    for i, (a, b) in enumerate((("relay worker", "one, in memory: rooms, mailbox"),
                                ("publisher", "re-signs the key file every 60 s"),
                                ("key file", "served on the same port"))):
        y = 124 + i * 66
        o.append(rect(556, y, 228, 54, "fpanel sline", 8))
        o.append(t(570, y + 22, a, "", 12.5, None, 700))
        o.append(t(570, y + 40, b, "dimf", 11))
    o.append(rect(820, 72, 146, 128, "fbg ln", 10))
    o.append(t(834, 94, "state directory", "", 12, None, 700))
    for i, s in enumerate(("~/.kollab/relay/", "agents.example.com/", "service.key  0600", "publisher.json")):
        o.append(t(834, 114 + i * 16, s, "m dimf", 10))
    o.append(t(834, 188, "back this up", "warnf", 11.5, None, 700))
    o.append(line(800, 136, 818, 136, "ln", f'marker-end="url(#{p}-a)"'))
    o.append(rect(820, 214, 146, 116, "fbg ln", 10))
    o.append(t(834, 236, "systemd unit", "", 12, None, 700))
    o.append(t(834, 256, "--install", "m youf", 10.5, None, 700))
    o.append(t(834, 276, "writes, enables and", "dimf", 11))
    o.append(t(834, 292, "starts it (sudo)", "dimf", 11))
    o.append(t(834, 312, "or --print systemd", "m dimf", 10.5))
    o.append(line(818, 270, 802, 270, "ln", f'marker-end="url(#{p}-a)"'))
    return svg(1000, 356,
               "One-command directory: devices reach your TLS proxy on 443, which forwards five routes to "
               "kollab relay serve on 127.0.0.1:9078; one process runs the relay, the publisher and the key "
               "file; the state directory holds the signing key; you add a DNS TXT record, and --install "
               "keeps it running as a systemd service.",
               "".join(o), 760)


# ---------------------------------------------------------------- figure 5b
def fig_kollaborai():
    p = "f6"
    o = [markers(p)]
    o.append(rect(20, 112, 130, 72, "fbg ln", 10))
    o.append(t(85, 136, "devices", "", 13, "middle", 700))
    o.append(t(85, 154, "/connect", "m dimf", 11, "middle"))
    o.append(t(85, 170, "kollabor.ai", "m dimf", 11, "middle"))
    o.append(rect(212, 40, 236, 236, "fpanel sline", 14))
    o.append(t(230, 66, "edge server", "", 14, None, 700))
    o.append(t(230, 84, "public, ports 80 and 443", "dimf", 11))
    o.append(rect(230, 100, 200, 156, "fbg ln thick", 10))
    o.append(t(246, 122, "nginx · TLS :443", "", 12.5, None, 700))
    for i, s in enumerate(("key file · health", "ws · enrollment/*", "contact/*")):
        o.append(t(246, 146 + i * 18, s, "m", 10.5))
    o.append(t(246, 212, "never: metrics", "m warnf", 10.5, None, 700))
    o.append(t(246, 236, "same five routes", "dimf", 11))
    o.append(line(150, 148, 228, 148, "ln thick", f'marker-end="url(#{p}-a)"'))
    o.append(t(189, 140, "wss, HTTPS", "m dimf", 10, "middle"))
    o.append(line(430, 178, 528, 178, "ln thick", f'marker-end="url(#{p}-a)"'))
    o.append(t(479, 170, "private link", "dimf", 11, "middle"))
    o.append(t(479, 196, "WireGuard", "m dimf", 10.5, "middle"))
    o.append(rect(530, 40, 450, 236, "fpanel sline", 14))
    o.append(t(548, 66, "relay host", "", 14, None, 700))
    o.append(t(548, 84, "systemd services", "dimf", 11))
    o.append(rect(548, 100, 236, 156, "fbg ln thick", 10))
    o.append(t(562, 122, "kollab relay run", "m", 12, None, 700))
    o.append(t(562, 138, "--config runtime.json", "m dimf", 10.5))
    o.append(rect(562, 150, 100, 30, "fpanel sline", 6))
    o.append(t(612, 170, "worker 1", "m", 11, "middle"))
    o.append(rect(670, 150, 100, 30, "fpanel sline", 6))
    o.append(t(720, 170, "worker 2…", "m", 11, "middle"))
    o.append(line(612, 180, 612, 204, "ln"))
    o.append(line(720, 180, 720, 204, "ln"))
    o.append(rect(562, 204, 208, 38, "fpanel sline", 6))
    o.append(t(666, 222, "shared backend", "", 11.5, "middle", 700))
    o.append(t(666, 236, "Valkey / Redis: leases, inboxes", "dimf", 10, "middle"))
    o.append(rect(800, 100, 164, 72, "fbg ln", 10))
    o.append(t(814, 122, "discovery publisher", "", 11.5, None, 700))
    o.append(t(814, 140, "--watch · every 60 s", "m dimf", 10))
    o.append(t(814, 158, "signs the key file", "dimf", 10.5))
    o.append(rect(800, 184, 164, 72, "fbg ln", 10))
    o.append(t(814, 206, "health :9080", "m", 11, None, 700))
    o.append(t(814, 224, "private; publisher only", "dimf", 10.5))
    o.append(t(814, 240, "advertises a ready relay", "dimf", 10.5))
    return svg(1000, 292,
               "kollabor.ai's form: devices reach an edge server's nginx on 443; a private WireGuard link "
               "carries traffic to a relay host running kollab relay run with several workers on a shared "
               "Valkey or Redis backend, plus a separate discovery publisher.",
               "".join(o), 760)


# ---------------------------------------------------------------- standalone files
# Kollab's TUI theme, light and dark. An SVG shown through <img> loads no web
# fonts, so the files use system fonts with the same metrics class.
THEMES = {
    "light": {"bg": "#F6F8FB", "panel": "#EAEFF5", "line": "#CDD4DD", "fg": "#191E26", "dim": "#5C6674",
              "you": "#C65220", "seal": "#1A703A", "agent": "#304EA8", "warn": "#94581A"},
    "dark": {"bg": "#121416", "panel": "#191C20", "line": "#2C323A", "fg": "#ECF0F4", "dim": "#9199A4",
             "you": "#F86C2B", "seal": "#60BE78", "agent": "#91AAFF", "warn": "#E0983E"},
}
FONTS = ("--body:-apple-system,BlinkMacSystemFont,'Segoe UI','Helvetica Neue',Helvetica,Arial,sans-serif;"
         "--mono:ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace;"
         "--display:var(--mono)")
SVG_RULES = """text { fill: var(--fg); font-family: var(--body); }
.m { font-family: var(--mono); }
.d { font-family: var(--display); letter-spacing: .08em; }
.dimf { fill: var(--dim); }
.youf { fill: var(--you); }
.sealf { fill: var(--seal); }
.agentf { fill: var(--agent); }
.warnf { fill: var(--warn); }
.numf { fill: var(--fg); }
.fpanel { fill: var(--panel); }
.fbg { fill: var(--bg); }
.fseal { fill: var(--seal); }
.ln { stroke: var(--fg); stroke-width: 1.3; }
.sline { stroke: var(--line); stroke-width: 1.2; }
.lnline { stroke: var(--line); stroke-width: 1.5; }
.lndim { stroke: var(--dim); stroke-width: 1.3; }
.lnyou { stroke: var(--you); stroke-width: 1.4; }
.lnseal { stroke: var(--seal); stroke-width: 1.4; }
.lnagent { stroke: var(--agent); stroke-width: 1.4; }
.lnwarn { stroke: var(--warn); stroke-width: 1.4; }
.med { stroke-width: 1.7; }
.thick { stroke-width: 2.2; }
.dash { stroke-dasharray: 7 6; }
.mk { fill: var(--fg); }
.mk-seal { fill: var(--seal); }
.mk-agent { fill: var(--agent); }
.mk-dim { fill: var(--dim); }
.mk-warn { fill: var(--warn); }
"""
FIGURES = {
    "topology": fig_topology,
    "join": fig_sequence,
    "layers": fig_layers,
    "lifecycle": fig_lifecycle,
    "discovery": fig_discovery,
    "selfhost": fig_selfhost,
    "kollaborai": fig_kollaborai,
}


def standalone(figure: str, theme: str) -> str:
    """An inline figure as a file: namespaced, sized, with its own colors, fonts and background."""
    head = re.match(r'<svg viewBox="0 0 (\d+) (\d+)" role="img" aria-label="([^"]*)" style="[^"]*">', figure)
    width, height, label = head.groups()
    colors = THEMES[theme]
    tokens = ";".join(f"--{name}:{value}" for name, value in colors.items())
    rules = " ".join(SVG_RULES.split())
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" role="img" aria-label="{label}"><title>{label}</title>'
        f"<style>svg{{{tokens};{FONTS}}} {rules}</style>"
        f'<rect width="{width}" height="{height}" rx="14" fill="{colors["bg"]}"/>'
        + figure[head.end():]
        + "\n"
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, build in FIGURES.items():
        figure = build()
        for theme in THEMES:
            (OUT_DIR / f"{name}-{theme}.svg").write_text(standalone(figure, theme))
    print(f"wrote {len(FIGURES) * len(THEMES)} files to {OUT_DIR}")


if __name__ == "__main__":
    main()
