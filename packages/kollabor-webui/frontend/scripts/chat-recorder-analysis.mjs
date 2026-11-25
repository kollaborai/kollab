export const CHAT_RECORDER_FLOWS = Object.freeze([
  "plain",
  "tools",
  "busy",
  "cut",
  "cuterr",
  "cutwatch",
  "other",
  "watch",
  "watchsend",
  "read",
  "three",
  "reload",
  "prompt-other",
  "compact",
  "goal-reload",
  "hub-note",
]);

export const CHAT_RECORDER_RUNTIMES = Object.freeze([
  "native",
  "codex-cli",
  "claude-code",
]);

/** Build the path suffix for the only session stream a recorder flow owns. */
export function chatRecorderStreamSuffix(session) {
  return `/sessions/${encodeURIComponent(session)}/stream`;
}

/** Reject permission state that cannot be restored exactly by prompt-other. */
export function requireRecorderApprovalMode(mode) {
  if (!["confirm_all", "default", "auto_approve_edits", "trust_all"].includes(mode)) {
    throw new Error("the engine does not know this agent's approval mode; open the agent and retry");
  }
  return mode;
}

/** Parse --runtime-matrix runtime=identity,... into its fixed runtime order. */
export function parseRuntimeMatrix(value) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error("expected native, codex-cli, and claude-code agent identities");
  }

  const entries = {};
  for (const raw of value.split(",")) {
    const entry = raw.trim();
    const equals = entry.indexOf("=");
    if (equals < 1) throw new Error(`invalid runtime entry ${JSON.stringify(entry)}`);

    const runtime = entry.slice(0, equals).trim();
    const identity = entry.slice(equals + 1).trim();
    if (!CHAT_RECORDER_RUNTIMES.includes(runtime)) {
      throw new Error(`unknown runtime ${JSON.stringify(runtime)}`);
    }
    if (Object.hasOwn(entries, runtime)) {
      throw new Error(`runtime ${JSON.stringify(runtime)} appears more than once`);
    }
    if (!identity) throw new Error(`runtime ${JSON.stringify(runtime)} needs an agent identity`);
    entries[runtime] = identity;
  }

  const missing = CHAT_RECORDER_RUNTIMES.filter((runtime) => !entries[runtime]);
  if (missing.length) throw new Error(`missing runtime entries: ${missing.join(", ")}`);

  return Object.fromEntries(CHAT_RECORDER_RUNTIMES.map((runtime) => [runtime, entries[runtime]]));
}

/** Identify the configured runtime from an engine session representation. */
export function classifyChatRuntime(session) {
  if (String(session?.provider ?? "").toLowerCase() !== "acp") return "native";
  const harness = `${session.harness ?? ""} ${session.profile ?? ""}`.toLowerCase();
  if (harness.includes("codex")) return "codex-cli";
  if (harness.includes("claude")) return "claude-code";
  return "acp";
}

/** Check the compaction state or its final native transcript replacement. */
export function isCompactionComplete({
  sawStart,
  status,
  latestEventStatus,
  replaced,
  runtime,
  phase = "staged",
}) {
  if (!sawStart) return false;
  if (phase === "transcript") return runtime === "native" && replaced;
  if (runtime !== "native") return status === "idle" || latestEventStatus === "idle";
  const done = ["ready_to_retry", "applied"];
  return done.includes(status) || done.includes(latestEventStatus) ||
    (replaced && status !== "compacting");
}

/** Check that a reload snapshot still contains the same live turn and inputs. */
export function hasRunningTurnWithInputs(snapshot, expectedTurn) {
  const expectedIds = expectedTurn?.input_ids;
  if (!Array.isArray(snapshot?.turns) || !Array.isArray(snapshot?.inputs) ||
      !expectedTurn?.turn_id || !Array.isArray(expectedIds) || expectedIds.length === 0) return false;
  const turn = snapshot.turns.find((item) => item.turn_id === expectedTurn.turn_id && item.state === "running");
  if (!turn || !Array.isArray(turn.input_ids)) return false;
  return expectedIds.every((id) =>
    turn.input_ids.includes(id) && snapshot.inputs.some((input) =>
      input.id === id && input.turn_id === expectedTurn.turn_id &&
      (input.state === "started" || input.state === "joined")));
}

/** Find a post-action upsert that changes the status of the same prompt row. */
export function findPromptStatusUpdate(entries, promptId, initialStatus, afterIndex = 0) {
  if (!Array.isArray(entries) || !promptId || typeof initialStatus !== "string" ||
      !Number.isInteger(afterIndex) || afterIndex < -1) return null;
  return entries.find((entry, index) => index > afterIndex &&
    entry.kind === "stream" && entry.ev === "row.upsert" && entry.row === promptId &&
    typeof entry.status === "string" && entry.status !== initialStatus) ?? null;
}

