/**
 * Messages other agents sent over the hub. The daemon hands them to the model
 * inside the `<agent_hud>` block it puts at the top of a user turn
 * (plugins/hub/plugin.py, kollabor/llm/agent_hud.py):
 *
 *   <agent_hud>
 *   [hub:aquamarine->lapis]
 *   + [hub channel: aquamarine -> lapis]
 *     Yes, I can see your message.
 *
 *     [hub wake instruction]
 *     classification: ...
 *   </agent_hud>
 *
 * The chat shows each one as a message from that gem, the way the terminal
 * draws its hub boxes. The rest of the block (vault notes, pending replies, the
 * wake instruction) is model context and stays out of the chat.
 */
export type HubNote = {
  from: string;
  /** Empty for a broadcast (the hub's `*` target). */
  to: string;
  text: string;
  /** Sent to another agent; this one only saw it go by. */
  observed: boolean;
};

const AGENT_HUD = /^\s*<agent_hud>\n?([\s\S]*?)\n?<\/agent_hud>\s*/;
const SECTION = /^\[([^:\]\s]+):([^\]]*)\]$/;
// What the hub appends to a message for the model, never part of the message.
const TRAILERS = [
  "\n[relay event context:",
  "\n(the human is typing in ",
  "\n(this message was sent to ",
  "\n\n[hub wake instruction]",
];

function hubNote(label: string, lines: string[]): HubNote | null {
  const arrow = label.indexOf("->");
  if (arrow < 0) return null; // "pending_replies" and other status entries
  // Entry bodies are indented: "+ " on the first line, two spaces after.
  let body = lines
    .map((line) => (line === "+" ? "" : /^(\+ | {2})/.test(line) ? line.slice(2) : line))
    .join("\n")
    .trim();
  if (body.startsWith("[hub channel:")) {
    const newline = body.indexOf("\n");
    body = newline < 0 ? "" : body.slice(newline + 1);
  }
  const observed = body.includes("\n(this message was sent to ") || body.startsWith("(this message was sent to ");
  const cuts = TRAILERS.map((marker) => body.indexOf(marker)).filter((index) => index >= 0);
  if (cuts.length) body = body.slice(0, Math.min(...cuts));
  const text = body.trim();
  if (!text) return null;
  return { from: label.slice(0, arrow), to: label.slice(arrow + 2), text, observed };
}

/** The hub messages in a user turn's agent HUD, and the text after the HUD. */
export function splitAgentHud(text: string): { notes: HubNote[]; rest: string } {
  const match = AGENT_HUD.exec(text);
  if (!match) return { notes: [], rest: text };
  const notes: HubNote[] = [];
  let section: { name: string; label: string; lines: string[] } | null = null;
  const flush = () => {
    const note = section?.name === "hub" ? hubNote(section.label, section.lines) : null;
    if (note) notes.push(note);
  };
  for (const line of match[1].split("\n")) {
    const header = SECTION.exec(line);
    if (header) {
      flush();
      section = { name: header[1], label: header[2], lines: [] };
    } else {
      section?.lines.push(line);
    }
  }
  flush();
  return { notes, rest: text.slice(match[0].length) };
}
