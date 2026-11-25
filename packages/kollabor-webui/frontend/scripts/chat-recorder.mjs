// Frame-by-frame recorder for the web UI chat.
//
// Drives a real page against a running engine and a real agent, samples the
// chat on every animation frame inside the page, logs each /stream, /inputs,
// /events and /history exchange on the same clock, records a video, and writes one
// timeline per flow with the defects it saw:
//
//   BLANK         the chat had rows and showed none
//   REMOUNT-ALL   every row became a new element (the whole thread reloaded)
//   MASS-FADE     three or more rows played their entrance fade at once
//   DUP-USER      a message this flow sent shows in more than one bubble
//   DUP-REPLY     a reply's words show more than once
//   DUP-TOOLS     the chat draws more tool calls than the turn has started
//   REPLY-GONE    a reply that was on screen left it
//   PICKER        an "N / M" branch switcher shows
//   ERROR-BOX     an error box remains at the end (temporary cut errors are logged)
//   ANCHOR-MOVED  the row a reader scrolled to moved, or ANCHOR-LOST: was rebuilt
//
// Use it on any change to the chat's history sync, live turn or runtime, before
// calling the change done. Run after `npm ci`, from this folder's parent:
//
//   node scripts/chat-recorder.mjs <flow|all> --agent <identity> [options]
//   node scripts/chat-recorder.mjs <flow|all> --runtime-matrix native=<identity>,codex-cli=<identity>,claude-code=<identity> [options]
//
//   --web <url>          the web UI (default http://127.0.0.1:8080)
//   --engine <url>       the engine the page talks to (default http://127.0.0.1:7433)
//   --token-file <path>  the engine's token file (default ~/.kollab/engine.token)
//   --agent <identity>   one agent identity, as the sidebar shows it
//   --runtime-matrix ... runs each selected flow on all three named runtimes
//   --session <id>       its session id (default: the session with that identity)
//   --label <text>       makes this run's messages unique (default: the time)
//   --out <dir>          where the recordings go (default ./chat-recordings)
//   --headed             show the browser
//
// Flows: plain, tools, busy, cut, cuterr, cutwatch, other, watch, watchsend,
// read, three, reload, prompt-other, compact, goal-reload, hub-note. `all`
// runs each in turn. The agent runs real turns with its model and needs a
// terminal tool it may run without asking (`sleep`); prompt-other temporarily
// changes approval mode and restores it. hub-note needs a hub-reachable agent.
// Use a throwaway agent in a scratch folder, on a side engine when the change
// touches the engine. Exits 1 when a flow shows a defect.
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  analyzeChatFrames,
  CHAT_RECORDER_FLOWS,
  classifyChatRuntime,
  chatRecorderStreamSuffix,
  findPromptStatusUpdate,
  hasRunningTurnWithInputs,
  isCompactionComplete,
  parseRuntimeMatrix,
} from "./chat-recorder-analysis.mjs";

const require = createRequire(import.meta.url);

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const at = args.indexOf(`--${name}`);
  return at >= 0 && args[at + 1] !== undefined ? args[at + 1] : fallback;
};
const wanted = args[0] && !args[0].startsWith("--") ? args[0] : "";
const AGENT = option("agent", "");
const matrixText = option("runtime-matrix", "");
const sessionOverride = option("session", "");
let runtimeMatrix = null;
if (matrixText) {
  try {
    runtimeMatrix = parseRuntimeMatrix(matrixText);
  } catch (error) {
    console.error(`invalid --runtime-matrix: ${error.message}`);
    process.exit(2);
  }
}
const usage = `usage: node scripts/chat-recorder.mjs <${CHAT_RECORDER_FLOWS.join("|")}|all> (--agent <identity> | --runtime-matrix native=<identity>,codex-cli=<identity>,claude-code=<identity>) [--web url] [--engine url] [--token-file path] [--session id] [--label text] [--out dir] [--headed]`;
if (
  !(wanted === "all" || CHAT_RECORDER_FLOWS.includes(wanted)) ||
  (runtimeMatrix ? Boolean(AGENT || sessionOverride) : !AGENT)
) {
  console.error(usage);
  process.exit(2);
}
const WEB = option("web", "http://127.0.0.1:8080");
const ENGINE = option("engine", "http://127.0.0.1:7433").replace(/\/+$/, "");
const TOKEN = fs.readFileSync(option("token-file", path.join(os.homedir(), ".kollab", "engine.token")), "utf8").trim();
const LABEL = option("label", new Date().toISOString().slice(11, 19).replaceAll(":", ""));
const OUT = path.resolve(option("out", "chat-recordings"));
const HEADED = args.includes("--headed");
const engine = (route, init = {}) => fetch(`${ENGINE}${route}`, {
  ...init, headers: { authorization: `Bearer ${TOKEN}`, "content-type": "application/json", ...init.headers },
});

async function sessionId(agent, runtime, given = "") {
  if (given) return given;
  const listed = await (await engine("/sessions")).json();
  const sessions = Array.isArray(listed) ? listed : listed.sessions ?? [];
  const match = sessions.filter((session) => session.identity === agent);
  if (match.length !== 1) {
    const hint = runtime === "single" ? "; pass --session" : ` for runtime ${runtime}`;
    throw new Error(`${match.length} sessions have the identity "${agent}"${hint}`);
  }
  return match[0].session_id;
}

