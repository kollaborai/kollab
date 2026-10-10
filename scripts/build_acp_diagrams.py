# ruff: noqa: E501
"""Diagrams for docs/specs/acp-bridge.md.

    python scripts/build_acp_diagrams.py

writes docs/diagrams/acp-bridge/<figure>-light.svg and -dark.svg in the look of
the agent-network figures, whose helpers and themes it borrows. Every label
states the spec: change the spec, change the label here, run this again.
"""

from pathlib import Path

import build_network_diagrams as nd
from build_network_diagrams import line, path, rect, svg, t

OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "diagrams" / "acp-bridge"
W = 1000

# (line class, marker, extra attributes) for each kind of link
ACP = ("lnagent thick", "g", "")
HUB = ("ln med", "a", "")
NET = ("lnseal thick", "s", "")
MCP = ("lndim med", "d", 'stroke-dasharray="6 5"')
YOU = ("lnyou med", "y", "")


def markers(p):
    you = (f'<marker id="{p}-y" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="10" markerHeight="10" '
           f'markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,1 L9,5 L0,9 z" class="mk-you"/></marker>')
    return nd.markers(p).replace("</defs>", you + "</defs>")


def arrow(o, p, style, x1, y1, x2, y2, both=False):
    cls, mk, extra = style
    ends = f'marker-end="url(#{p}-{mk})"' + (f' marker-start="url(#{p}-{mk})"' if both else "")
    o.append(line(x1, y1, x2, y2, cls, f"{ends} {extra}".strip()))


def legend(o, p, items, y=26):
    x = 20
    for style, label in items:
        arrow(o, p, style, x, y - 4, x + 34, y - 4)
        o.append(t(x + 44, y, label, "m dimf", 11))
        x += 44 + len(label) * 6.7 + 34


def panel(o, x, y, w, h, title, sub):
    o.append(rect(x, y, w, h, "fpanel sline", 14))
    o.append(t(x + 16, y + 28, title, "", 15, None, 700))
    o.append(t(x + 16, y + 46, sub, "m dimf", 11))


def box(o, x, y, w, h, title, sub="", cls="fbg ln", mono=False, sub_mono=False):
    o.append(rect(x, y, w, h, cls, 8))
    ty = y + h / 2 - 3 if sub else y + h / 2 + 5
    o.append(t(x + 16, ty, title, "m" if mono else "", 12.5 if mono else 13, None, 700))
    if sub:
        o.append(t(x + 16, ty + 17, sub, "m dimf" if sub_mono else "dimf", 11 if sub_mono else 11.5))


# ---------------------------------------------------------------- figure 1
def fig_overview():
    p = "a1"
    o = [markers(p)]
    legend(o, p, [(ACP, "ACP · JSON-RPC over stdio"), (HUB, "kollab hub socket"), (NET, "kollab network · sealed")])
    panel(o, 20, 52, 200, 330, "ACP clients", "editors, other harnesses")
    panel(o, 260, 52, 470, 330, "kollab, on your laptop", "every agent in the hub is a member")
    panel(o, 770, 52, 210, 330, "harness processes", "one per agent, own login")
    # inbound: ACP clients drive a kollab agent
    for i, (name, sub) in enumerate((("Zed", "editor"), ("JetBrains", "IDEs"), ("Buzz", "agent harness"))):
        y = 118 + i * 54
        box(o, 36, y, 168, 44, name, sub)
        o.append(line(204, y + 22, 238, y + 22, "lnagent med"))
    o.append(line(238, 140, 238, 248, "lnagent med"))
    o.append(t(36, 300, "or any ACP client", "dimf", 11.5))
    for i, s in enumerate(("qualified: Zed first;", "JetBrains and Buzz", "once their runs pass")):
        o.append(t(36, 324 + i * 16, s, "dimf", 11))
    arrow(o, p, ACP, 238, 140, 273, 140)
    o.append(t(240, 128, "ACP", "m agentf", 11, "middle", 700))
    # kollab on this machine
    box(o, 275, 118, 200, 44, "kollab acp", "speaks ACP, drives lapis", mono=True)
    for i, s in enumerate(("claude, codex and gemini are", "kollab agents whose turns", "run in another harness")):
        o.append(t(500, 134 + i * 16, s, "dimf", 11.5))
    o.append(rect(276, 182, 438, 186, "fbg lnline", 10, 'stroke-dasharray="5 4"'))
    o.append(t(292, 356, "hub · this folder", "m dimf", 11))
    arrow(o, p, HUB, 375, 162, 375, 247)
    o.append(t(383, 214, "attach", "m dimf", 11))
    box(o, 290, 249, 170, 52, "lapis", "model API, any provider")
    o.append(line(495, 219, 495, 331, "ln med"))
    o.append(line(460, 275, 495, 275, "ln med"))
    o.append(t(495, 207, "hub_msg", "m", 11, "middle"))
    # outbound: each ACP-backed agent runs its own harness process
    for i, (name, sub, cmd, harness) in enumerate((
        ("claude", "brain: Claude Code", "claude-agent-acp", "Claude Code"),
        ("codex", "brain: Codex", "codex-acp", "Codex"),
        ("gemini", "brain: Gemini CLI", "gemini --acp", "Gemini CLI"),
    )):
        y = 196 + i * 56
        o.append(line(495, y + 23, 530, y + 23, "ln med"))
        box(o, 530, y, 170, 46, name, sub, cls="fbg lnagent")
        arrow(o, p, ACP, 702, y + 23, 784, y + 23, both=True)
        box(o, 786, y, 178, 46, cmd, harness, mono=True)
    o.append(t(750, 186, "ACP", "m agentf", 11, "middle", 700))
    # the network
    arrow(o, p, NET, 495, 384, 495, 412, both=True)
    o.append(rect(260, 414, 470, 46, "fpanel lnseal", 10))
    o.append(t(276, 433, "the network", "", 13, None, 700))
    o.append(t(276, 450, "every agent above is agent@laptop on your other devices", "dimf", 11.5))
    return svg(W, 474,
               "Kollab speaks ACP both ways: an ACP client such as Zed drives a kollab agent "
               "through kollab acp, and kollab runs Claude Code, Codex and Gemini CLI as hub members over "
               "ACP; the network makes every one of them reachable from your other devices.",
               "".join(o), 760)


