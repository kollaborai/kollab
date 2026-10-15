/**
 * Messages other agents sent over the hub. The daemon hands them to the model
 * inside the `<agent_hud>` block it puts at the top of a user turn.
 */
export type HubNote = {
  from: string;
  /** Empty for a broadcast (the hub's `*` target). */
  to: string;
  text: string;
  /** Sent to another agent; this one only saw it go by. */
  observed: boolean;
  /** Optional routing metadata, retained for presentation only. */
  threadId?: string;
  replyTo?: string;
};

export type HubMessageHeader = {
  sender: string;
  threadId?: string;
  replyTo?: string;
};

/** Parse the stable human-readable hub prefix without mutating message text. */
export function parseHubMessageHeader(text: string): HubMessageHeader | null {
  const match = /^Message from\s+([^\s\[]+)(?:\s+\[thread:([^\]]+)\])?(?:\s+\[reply-to:([^\]]+)\])?\s*$/i.exec(text.trim());
  if (!match) return null;
  return {
    sender: match[1],
    ...(match[2] ? { threadId: match[2] } : {}),
    ...(match[3] ? { replyTo: match[3] } : {}),
  };
}

const AGENT_HUD = /^\s*<agent_hud>\n?([\s\S]*?)\n?<\/agent_hud>\s*/;
const SECTION = /^\[([^:\]\s]+):(.+)\]$/;
// What the hub appends to a message for the model, never part of the message.
const TRAILERS = [
  "\n[relay event context:",
  "\n(the human is typing in ",
  "\n(this message was sent to ",
  "\n\n[hub wake instruction]",
  "\n\n[hub thread instruction]",
  "\n[hub thread instruction]",
  "\n\n[hub reply instruction]",
  "\n[hub reply instruction]",
];

function parseRoute(label: string): { from: string; to: string; threadId?: string; replyTo?: string } {
  const arrow = label.indexOf("->");
  if (arrow < 0) return { from: label, to: "" };
  const from = label.slice(0, arrow).trim();
  let to = label.slice(arrow + 2).trim();
  let threadId: string | undefined;
  let replyTo: string | undefined;
  const thread = /\s*\[thread:([^\]]+)\]/i.exec(to);
  if (thread) { threadId = thread[1]; to = to.replace(thread[0], "").trim(); }
  const reply = /\s*\[reply-to:([^\]]+)\]/i.exec(to);
  if (reply) { replyTo = reply[1]; to = to.replace(reply[0], "").trim(); }
  return { from, to, ...(threadId ? { threadId } : {}), ...(replyTo ? { replyTo } : {}) };
}

function hubNote(label: string, lines: string[]): HubNote | null {
  const route = parseRoute(label);
  if (!label.includes("->")) return null;
  let body = lines
    .map((line) => (line === "+" ? "" : /^(\+ | {2})/.test(line) ? line.slice(2) : line))
    .join("\n")
    .trim();
  if (body.startsWith("[hub channel:")) {
    const newline = body.indexOf("\n");
    const channel = newline < 0 ? body : body.slice(0, newline);
    const thread = /\[thread:([^\]]+)\]/i.exec(channel);
    const reply = /\[reply-to:([^\]]+)\]/i.exec(channel);
    if (thread) route.threadId = thread[1];
    if (reply) route.replyTo = reply[1];
    body = newline < 0 ? "" : body.slice(newline + 1);
  }
  const observed = body.includes("\n(this message was sent to ") || body.startsWith("(this message was sent to ");
  const cuts = TRAILERS.map((marker) => body.indexOf(marker)).filter((index) => index >= 0);
  if (cuts.length) body = body.slice(0, Math.min(...cuts));
  const text = body.trim();
  if (!text) return null;
  return { ...route, text, observed };
}

/** The hub messages in a user turn's agent HUD, and the text after the HUD. */
export function splitAgentHud(text: string): { notes: HubNote[]; rest: string } {
  const match = AGENT_HUD.exec(text);
  if (!match) {
    const newline = text.indexOf("\n");
    const header = parseHubMessageHeader(newline < 0 ? text : text.slice(0, newline));
    if (!header) return { notes: [], rest: text };
    const body = newline < 0 ? "" : text.slice(newline + 1).trim();
    return body ? { notes: [{ from: header.sender, to: "", text: body, observed: false, ...(header.threadId ? { threadId: header.threadId } : {}), ...(header.replyTo ? { replyTo: header.replyTo } : {}) }], rest: "" } : { notes: [], rest: text };
  }
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