/** Count the recorder's frame-level defects without accessing a browser. */
export function analyzeChatFrames(frames, marks) {
  const at = (name) => marks.find((mark) => mark.name === name)?.t;
  const readyAt = at("READY") ?? 0;
  const reloading = (time) =>
    at("RELOAD") !== undefined && time >= at("RELOAD") && time <= (at("RELOADED") ?? Infinity) + 3000;
  const baseline = frames.find((frame) => frame.t >= readyAt) ?? frames[0];
  const events = [];
  const defects = {
    blank: 0,
    remount: 0,
    massFade: 0,
    dupUser: 0,
    dupReply: 0,
    dupTools: 0,
    replyGone: 0,
    picker: 0,
    anchorMoved: 0,
    anchorLost: 0,
    errorBoxLeft: 0,
  };
  const flag = (time, name, note, counter) => {
    events.push({ t: time, name, note });
    if (counter) defects[counter] += 1;
  };
  const ids = (frame) => new Set(frame.rows.map((row) => /^[a-z](\d+):/.exec(row)?.[1]));
  const over = (counts, base) => counts.some((count, index) => count - (base?.[index] ?? 0) > 1);
  const on = { dupUser: false, dupReply: false, dupTools: false, picker: false, err: false, fade: false };
  const seen = baseline ? baseline.replies.map(() => false) : [];
  const gone = seen.map(() => false);
  let previous = null;
  let anchor = null;
  let remountEpisode = false;

  for (const frame of frames) {
    if (frame.t < readyAt || reloading(frame.t)) {
      previous = frame;
      continue;
    }
    if (previous && !reloading(previous.t)) {
      if (frame.n === 0 && previous.n > 0) {
        flag(frame.t, "BLANK", `rows ${previous.n} -> 0`, "blank");
      }
      if (previous.n >= 3 && frame.n >= 3) {
        const remounted = ![...ids(frame)].some((id) => ids(previous).has(id));
        if (remounted && !remountEpisode) {
          flag(frame.t, "REMOUNT-ALL", `every row is a new element (${previous.n} -> ${frame.n} rows)`, "remount");
        }
        remountEpisode = remounted;
      } else {
        remountEpisode = false;
      }
    }

    const fade = frame.fading >= 3;
    if (fade && !on.fade) flag(frame.t, "MASS-FADE", `${frame.fading} rows fading in at once`, "massFade");
    on.fade = fade;

    const dupUser = over(frame.users, baseline?.users);
    if (dupUser && !on.dupUser) flag(frame.t, "DUP-USER", `bubbles per message: ${frame.users.join("/")}`, "dupUser");
    if (!dupUser && on.dupUser) flag(frame.t, "DUP-USER-END", "");
    on.dupUser = dupUser;

    const dupReply = over(frame.replies, baseline?.replies);
    if (dupReply && !on.dupReply) flag(frame.t, "DUP-REPLY", `copies per reply: ${frame.replies.join("/")}`, "dupReply");
    if (!dupReply && on.dupReply) flag(frame.t, "DUP-REPLY-END", "");
    on.dupReply = dupReply;

    const dupTools = frame.toolsDrawn > frame.toolsStarted;
    if (dupTools && !on.dupTools) {
      flag(frame.t, "DUP-TOOLS", `${frame.toolsDrawn} tool calls drawn, ${frame.toolsStarted} started`, "dupTools");
    }
    if (!dupTools && on.dupTools) flag(frame.t, "DUP-TOOLS-END", "");
    on.dupTools = dupTools;

    frame.replies.forEach((count, index) => {
      const shows = count - (baseline?.replies?.[index] ?? 0) > 0;
      if (shows && gone[index]) {
        gone[index] = false;
        flag(frame.t, "REPLY-BACK", `reply ${index + 1}`);
      }
      if (shows) seen[index] = true;
      else if (seen[index] && !gone[index]) {
        gone[index] = true;
        flag(frame.t, "REPLY-GONE", `reply ${index + 1} left the screen`, "replyGone");
      }
    });

    const picker = frame.pick > (baseline?.pick ?? 0);
    if (picker && !on.picker) flag(frame.t, "PICKER", `${frame.pick} "N / M" switcher(s)`, "picker");
    on.picker = picker;

    const errorBox = frame.err > (baseline?.err ?? 0);
    if (errorBox && !on.err) flag(frame.t, "ERROR-BOX", `"${frame.errText}"`);
    if (!errorBox && on.err) flag(frame.t, "ERROR-BOX-END", "");
    on.err = errorBox;

    if ("anc" in frame) {
      if (frame.anc === null) {
        if (anchor !== "lost") flag(frame.t, "ANCHOR-LOST", "the row being read was rebuilt", "anchorLost");
        anchor = "lost";
      } else if (anchor === null) {
        anchor = frame.anc;
      } else if (anchor !== "lost" && Math.abs(frame.anc - anchor) > 3) {
        flag(frame.t, "ANCHOR-MOVED", `the row being read moved ${frame.anc - anchor} px`, "anchorMoved");
        anchor = frame.anc;
      }
    }
    previous = frame;
  }

  if (on.err) defects.errorBoxLeft = 1;
  return { events, defects };
}