# ---------------------------------------------------------------- figure 2
KOLLAB_OWNS = [
    ("Name and roster", "claude here, claude@laptop everywhere"),
    ("Hub messages", "prompts in; answers and hub tools out"),
    ("Network and trust", "open, agents, manual: as for any agent"),
    ("Windows", "terminal, web UI and phone attach to it"),
    ("Permission prompts", "your approval mode, in every window"),
    ("The transcript", "what you see; /resume: resumed or fresh"),
]
HARNESS_OWNS = [
    ("Model and login", "Pro/Max, ChatGPT or an API key"),
    ("Context and memory", "its history, compaction, CLAUDE.md"),
    ("Tools", "files, shell, web, its own MCP servers"),
    ("What needs a prompt", "its allow rules run without asking"),
    ("Hooks", "its hooks run; kollab's config hooks don't"),
    ("Cost", "billed to its login, not to kollab"),
]
WIRE = [
    (1, "session/new", "cwd + kollab's hub tools"),
    (1, "session/prompt", "your input and messages to claude"),
    (-1, "session/update", "text, thinking, tool cards, plan"),
    (0, "session/request_permission", "an offered option, picked in kollab"),
    (1, "session/cancel", "Esc or Stop"),
    (-1, "stop reason", "end_turn, cancelled, refusal, …"),
]


def fig_ownership():
    p = "a2"
    o = [markers(p)]
    legend(o, p, [(ACP, "ACP · stdio"), (MCP, "MCP · kollab's hub tools"), (HUB, "kollab hub socket")])
    for x, title, sub, items, foot, foot_cls in (
        (20, "kollab owns the agent", "every kollab agent has these, claude too", KOLLAB_OWNS,
         "kollab decides these", "sealf"),
        (660, "the harness owns the brain", "Claude Code, Codex, Gemini CLI, …", HARNESS_OWNS,
         "kollab sees these only through ACP", "warnf"),
    ):
        panel(o, x, 52, 320, 372, title, sub)
        for i, (head, detail) in enumerate(items):
            y = 128 + i * 44
            o.append(f'<circle cx="{x + 24}" cy="{y - 4}" r="3.5" class="fbg ln"/>')
            o.append(t(x + 36, y, head, "", 13, None, 700))
            o.append(t(x + 36, y + 17, detail, "dimf", 11.5))
        o.append(t(x + 16, 404, foot, foot_cls, 12, None, 700))
    o.append(t(500, 80, "the ACP wire", "", 15, "middle", 700))
    o.append(t(500, 98, "one session per agent", "m dimf", 11, "middle"))
    for i, (d, label, sub) in enumerate(WIRE):
        y = 136 + i * 44
        x1, x2 = (346, 654) if d >= 0 else (654, 346)
        arrow(o, p, ("lnagent med", "g", ""), x1, y, x2, y, both=d == 0)
        o.append(t(500, y - 8, label, "m", 12, "middle"))
        o.append(t(500, y + 15, sub, "dimf", 11, "middle"))
    # the way back: the harness calls kollab's hub tools
    o.append(rect(350, 440, 300, 46, "fbg lnline", 8))
    o.append(t(500, 459, "kollab mcp hub", "m", 12.5, "middle", 700))
    o.append(t(500, 476, "hub_msg · hub_ask · hub_status · hub_agents", "m dimf", 10.5, "middle"))
    o.append(path("M 820 426 V 463 H 654", MCP[0], f'marker-end="url(#{p}-d)" {MCP[2]}'))
    o.append(t(737, 456, "MCP · stdio", "m dimf", 11, "middle"))
    o.append(path("M 348 463 H 180 V 428", HUB[0], f'marker-end="url(#{p}-a)"'))
    o.append(t(264, 456, "hub socket", "m dimf", 11, "middle"))
    return svg(W, 500,
               "What kollab controls and what the harness keeps. Kollab owns the agent: its name, hub "
               "messages, network trust, windows, permission prompts and transcript. The harness owns the "
               "brain: model, login, context, tools, its own permission rules, hooks and cost. ACP carries "
               "session/new, session/prompt, session/update, session/request_permission, session/cancel "
               "and the stop reason between them, and the harness calls back through kollab mcp hub.",
               "".join(o), 760)


