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
  /** Raw text before presentation cleaning, preserved for model/history. */
  rawText?: string;
  /** Whether this is a direct message to this agent (not an HUD observation). */
  isDirect?: boolean;
  /** Whether this note is an online notice (agent coming online). */
  isOnlineNotice?: boolean;
};

export type HubMessageHeader = {
  sender: string;
  threadId?: string;
  replyTo?: string;
};

/** Saved ACP observation batches predate the HUD envelope. Parse only when
 * their persisted source identifies them, never for user-authored examples. */
export function splitObservationReplies(text: string): { notes: HubNote[]; rest: string } {
  const header = /^Reply from ([^\s\[]+) \[thread:([^\]\r\n]*)\]\r?\n/gm;
  const matches = [...text.matchAll(header)];
  if (!matches.length || matches[0].index !== 0) return { notes: [], rest: text };
  const notes = matches.map((match, index) => {
    const rawText = text.slice(match.index, matches[index + 1]?.index ?? text.length).trim();
    const body = text.slice(match.index + match[0].length, matches[index + 1]?.index ?? text.length).trim();
    return {
      from: match[1], to: "", text: stripReplyInstructions(body), observed: true,
      ...(match[2] ? { threadId: match[2] } : {}), rawText,
    };
  });
  return { notes, rest: "" };
}

export type RosterEntry = {
  name?: string;
  identity?: string;
  device?: string;
  agent_id?: string;
  id?: string;
  device_id?: string;
  handle?: string;
  [key: string]: unknown;
};

export type RosterLookup =
  | readonly RosterEntry[]
  | {
      agents?: readonly RosterEntry[];
      remoteAgents?: readonly RosterEntry[];
      roster?: readonly RosterEntry[];
      [key: string]: unknown;
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

/** Resolve device IDs or agent IDs like d01769a1ed99 to agent@device from roster data. */
export function resolveAgentSender(
  sender: string,
  roster?: RosterLookup,
  contextText?: string,
): string {
  const trimmed = sender.trim();
  if (!trimmed) return trimmed;
  if (trimmed.includes("@")) return trimmed;

  let entries: readonly RosterEntry[] = [];
  if (Array.isArray(roster)) {
    entries = roster;
  } else if (roster && typeof roster === "object") {
    const grouped = roster as Exclude<RosterLookup, readonly RosterEntry[]>;
    entries = [
      ...(Array.isArray(grouped.agents) ? grouped.agents : []),
      ...(Array.isArray(grouped.remoteAgents) ? grouped.remoteAgents : []),
      ...(Array.isArray(grouped.roster) ? grouped.roster : []),
    ];
  }

  const match = entries.find((e) => {
    return (
      e.agent_id === trimmed ||
      e.id === trimmed ||
      e.device_id === trimmed ||
      e.device === trimmed ||
      e.handle === trimmed ||
      e.name === trimmed ||
      e.identity === trimmed
    );
  });

  if (match) {
    const name = match.name || match.identity;
    const device = match.device;
    if (name && device) {
      return `${name}@${device}`;
    }
    if (match.handle) {
      return match.handle;
    }
    if (name) {
      return name;
    }
  }

  // If not found in roster, check contextText for online notices
  if (contextText) {
    const onlineMatch = contextText.match(/agent\s+['"]?([^'"]+)['"]?\s+just came online/i);
    if (onlineMatch?.[1]) {
      const agentName = onlineMatch[1];
      if (/^[0-9a-f]{8,}$/i.test(trimmed)) {
        return `${agentName}@${trimmed}`;
      }
      return agentName;
    }
  }

  return trimmed;
}

/** Strip trailing hub_msg / hub_reply instructions from prompt text for UI presentation. */
export function stripReplyInstructions(text: string): string {
  return text
    .replace(/(?:\r?\n)*\s*respond back using:\s*<hub_(?:msg|reply)[^>]*>[\s\S]*?(?:<\/hub_(?:msg|reply)>|$)\s*$/i, "")
    .replace(/(?:\r?\n)*\s*respond back using:\s*.*$/i, "")
    .trim();
}

/** Detect if a message is an online notice from an agent coming online. */
export function isOnlineNoticeText(text: string): boolean {
  return /^agent\s+['"]?([^'"]+)['"]?\s+just came online/i.test(text.trim());
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
export function splitAgentHud(text: string, roster?: RosterLookup): { notes: HubNote[]; rest: string } {
  const match = AGENT_HUD.exec(text);
  if (!match) {
    const newline = text.indexOf("\n");
    const header = parseHubMessageHeader(newline < 0 ? text : text.slice(0, newline));
    if (!header) return { notes: [], rest: text };
    let body = newline < 0 ? "" : text.slice(newline + 1).trim();
    if (!body) return { notes: [], rest: text };
    const rawBody = body;
    const cuts = TRAILERS.map((marker) => body.indexOf(marker)).filter((index) => index >= 0);
    if (cuts.length) body = body.slice(0, Math.min(...cuts)).trim();
    body = stripReplyInstructions(body);
    const isOnline = isOnlineNoticeText(body);
    const from = resolveAgentSender(header.sender, roster, rawBody);
    return {
      notes: [
        {
          from,
          to: "",
          text: body,
          observed: false,
          ...(header.threadId ? { threadId: header.threadId } : {}),
          ...(header.replyTo ? { replyTo: header.replyTo } : {}),
          rawText: text,
          isDirect: true,
          isOnlineNotice: isOnline,
        },
      ],
      rest: "",
    };
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

  let rest = text.slice(match[0].length);
  const restNewline = rest.indexOf("\n");
  const restHeader = parseHubMessageHeader(restNewline < 0 ? rest : rest.slice(0, restNewline));
  if (restHeader) {
    let restBody = restNewline < 0 ? "" : rest.slice(restNewline + 1).trim();
    const rawRestBody = restBody;
    const cuts = TRAILERS.map((marker) => restBody.indexOf(marker)).filter((index) => index >= 0);
    if (cuts.length) restBody = restBody.slice(0, Math.min(...cuts)).trim();
    restBody = stripReplyInstructions(restBody);
    const isOnline = isOnlineNoticeText(restBody);
    const from = resolveAgentSender(restHeader.sender, roster, rawRestBody);
    notes.push({
      from,
      to: "",
      text: restBody,
      observed: false,
      ...(restHeader.threadId ? { threadId: restHeader.threadId } : {}),
      ...(restHeader.replyTo ? { replyTo: restHeader.replyTo } : {}),
      rawText: rest,
      isDirect: true,
      isOnlineNotice: isOnline,
    });
    rest = "";
  }
  return { notes, rest };
}