// ---------------------------------------------------------------- in the page
function installRecorder(cfg) {
  if (window.__rec) return;
  let restore = null;
  try {
    const saved = sessionStorage.getItem("__kollabChatRecorderRestore");
    if (saved) restore = JSON.parse(saved);
    sessionStorage.removeItem("__kollabChatRecorderRestore");
  } catch { /* a browser with blocked session storage starts with empty recorder state */ }
  const rec = (window.__rec = {
    frames: [], net: [], marks: [], cut: null, anchorEl: null,
    toolsStarted: Number(restore?.toolsStarted) || 0,
    toolRows: Array.isArray(restore?.toolRows) ? restore.toolRows : [],
    flowSeq: Number.isInteger(restore?.flowSeq) ? restore.flowSeq : null,
    streamTurns: Array.isArray(restore?.streamTurns) ? restore.streamTurns : [],
    streamInputs: Array.isArray(restore?.streamInputs) ? restore.streamInputs : [],
    expectedActiveTurn: restore?.expectedActiveTurn ?? null,
    latestSnapshot: null,
    lastStreamSeq: null,
    streamReady: false,
  });
  const now = () => Math.round(performance.timeOrigin + performance.now() - cfg.wall0);
  rec.mark = (name) => rec.marks.push({ t: now(), name });
  rec.noteToolRow = (row) => {
    if (!row || row.kind !== "tool" || !row.id || rec.toolRows.includes(row.id)) return;
    rec.toolRows.push(row.id);
    if (rec.flowSeq !== null && Number.isInteger(row.seq) && row.seq >= rec.flowSeq) rec.toolsStarted += 1;
  };

  // The chat's streams, logged as they pass.
  const realFetch = window.fetch.bind(window);
  let seq = 0;
  const brief = (value, max = 40) => String(typeof value === "string" ? value : JSON.stringify(value ?? ""))
    .replace(/<agent_hud>[\s\S]*?<\/agent_hud>\s*/g, "").replace(/\s+/g, " ").slice(0, max);
  let tokens = 0;
  const logEvent = (id, kind, raw) => {
    const data = raw.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim()).join("");
    if (!data) return;
    if (data === "[DONE]") return void rec.net.push({ id, kind, t: now(), ev: "[DONE]" });
    let event;
    try { event = JSON.parse(data); } catch { return void rec.net.push({ id, kind, t: now(), raw: data.slice(0, 80) }); }
    const type = event.type ?? "?";
    if (kind === "events") {
      if (type === "token" || type === "thinking") { tokens += 1; return; }
      const entry = { id, kind, t: now(), ev: type };
      if (tokens) { entry.tokensBefore = tokens; tokens = 0; }
      if (event.request_id) entry.rq = event.request_id;
      if (event.request_ids) entry.rqs = event.request_ids;
      for (const key of ["stop_reason", "message", "input_source", "queued", "tool_name", "status", "action"]) {
        if (event[key] !== undefined) entry[key] = brief(event[key], 60);
      }
      if (type === "user_message") entry.text = brief(event.content ?? event.text ?? event.message, 50);
      return void rec.net.push(entry);
    }
    if (kind === "stream") {
      const data = event.data ?? {};
      if (type === "snapshot") {
        rec.streamReady = true;
        rec.latestSnapshot = {
          turns: Array.isArray(data.turns) ? data.turns : [],
          inputs: Array.isArray(data.inputs) ? data.inputs : [],
        };
        rec.streamTurns = rec.latestSnapshot.turns;
        rec.streamInputs = rec.latestSnapshot.inputs;
        rec.lastStreamSeq = Number.isInteger(data.next_seq) ? data.next_seq - 1 : event.seq;
        (data.rows ?? []).forEach(rec.noteToolRow);
      } else if (type === "turn.state") {
        rec.streamTurns = [
          ...rec.streamTurns.filter((turn) => turn.turn_id !== data.turn_id),
          data,
        ];
      } else if (type === "input.state") {
        rec.streamInputs = [
          ...rec.streamInputs.filter((input) => input.id !== data.id),
          data,
        ];
      } else if (type === "replace") {
        (data.rows ?? []).forEach(rec.noteToolRow);
      } else if (type === "row.upsert") {
        rec.noteToolRow(data);
      }
      if (Number.isInteger(event.seq)) rec.lastStreamSeq = Math.max(rec.lastStreamSeq ?? -1, event.seq);
      const entry = { id, kind, t: now(), ev: type, seq: event.seq };
      if (data.id) entry.row = data.id;
      if (data.kind) entry.rowKind = data.kind;
      if (data.state) entry.state = data.state;
      if (data.status !== undefined) entry.status = data.status;
      if (Array.isArray(data.rows)) entry.rows = data.rows.length;
      return void rec.net.push(entry);
    }
    if (/-delta$|^part-|^tool-call-|^result$/.test(type)) return;
    rec.net.push({ id, kind, t: now(), ev: type, ...(event.finishReason ? { finishReason: event.finishReason } : {}),
      ...(event.error ? { error: brief(event.error, 80) } : {}) });
  };
  window.fetch = async (input, init) => {
    let parsed;
    try { parsed = new URL(String(input instanceof Request ? input.url : input), location.href); } catch { return realFetch(input, init); }
    const match = /\/sessions\/[^/]+\/(events|history|stream|inputs)(?:\/|$)/.exec(parsed.pathname);
    if (!match) return realFetch(input, init);
    if (match[1] === "stream" && !parsed.pathname.endsWith(cfg.sessionStreamSuffix)) {
      return realFetch(input, init);
    }
    const id = ++seq;
    const kind = match[1];
    const entry = { id, kind, t: now(), req: `${init?.method ?? "GET"} ${parsed.pathname.replace(/^.*\/sessions\/[^/]+/, "")}${parsed.search.slice(0, 40)}` };
    if (kind === "inputs" && init?.body) {
      try {
        const body = JSON.parse(String(init.body));
        entry.input = body.id;
        entry.text = brief(body.text, 50);
        entry.attachments = Array.isArray(body.attachments) ? body.attachments.length : 0;
      } catch { /* not JSON */ }
    }
    rec.net.push(entry);
    let response;
    try { response = await realFetch(input, init); } catch (error) {
      rec.net.push({ id, kind, t: now(), fetchError: String(error) });
      throw error;
    }
    rec.net.push({ id, kind, t: now(), status: response.status });
    if (kind === "history") {
      response.clone().json().then((page) => rec.net.push({
        id, kind, t: now(), page: `${page.start_index}..${page.end_index}/${page.total_messages}${page.stale ? " STALE" : ""}`,
        tail: (page.history ?? []).slice(-4).map((row) => `${row.role?.[0]}:${brief(row.content, 26)}`),
      })).catch(() => undefined);
      return response;
    }
    if (kind === "inputs") {
      response.clone().json().then((result) => rec.net.push({
        id, kind, t: now(), ev: "accepted", input: result.id, state: result.state,
      })).catch(() => undefined);
      return response;
    }
    if (!response.body) return response;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let closed = false;
    let control;
    const stream = new ReadableStream({
      start(controller) { control = controller; },
      async pull(controller) {
        try {
          const { done, value } = await reader.read();
          if (closed) return;
          if (done) { closed = true; rec.net.push({ id, kind, t: now(), ev: "<stream end>" }); controller.close(); return; }
          buffer += decoder.decode(value, { stream: true });
          const chunks = buffer.split(/\r?\n\r?\n/);
          buffer = chunks.pop() ?? "";
          for (const raw of chunks) logEvent(id, kind, raw);
          controller.enqueue(value);
        } catch (error) {
          if (closed) return;
          closed = true;
          rec.net.push({ id, kind, t: now(), streamError: String(error) });
          controller.error(error);
        }
      },
      cancel(reason) { closed = true; return reader.cancel(reason); },
    });
    if (kind === "stream") {
      // The request's stream ends before the agent's turn does: a dropped
      // connection ("close" ends it cleanly, "error" fails it).
      rec.cut = (mode) => {
        if (closed) return false;
        closed = true;
        rec.net.push({ id, kind, t: now(), ev: `<CUT ${mode}>` });
        reader.cancel("cut").catch(() => undefined);
        try { if (mode === "error") control.error(new TypeError("network error (recorder cut)")); else control.close(); } catch { /* closed */ }
        return true;
      };
    }
    return new Response(stream, { status: response.status, statusText: response.statusText, headers: response.headers });
  };

  // The chat, sampled once per animation frame and stored when it changes.
  let nodeSeq = 0;
  const nodeId = (el) => (el.__recNode ??= ++nodeSeq);
  const hash = (text) => { let h = 2166136261; for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0).toString(36); };
  let viewport = null;
  const findViewport = (row) => {
    for (let el = row?.parentElement; el; el = el.parentElement) {
      if (/(auto|scroll)/.test(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight + 4) return el;
    }
    return null;
  };
  rec.viewport = () => viewport;
  // The row nearest the top of the view: what a reader is looking at.
  rec.anchor = () => {
    const top = viewport?.getBoundingClientRect().top ?? 0;
    let best = null;
    for (const el of document.querySelectorAll("[data-role]")) {
      const rect = el.getBoundingClientRect();
      if (rect.bottom > top + 8 && (!best || rect.top < best.getBoundingClientRect().top)) best = el;
    }
    rec.anchorEl = best;
    rec.mark("anchor set");
  };
  let last = "";
  const sample = () => {
    try {
      const all = document.querySelectorAll("[data-role]");
      const rows = [];
      const texts = [];
      let fading = 0;
      const users = cfg.user.map(() => 0);
      const replies = cfg.reply.map(() => 0);
      // Tool calls drawn since the first message this flow sent. More than the
      // turn has started is a call drawn twice (its live card beside its saved one).
      let toolsDrawn = 0;
      let sent = false;
      for (let i = 0; i < all.length; i++) {
        const el = all[i];
        const role = el.dataset.role;
        const text = (el.textContent ?? "").replace(/\s+/g, " ").trim();
        if (role === "user") {
          let own = false;
          cfg.user.forEach((marker, index) => { if (text.includes(marker)) { users[index] += 1; own = true; } });
          if (own) sent = true;
        }
        if (role === "assistant") {
          if (sent) toolsDrawn += Number(/(\d+) tool calls?/.exec(text)?.[1] ?? (text.includes("Run · ") ? 1 : 0));
          cfg.reply.forEach((marker, index) => { replies[index] += text.split(marker).length - 1; });
        }
        if (i >= all.length - 14) {
          const opacity = Number(getComputedStyle(el).opacity);
          if (opacity < 0.995) fading += 1;
          rows.push(`${role[0]}${nodeId(el)}:${hash(text)}:${text.length}`);
          if (i >= all.length - 4) texts.push(`${role[0]}${nodeId(el)}=${text.slice(0, 64)}`);
        }
      }
      if (!viewport || !viewport.isConnected) viewport = findViewport(all[all.length - 1]);
      const errors = [...document.querySelectorAll(".aui-message-error-root")];
      const frame = {
        n: all.length, rows, texts, fading, users, replies, toolsDrawn, toolsStarted: rec.toolsStarted,
        st: viewport ? Math.round(viewport.scrollTop) : -1,
        pick: document.querySelectorAll('[class*="branch-picker-root"]').length,
        err: errors.length,
        errText: errors.map((el) => (el.textContent ?? "").trim().slice(0, 70)).join(" | "),
        run: document.querySelector('[aria-label="Stop generating"]') ? 1 : 0,
      };
      if (rec.anchorEl) frame.anc = rec.anchorEl.isConnected ? Math.round(rec.anchorEl.getBoundingClientRect().top) : null;
      const signature = JSON.stringify(frame);
      if (signature !== last && rec.frames.length < 60000) { last = signature; rec.frames.push({ t: now(), ...frame }); }
    } catch (error) {
      rec.sampleError = String(error);
    }
    requestAnimationFrame(sample);
  };
  requestAnimationFrame(sample);
}