# ---------------------------------------------------------------- figure 3
LANES = {"you": 100, "lapis": 340, "claude": 610, "cc": 880}
STEPS = [
    ("band", "A", "lapis asks claude"),
    (YOU, "you", "lapis", "“ask claude to review my diff”", ""),
    (HUB, "lapis", "claude", 'hub_msg to="claude"', "the hub wakes claude"),
    (ACP, "claude", "cc", "session/prompt", "the message, marked from lapis"),
    ("band", "B", "claude works · you approve"),
    (ACP, "cc", "claude", "session/update", "tool cards and text, live in its chat"),
    (ACP, "cc", "claude", "session/request_permission", "Bash: pytest -q"),
    (YOU, "claude", "you", "permission prompt", "in every window attached to claude"),
    (YOU, "you", "claude", "a · approve once", ""),
    (ACP, "claude", "cc", "selected option", "the allow-once option Claude Code offered"),
    ("band", "C", "the answer comes back"),
    (ACP, "cc", "claude", "end_turn", "after its final message: the review"),
    (HUB, "claude", "lapis", "“2 issues: …”", "its final message, on lapis's thread"),
    (YOU, "lapis", "you", "“claude found 2 issues: …”", ""),
]


def fig_sequence():
    p = "a3"
    o = [markers(p)]
    y, rows, bands = 92, [], []
    for step in STEPS:
        if step[0] == "band":
            y += 10
            bands.append((y, step[1], step[2]))
            y += 52
            continue
        rows.append((y, step))
        y += 44
    legend_y = y + 4
    height = legend_y + 20
    for x, title, sub in ((LANES["you"], "You", "terminal, web UI, phone"),
                          (LANES["lapis"], "lapis", "kollab agent · model API"),
                          (LANES["claude"], "claude", "kollab agent · ACP client"),
                          (LANES["cc"], "Claude Code", "claude-agent-acp")):
        o.append(rect(x - 90, 14, 180, 54, "fpanel ln", 10))
        o.append(t(x, 37, title, "", 14, "middle", 700))
        o.append(t(x, 55, sub, "m dimf", 11, "middle"))
    for by, _, _ in bands:
        o.append(rect(0, by, W, 26, "fpanel", 0))
    for x in LANES.values():
        o.append(line(x, 68, x, legend_y - 18, "lnline"))
    # band titles sit on top of the lifelines they would otherwise cross
    for by, letter, title in bands:
        label = f"{letter}  {title}".upper()
        o.append(rect(0, by, 32 + len(label) * 8.2, 26, "fpanel", 0))
        o.append(t(16, by + 17, label, "d dimf", 11))
    for n, (cy, (style, a, b, label, sub)) in enumerate(rows, 1):
        xa, xb = LANES[a], LANES[b]
        arrow(o, p, style, xa + (6 if xb > xa else -6), cy, xb + (-8 if xb > xa else 8), cy)
        # a prompt between you and claude crosses lapis: label it beside claude
        crosses = {a, b} == {"you", "claude"}
        mid = (LANES["lapis"] + LANES["claude"]) / 2 if crosses else (xa + xb) / 2
        speech = label.startswith("“")
        o.append(t(mid, cy - 8, label, "youf" if style is YOU else ("" if speech else "m"), 12.5 if speech else 12, "middle"))
        if sub:
            o.append(t(mid, cy + 16, sub, "dimf", 11, "middle"))
        o.append(f'<circle cx="26" cy="{cy - 4}" r="10" class="fbg {style[0].split()[0]}"/>')
        o.append(t(26, cy, str(n), "m numf", 11, "middle", 700))
    o.append(rect(0, legend_y - 18, W, 2, "fpanel", 0))
    legend(o, p, [(YOU, "you"), (HUB, "kollab hub socket"), (ACP, "ACP · stdio")], legend_y + 6)
    return svg(W, height,
               "One request in 11 steps: you ask lapis to have claude review your diff; lapis sends a hub "
               "message; kollab turns it into an ACP prompt for Claude Code; Claude Code streams tool cards "
               "and asks permission to run the tests; you approve in kollab's prompt and kollab selects the "
               "allow-once option Claude Code offered; Claude Code ends its turn, kollab sends its final "
               "message to lapis on lapis's thread, and lapis tells you what claude found.",
               "".join(o), 760)