// ---------------------------------------------------------------- one flow
async function record(flow, session, browser, target) {
  const { runtime, agent } = target;
  const dir = runtime === "single"
    ? path.join(OUT, `${flow}-${LABEL}`)
    : path.join(OUT, runtime, `${flow}-${LABEL}`);
  fs.mkdirSync(dir, { recursive: true });
  const wall0 = Date.now();
  const tag = `${runtime === "single" ? "" : `${runtime}-`}${flow}${LABEL}`;
  const U = (n) => `U${n}-${tag}`;
  const R = (n) => `zebra${n}${tag}`;
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 }, recordVideo: { dir, size: { width: 1280, height: 860 } } });
  await context.addInitScript(installRecorder, {
    wall0,
    sessionStreamSuffix: chatRecorderStreamSuffix(session),
    user: [U(1), U(2), U(3)],
    reply: [R(1), R(2), R(3)],
  });
  const page = await context.newPage();
  const videoStart = Date.now() - wall0;
  const consoleLog = [];
  page.on("pageerror", (error) => consoleLog.push(`${Date.now() - wall0} pageerror: ${String(error).slice(0, 300)}`));

  const collected = { frames: [], net: [], marks: [] };
  let externalSeq = 0;
  const collect = async () => {
    const part = await page.evaluate(() => {
      const rec = window.__rec;
      const out = { frames: rec.frames, net: rec.net, marks: rec.marks, sampleError: rec.sampleError };
      rec.frames = []; rec.net = []; rec.marks = [];
      return out;
    }).catch(() => null);
    if (!part) return;
    collected.frames.push(...part.frames);
    collected.net.push(...part.net);
    collected.marks.push(...part.marks);
    if (part.sampleError) collected.sampleError = part.sampleError;
  };
  const mark = (name) => page.evaluate((n) => window.__rec.mark(n), name)
    .catch(() => collected.marks.push({ t: Date.now() - wall0, name }));
  const postFromClient = async (route, body, name) => {
    await mark(name);
    const id = `client-${++externalSeq}`;
    collected.net.push({ id, kind: "client", t: Date.now() - wall0, req: `POST ${route}${body.id ? ` id=${body.id}` : ""}` });
    const response = await engine(route, { method: "POST", body: JSON.stringify(body) });
    collected.net.push({ id, kind: "client", t: Date.now() - wall0, status: response.status });
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw new Error(`client request ${route} returned ${response.status}${detail ? `: ${detail.slice(0, 180)}` : ""}`);
    }
    response.text().catch(() => undefined);
    return response;
  };
  const sleep = (ms) => page.waitForTimeout(ms);
  const box = () => page.getByPlaceholder(/^Message /i).first();
  const send = async (text, name) => {
    await box().fill(text);
    // While a turn runs the composer sends through its own button.
    const busy = page.getByLabel("Send message while generating");
    if (await busy.count()) {
      await mark(`PAGE SEND ${name} (while generating)`);
      await busy.first().click();
    } else {
      await mark(`PAGE SEND ${name}`);
      await box().press("Enter");
    }
  };
  const other = async (content, name) => {
    const id = `recorder-${tag}-${name}`.replace(/[^A-Za-z0-9._:-]/g, "-");
    await postFromClient(`/sessions/${session}/inputs`, { id, text: content }, `OTHER CLIENT SEND ${name}`);
  };
  const command = async (text, name) => {
    await box().fill(text);
    await mark(name);
    await box().press("Enter");
  };
  const cut = async (mode) => mark(`CUT ${mode} -> ${await page.evaluate((m) => window.__rec.cut?.(m) ?? false, mode)}`);
  /** Until the page shows no run and the reply, and stays so for `quiet` ms. */
  const settle = async (reply, quiet = 9000, max = 240000) => {
    const started = Date.now();
    let calm = null;
    while (Date.now() - started < max) {
      await sleep(500);
      const state = await page.evaluate((marker) => ({
        run: !!document.querySelector('[aria-label="Stop generating"]'),
        has: [...document.querySelectorAll('[data-role="assistant"]')].some((el) => (el.textContent ?? "").includes(marker)),
      }), reply);
      if (!state.run && state.has) { calm ??= Date.now(); if (Date.now() - calm >= quiet) return; } else calm = null;
    }
    await mark("SETTLE TIMEOUT");
  };
  const tools = (n, seconds) => `run \`sleep ${seconds}\` with your terminal tool ${n} times, one call at a time, waiting for each to finish`;
  const say = (n) => `Then reply with only this, as one word with nothing between the parts: zebra, the digit ${n}, then ${tag}.`;
  const apiJson = async (route, init) => {
    const response = await engine(route, init);
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw new Error(`engine request ${route} returned ${response.status}${detail ? `: ${detail.slice(0, 180)}` : ""}`);
    }
    return response.json();
  };
  const waitForRunning = async (timeout = 45000) => {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      if (await page.evaluate(() => !!document.querySelector('[aria-label="Stop generating"]'))) return;
      await sleep(250);
    }
    throw new Error("the turn did not reach a running state before the recorder action");
  };
  const waitForStreamSnapshot = async (timeout = 30000) => {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      const snapshot = await page.evaluate(() => window.__rec.latestSnapshot);
      if (snapshot) return snapshot;
      await sleep(250);
    }
    throw new Error("the chat stream did not send its initial snapshot");
  };
  const waitForStreamRowKind = async (kind, { timeout = 30000, whileRunning = false } = {}) => {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      const state = await page.evaluate((rowKind) => ({
        row: window.__rec.net.find((entry) =>
          entry.kind === "stream" && entry.ev === "row.upsert" && entry.rowKind === rowKind),
        running: !!document.querySelector('[aria-label="Stop generating"]'),
      }), kind);
      if (state.row) {
        if (whileRunning && !state.running) throw new Error(`the ${kind} row arrived after the turn ended`);
        return state.row;
      }
      if (whileRunning && !state.running) throw new Error(`the turn ended before its ${kind} row arrived`);
      await sleep(250);
    }
    throw new Error(`the chat stream did not deliver a ${kind} row within ${timeout / 1000} seconds`);
  };
  const waitForPromptStatusUpdate = async (rowId, initialStatus, afterIndex, timeout = 30000) => {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      const entries = await page.evaluate(() => window.__rec.net);
      const updated = findPromptStatusUpdate(entries, rowId, initialStatus, afterIndex);
      if (updated) return updated;
      await sleep(250);
    }
    throw new Error(`the prompt row ${rowId} did not receive an updated status after approval`);
  };
  const preserveRecorderState = async (expectedActiveTurn) => page.evaluate((activeTurn) => sessionStorage.setItem(
    "__kollabChatRecorderRestore",
    JSON.stringify({
      toolsStarted: window.__rec.toolsStarted,
      toolRows: window.__rec.toolRows,
      flowSeq: window.__rec.flowSeq,
      streamTurns: window.__rec.streamTurns,
      streamInputs: window.__rec.streamInputs,
      expectedActiveTurn: activeTurn,
    }),
  ), expectedActiveTurn);
  const reloadDuringRun = async (reply) => {
    await waitForRunning();
    await sleep(1200);
    const expectedActiveTurn = await page.evaluate(() => {
      const turn = window.__rec.streamTurns.filter((item) => item.state === "running").at(-1);
      if (!turn || !Array.isArray(turn.input_ids) || turn.input_ids.length === 0) return null;
      return { turn_id: turn.turn_id, input_ids: [...turn.input_ids] };
    });
    if (!expectedActiveTurn) {
      throw new Error("the stream did not identify the running turn and its inputs before reload");
    }
    await collect();
    await preserveRecorderState(expectedActiveTurn);
    collected.marks.push({ t: Date.now() - wall0, name: "RELOAD" });
    await page.reload({ waitUntil: "domcontentloaded" });
    const snapshot = await waitForStreamSnapshot();
    const preservedTurn = await page.evaluate(() => window.__rec.expectedActiveTurn);
    if (!hasRunningTurnWithInputs(snapshot, preservedTurn)) {
      throw new Error("the reload snapshot did not retain the active running turn and its inputs");
    }
    await sleep(2500);
    await mark("RELOADED");
    await settle(reply);
  };
  const waitForPermission = async (existing) => {
    const started = Date.now();
    while (Date.now() - started < 90000) {
      const permissions = await apiJson(`/sessions/${session}/permissions`);
      const prompt = (permissions.pending_prompts ?? []).find((item) => {
        const key = `${item.tool_id ?? item.details?.tool_id ?? ""}:${item.permission_id ?? item.details?.permission_id ?? ""}`;
        return key !== ":" && !existing.has(key);
      });
      if (prompt) return prompt;
      await sleep(400);
    }
    throw new Error("the agent did not open a permission prompt within 90 seconds");
  };
  const contextCompactionStatus = async () => {
    const state = await apiJson(`/sessions/${session}/state`);
    return state.context_compaction?.compaction_status ?? "unknown";
  };
  const waitForCompaction = async (previousNetCount, phase = "staged") => {
    const started = Date.now();
    let sawStart = phase === "transcript";
    while (Date.now() - started < 240000) {
      const status = await contextCompactionStatus();
      const updates = await page.evaluate((offset) => window.__rec.net.slice(offset), previousNetCount);
      const eventStatuses = updates
        .filter((entry) => entry.kind === "events" && entry.ev === "context_compaction_status")
        .map((entry) => entry.status);
      const replaced = updates.some((entry) => entry.kind === "stream" && entry.ev === "replace");
      sawStart ||= status === "compacting" || eventStatuses.includes("compacting") || replaced;
      const latestEventStatus = eventStatuses.at(-1);
      if (["failed", "paused", "warning", "rolled_over"].includes(status) ||
          ["failed", "paused", "warning", "rolled_over"].includes(latestEventStatus)) {
        throw new Error(`compaction ended with status ${status === "compacting" ? latestEventStatus : status}`);
      }
      if (isCompactionComplete({
        sawStart,
        status,
        latestEventStatus,
        replaced,
        runtime: activeRuntime,
        phase,
      })) return;
      await sleep(1000);
    }
    throw new Error(phase === "transcript"
      ? "the native transcript replacement did not arrive within 240 seconds"
      : "the compaction did not finish within 240 seconds");
  };
  const runtimeForSession = async () => {
    const details = await apiJson(`/sessions/${session}`);
    return classifyChatRuntime(details);
  };

  await page.goto(WEB, { waitUntil: "domcontentloaded" });
  await page.getByText(new RegExp(agent.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i")).first().click({ timeout: 30000 });
  await sleep(5000);
  await waitForStreamSnapshot();
  await page.evaluate(() => {
    if (window.__rec.flowSeq === null) window.__rec.flowSeq = (window.__rec.lastStreamSeq ?? -1) + 1;
  });
  const activeRuntime = await runtimeForSession();
  if (runtime !== "single" && activeRuntime !== runtime) {
    throw new Error(`identity "${agent}" is ${activeRuntime}, expected ${runtime}`);
  }
  await mark("READY");

  const flows = {
    // One plain send and reply.
    async plain() { await send(`${U(1)}: ${say(1)}`, "U1"); await settle(R(1)); },
    // A turn with several tool calls.
    async tools() { await send(`${U(1)}: ${tools(3, 2)}. ${say(1)}`, "U1"); await settle(R(1)); },
    // The same page sends again while its own turn runs.
    async busy() {
      await send(`${U(1)}: ${tools(3, 5)}. ${say(1)}`, "U1");
      await sleep(6000);
      await send(`${U(2)}: ${say(2)}`, "U2");
      await settle(R(2));
    },
    // The first request's stream ends early, then the page sends again.
    async cut() {
      await send(`${U(1)}: ${tools(3, 6)}. ${say(1)}`, "U1");
      await sleep(7000);
      await cut("close");
      await sleep(3000);
      await send(`${U(2)}: ${say(2)}`, "U2");
      await settle(R(2));
    },
    // Same, with the stream failing as a network error does.
    async cuterr() {
      await send(`${U(1)}: ${tools(3, 6)}. ${say(1)}`, "U1");
      await sleep(7000);
      await cut("error");
      await sleep(3000);
      await send(`${U(2)}: ${say(2)}`, "U2");
      await settle(R(2));
    },
    // The stream ends early and the page only watches from then on.
    async cutwatch() {
      await send(`${U(1)}: ${tools(3, 6)}. ${say(1)}`, "U1");
      await sleep(7000);
      await cut("close");
      await settle(R(1));
    },
    // Another client sends while this page's turn runs.
    async other() {
      await send(`${U(1)}: ${tools(3, 6)}. ${say(1)}`, "U1");
      await sleep(7000);
      await other(`${U(2)}: ${say(2)}`, "U2");
      await settle(R(2));
    },
    // This page only watches a turn another client started.
    async watch() { await other(`${U(1)}: ${tools(3, 4)}. ${say(1)}`, "U1"); await settle(R(1)); },
    // This page sends while it watches another client's turn.
    async watchsend() {
      await other(`${U(1)}: ${tools(3, 6)}. ${say(1)}`, "U1");
      await sleep(7000);
      await send(`${U(2)}: ${say(2)}`, "U2");
      await settle(R(2));
    },
    // A reader scrolled up while the page's own turn runs and ends.
    async read() {
      await send(`${U(1)}: ${tools(3, 5)}. ${say(1)}`, "U1");
      await sleep(4000);
      await page.evaluate(() => { const vp = window.__rec.viewport(); if (vp) vp.scrollTop = Math.max(0, vp.scrollTop - 700); });
      await sleep(250);
      await page.evaluate(() => window.__rec.anchor());
      await settle(R(1));
    },
    // Three sends back to back, each after the last settles.
    async three() {
      for (const n of [1, 2, 3]) { await send(`${U(n)}: ${say(n)}`, `U${n}`); await settle(R(n), 4000); }
    },
    // The page reloads in the middle of its own turn.
    async reload() {
      await send(`${U(1)}: ${tools(3, 6)}. ${say(1)}`, "U1");
      await sleep(7000);
      await reloadDuringRun(R(1));
    },
    // A permission prompt appears in this page and an independent client approves it.
    async "prompt-other"() {
      const initial = await apiJson(`/sessions/${session}/permissions`);
      const pending = initial.pending_prompts ?? [];
      if (pending.length) throw new Error("clear existing permission prompts before recording prompt-other");
      const originalMode = initial.approval_mode || "default";
      if (originalMode !== "confirm_all") {
        await postFromClient(`/sessions/${session}/permissions/mode`, { mode: "confirm_all" }, "SET APPROVAL MODE confirm_all");
      }
      try {
        await send(`${U(1)}: ${tools(1, 8)}. ${say(1)}`, "U1");
        const existing = new Set(pending.map((item) =>
          `${item.tool_id ?? item.details?.tool_id ?? ""}:${item.permission_id ?? item.details?.permission_id ?? ""}`));
        const prompt = await waitForPermission(existing);
        const toolId = prompt.tool_id ?? prompt.details?.tool_id;
        const permissionId = prompt.permission_id ?? prompt.details?.permission_id;
        if (!toolId || !permissionId) throw new Error("permission prompt omitted its tool or permission id");
        const promptRow = await waitForStreamRowKind("prompt");
        if (!promptRow.row || typeof promptRow.status !== "string") {
          throw new Error("the prompt stream row omitted its id or initial status");
        }
        await mark(`PROMPT OPEN ${toolId}`);
        await sleep(900);
        const approvalOffset = await page.evaluate(() => window.__rec.net.length - 1);
        await postFromClient(`/sessions/${session}/permission`, {
          tool_id: toolId,
          permission_id: permissionId,
          decision: "approve",
          scope: "once",
        }, "OTHER CLIENT ANSWER PROMPT");
        await waitForPromptStatusUpdate(promptRow.row, promptRow.status, approvalOffset);
        await settle(R(1));
      } finally {
        if (originalMode !== "confirm_all") {
          await postFromClient(`/sessions/${session}/permissions/mode`, { mode: originalMode }, `RESTORE APPROVAL MODE ${originalMode}`);
        }
      }
    },
    // Seed enough turns for /compact now, then preserve the live thread through compaction.
    async compact() {
      for (let n = 1; n <= 5; n += 1) {
        const reply = `recorder-seed-${n}-${tag}`;
        await send(`Store this short context seed ${n} for later reference, then reply with only ${reply}.`, `SEED ${n}`);
        await settle(reply, 1500);
      }
      const priorEvents = await page.evaluate(() => window.__rec.net.length);
      const before = await contextCompactionStatus();
      if (before === "compacting") throw new Error("a compaction is already running in this session");
      if (before === "unknown") throw new Error("the engine did not report context compaction status for this session");
      await command("/compact now", "PAGE COMMAND /compact now");
      await waitForCompaction(priorEvents);
      if (activeRuntime === "native") await mark("COMPACTION STAGED");
      else await mark("COMPACTION COMPLETE");
      await send(`${U(1)}: ${say(1)}`, "U1");
      if (activeRuntime === "native") {
        await waitForCompaction(priorEvents, "transcript");
        await mark("COMPACTION COMPLETE");
      }
      await settle(R(1));
    },
    // Reload while a saved native goal is running; ACP uses the same goal-shaped chat prompt.
    async "goal-reload"() {
      if (activeRuntime === "native") {
        await command(`/goal ${U(1)}: Use your terminal tool to run \`sleep 10\`, then complete this goal with a concise report containing exactly ${R(1)}.`, "PAGE START GOAL");
      } else {
        await send(`${U(1)}: Work toward this goal: ${tools(1, 10)}. ${say(1)}`, "U1");
      }
      await reloadDuringRun(R(1));
    },
    // Inject a real hub message while the page's turn is still running.
    async "hub-note"() {
      await send(`${U(1)}: ${tools(3, 6)}. ${say(1)}`, "U1");
      await waitForRunning();
      await sleep(7000);
      if (!await page.evaluate(() => !!document.querySelector('[aria-label="Stop generating"]'))) {
        throw new Error("the turn finished before the hub note could arrive");
      }
      await postFromClient("/hub/messages", {
        target: agent,
        from_identity: `chat-recorder-${LABEL}`,
        content: `${tag} hub note arriving during the active turn. Acknowledge it briefly, then finish the current request.`,
      }, "HUB NOTE ARRIVES MID-TURN");
      await waitForStreamRowKind("note", { whileRunning: true });
      await settle(R(1));
    },
  };
  let failed = null;
  try { await flows[flow](); } catch (error) { failed = String(error); }
  await mark("END");
  await collect();
  await page.screenshot({ path: path.join(dir, "final.png") }).catch(() => undefined);
  const video = page.video();
  await context.close();
  if (video) await video.saveAs(path.join(dir, "video.webm")).catch(() => undefined);

  // ---- what the frames show
  const { frames, net, marks } = collected;
  frames.sort((a, b) => a.t - b.t);
  const { events, defects } = analyzeChatFrames(frames, marks);
  const final = frames.at(-1);
  const summary = {
    flow, runtime: runtime === "single" ? activeRuntime : runtime, agent, label: LABEL, web: WEB, failed, defects,
    final: final ? { users: final.users, replies: final.replies, running: final.run } : null,
    frames: frames.length, videoStartMs: videoStart, sampleError: collected.sampleError ?? null,
  };

  // ---- one timeline: what the page did, what the wire carried, what the frames showed
  const fmt = (t) => `${(t / 1000).toFixed(3).padStart(8)}s`;
  const wire = (entry) => {
    const head = `${entry.kind}#${entry.id}`;
    if (entry.req) return `${head} -> ${entry.req}${entry.commands ? ` commands=${JSON.stringify(entry.commands)} rows=${entry.sentCount} synced=${JSON.stringify(entry.sentSynced)} tail=${JSON.stringify(entry.sent)}` : ""}${entry.input ? ` input=${entry.input} text=${JSON.stringify(entry.text)} attachments=${entry.attachments}` : ""}`;
    if (entry.status !== undefined) return `${head} <- HTTP ${entry.status}`;
    if (entry.page) return `${head} <- rows ${entry.page} tail=${JSON.stringify(entry.tail)}`;
    if (entry.ops) return `${head} state ${entry.ops.map((op) => op.rows ? `messages=[..${op.rows.length}: ${op.rows.slice(-4).join("  ")}]` : op.row ? `${op.set}=${op.row}` : op.set ? `${op.set}=${JSON.stringify(op.value)}` : `${op.op}:${op.path}`).join(" ; ")}`;
    const rest = { ...entry };
    delete rest.id; delete rest.kind; delete rest.t;
    return `${head} ${JSON.stringify(rest)}`;
  };
  const lines = [
    ...marks.map((m) => ({ t: m.t, text: `## ${m.name}` })),
    ...events.map((e) => ({ t: e.t, text: `!! ${e.name} ${e.note}` })),
    ...net.map((entry) => ({ t: entry.t, text: `   ${wire(entry)}` })),
  ];
  let shown = null;
  for (const frame of frames) {
    const key = `${frame.n}|${frame.users.join("/")}|${frame.replies.join("/")}|${frame.toolsDrawn}|${frame.run}|${frame.err}|${frame.rows.map((row) => row.split(":")[0]).join(",")}`;
    if (key === shown) continue;
    shown = key;
    lines.push({ t: frame.t, text: `-- rows=${frame.n} bubbles=${frame.users.join("/")} replies=${frame.replies.join("/")} toolsDrawn=${frame.toolsDrawn}/${frame.toolsStarted} running=${frame.run} errorBoxes=${frame.err}\n${" ".repeat(13)}last rows: ${frame.texts.join("  ||  ")}` });
  }
  lines.sort((a, b) => a.t - b.t);
  fs.writeFileSync(path.join(dir, "timeline.txt"), [
    ...lines.map((line) => `${fmt(line.t)} ${line.text}`), "", ...consoleLog.map((line) => `console: ${line}`), "",
    `SUMMARY ${JSON.stringify(summary)}`, "",
  ].join("\n"));
  fs.writeFileSync(path.join(dir, "frames.json"), JSON.stringify({ wall0, videoStart, runtime: summary.runtime, agent, marks, events, frames }));
  fs.writeFileSync(path.join(dir, "summary.json"), JSON.stringify(summary, null, 1));
  return { summary, dir };
}

// ---------------------------------------------------------------- run
const targets = runtimeMatrix
  ? Object.entries(runtimeMatrix).map(([runtime, agent]) => ({ runtime, agent }))
  : [{ runtime: "single", agent: AGENT }];
for (const target of targets) {
  target.session = await sessionId(target.agent, target.runtime, target.runtime === "single" ? sessionOverride : "");
}
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH ?? "playwright-core");
const browser = await chromium.launch({ headless: !HEADED, executablePath: process.env.CHROME_PATH ?? chromium.executablePath() });
let bad = 0;
const defectLabels = {
  blank: "BLANK",
  remount: "REMOUNT-ALL",
  massFade: "MASS-FADE",
  dupUser: "DUP-USER",
  dupReply: "DUP-REPLY",
  dupTools: "DUP-TOOLS",
  replyGone: "REPLY-GONE",
  picker: "PICKER",
  anchorMoved: "ANCHOR-MOVED",
  anchorLost: "ANCHOR-LOST",
  errorBoxLeft: "ERROR-BOX",
};
try {
  for (const target of targets) {
    for (const flow of wanted === "all" ? CHAT_RECORDER_FLOWS : [wanted]) {
      const { summary, dir } = await record(flow, target.session, browser, target);
      const found = Object.entries(summary.defects)
        .filter(([, count]) => count > 0)
        .map(([name, count]) => `${defectLabels[name] ?? name}=${count}`);
      const incomplete = summary.failed ? [`failed: ${summary.failed}`] : [];
      if (found.length || incomplete.length) bad += 1;
      const label = target.runtime === "single" ? flow : `${target.runtime}/${flow}`;
      console.log(`${found.length || incomplete.length ? "DEFECT" : "CLEAN "} ${label.padEnd(27)} ${[...found, ...incomplete].join(" ") || "no defect in any frame"}`);
      console.log(`       ${path.join(dir, "timeline.txt")}`);
    }
  }
} finally {
  await browser.close();
}
process.exit(bad ? 1 : 0);