# ---------------------------------------------------------------- figure 4
def fig_network():
    p = "a4"
    o = [markers(p)]
    legend(o, p, [(NET, "kollab network · sealed"), (HUB, "kollab, inside home-server"), (ACP, "ACP · stdio, inside one machine")])
    panel(o, 20, 52, 280, 360, "laptop", "where you work")
    panel(o, 340, 52, 250, 360, "the network", "relay or direct · sealed")
    panel(o, 630, 52, 350, 360, "home-server", "kollab + Claude Code installed")
    box(o, 36, 124, 248, 50, "lapis", 'hub_msg to="claude@home-server"', sub_mono=True)
    for i, s in enumerate(("claude@home-server is a name in",
                           "the roster, like any agent@device;",
                           "the web UI here opens it when",
                           "home-server's trust allows")):
        o.append(t(36, 300 + i * 18, s, "dimf", 11.5))
    box(o, 356, 124, 218, 50, "relay", "forwards frames it can't read")
    for i, s in enumerate(("kollab's own protocol carries it:",
                           "ACP's remote transport is a draft,",
                           "so ACP never crosses the wire")):
        o.append(t(356, 300 + i * 18, s, "dimf", 11.5))
    # on home-server the trust check sits on the path, before the harness
    box(o, 646, 124, 318, 50, "trust: open · agents · manual", "may lapis@laptop reach claude?", cls="fbg lnseal")
    arrow(o, p, HUB, 805, 176, 805, 204)
    o.append(t(815, 194, "allowed", "m dimf", 11))
    box(o, 646, 206, 318, 50, "claude", "kollab agent · brain: Claude Code", cls="fbg lnagent")
    arrow(o, p, ACP, 805, 258, 805, 290, both=True)
    o.append(t(815, 278, "ACP · stdio", "m agentf", 11))
    box(o, 646, 292, 318, 50, "claude-agent-acp", "Claude Code, logged in here", mono=True)
    o.append(t(646, 370, "ACP never leaves this machine;", "agentf", 11.5, None, 700))
    o.append(t(646, 388, "prompts go to the windows its trust lets open claude", "dimf", 11.5))
    arrow(o, p, NET, 286, 149, 354, 149, both=True)
    o.append(t(320, 141, "hub_msg", "m", 11, "middle"))
    o.append(t(320, 166, "sealed", "m dimf", 11, "middle"))
    arrow(o, p, NET, 576, 149, 644, 149, both=True)
    o.append(t(610, 141, "delivered", "m", 11, "middle"))
    o.append(t(610, 166, "both ways", "m dimf", 11, "middle"))
    return svg(W, 432,
               "Across machines: lapis on the laptop sends a hub message to claude@home-server; the network "
               "carries it sealed; on home-server, trust decides first, then kollab hands the message to "
               "Claude Code over ACP on stdio. ACP never leaves home-server.",
               "".join(o), 760)


FIGURES = {
    "overview": fig_overview,
    "ownership": fig_ownership,
    "sequence": fig_sequence,
    "network": fig_network,
}


def standalone(figure: str, theme: str) -> str:
    return nd.standalone(figure, theme).replace("</style>", " .mk-you { fill: var(--you); }</style>", 1)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, build in FIGURES.items():
        figure = build()
        for theme in nd.THEMES:
            (OUT_DIR / f"{name}-{theme}.svg").write_text(standalone(figure, theme))
    print(f"wrote {len(FIGURES) * len(nd.THEMES)} files to {OUT_DIR}")


if __name__ == "__main__":
    main()
