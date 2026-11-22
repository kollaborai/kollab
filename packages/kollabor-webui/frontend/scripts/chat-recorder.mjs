// Frame-by-frame recorder for the web UI chat.
//
// Drives a real page against a running engine and a real agent, samples the
// chat on every animation frame inside the page, logs each /assistant, /events
// and /history exchange on the same clock, records a video, and writes one
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
//   ERROR-BOX     an error box shows (expected for a moment in the cut flows)
//   ANCHOR-MOVED  the row a reader scrolled to moved, or ANCHOR-LOST: was rebuilt
//
// Use it on any change to the chat's history sync, live turn or runtime, before
// calling the change done. Run after `npm ci`, from this folder's parent:
//
//   node scripts/chat-recorder.mjs <flow|all> --agent <name> [options]
//
//   --web <url>          the web UI (default http://127.0.0.1:8080)
//   --engine <url>       the engine the page talks to (default http://127.0.0.1:7433)
//   --token-file <path>  the engine's token file (default ~/.kollab/engine.token)
//   --agent <name>       the agent's name as the sidebar shows it (required)
//   --session <id>       its session id (default: the session with that identity)
//   --label <text>       makes this run's messages unique (default: the time)
//   --out <dir>          where the recordings go (default ./chat-recordings)
//   --headed             show the browser
//
// Flows: plain, tools, busy, cut, cuterr, cutwatch, other, watch, watchsend,
// read, three, reload. `all` runs each in turn. The agent runs real turns with
// its model and needs a terminal tool it may run without asking (`sleep`).
// Use a throwaway agent in a scratch folder, on a side engine when the change
// touches the engine. Exits 1 when a flow shows a defect.
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH ?? "playwright-core");

const FLOWS = ["plain", "tools", "busy", "cut", "cuterr", "cutwatch", "other", "watch", "watchsend", "read", "three", "reload"];
const args = process.argv.slice(2);
const option = (name, fallback) => {
  const at = args.indexOf(`--${name}`);
  return at >= 0 && args[at + 1] !== undefined ? args[at + 1] : fallback;
};
const wanted = args[0] && !args[0].startsWith("--") ? args[0] : "";
const AGENT = option("agent", "");
if (!(wanted === "all" || FLOWS.includes(wanted)) || !AGENT) {
  console.error(`usage: node scripts/chat-recorder.mjs <${FLOWS.join("|")}|all> --agent <name> [--web url] [--engine url] [--token-file path] [--session id] [--label text] [--out dir] [--headed]`);
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

async function sessionId() {
  const given = option("session", "");
  if (given) return given;
  const listed = await (await engine("/sessions")).json();
  const sessions = Array.isArray(listed) ? listed : listed.sessions ?? [];
  const match = sessions.filter((session) => session.identity === AGENT);
  if (match.length !== 1) throw new Error(`${match.length} sessions have the identity "${AGENT}"; pass --session`);
  return match[0].session_id;
}

// ---------------------------------------------------------------- in the page
function installRecorder(cfg) {
  if (window.__rec) return;
  const rec = (window.__rec = { frames: [], net: [], marks: [], cut: null, anchorEl: null, toolsStarted: 0 });
  const now = () => Math.round(performance.timeOrigin + performance.now() - cfg.wall0);
  rec.mark = (name) => rec.marks.push({ t: now(), name });

  // The chat's streams, logged as they pass.
  const realFetch = window.fetch.bind(window);
  let seq = 0;
  const brief = (value, max = 40) => String(typeof value === "string" ? value : JSON.stringify(value ?? ""))
    .replace(/<agent_hud>[\s\S]*?<\/agent_hud>\s*/g, "").replace(/\s+/g, " ").slice(0, max);
  const rowBrief = (row) => {
    if (!row || typeof row !== "object") return row;
    const parts = Array.isArray(row.content)
      ? row.content.map((part) => part?.type === "text" ? `t${String(part.text ?? "").length}`
        : part?.type === "tool-call" ? `c:${part.toolCallId}${"result" in part ? "+" : ""}` : part?.type?.[0] ?? "?").join(",")
      : `s${String(row.content ?? "").length}`;
    const custom = row.metadata?.custom ?? {};
    return `${row.id}|${row.role?.[0]}|${parts}|${row.status?.type ?? ""}` +
      `${custom.historySourceIndex !== undefined ? `|src${custom.historySourceIndex}` : ""}` +
      `${custom.requestId ? `|rq:${custom.requestId}` : ""}${custom.requestIds ? `|rqs:${custom.requestIds.join("+")}` : ""}`;
  };
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
      if (type === "tool_start") rec.toolsStarted += 1;
      const entry = { id, kind, t: now(), ev: type };
      if (tokens) { entry.tokensBefore = tokens; tokens = 0; }
      if (event.request_id) entry.rq = event.request_id;
      if (event.request_ids) entry.rqs = event.request_ids;
      for (const key of ["stop_reason", "message", "input_source", "queued", "tool_name"]) {
        if (event[key] !== undefined) entry[key] = brief(event[key], 60);
      }
      if (type === "user_message") entry.text = brief(event.content ?? event.text ?? event.message, 50);
      return void rec.net.push(entry);
    }
    if (type === "update-state") {
      const ops = (event.operations ?? []).map((op) => {
        const where = (op.path ?? []).join(".");
        if (where === "messages" && Array.isArray(op.value)) return { set: "messages", rows: op.value.map(rowBrief) };
        if (/^messages\.\d+$/.test(where)) return { set: where, row: rowBrief(op.value) };
        if (where === "synced" || where === "error") return { set: where, value: op.value };
        return { op: op.type, path: where };
      });
      return void rec.net.push({ id, kind, t: now(), ev: type, ops });
    }
    if (/-delta$|^part-|^tool-call-|^result$/.test(type)) return;
    rec.net.push({ id, kind, t: now(), ev: type, ...(event.finishReason ? { finishReason: event.finishReason } : {}),
      ...(event.error ? { error: brief(event.error, 80) } : {}) });
  };
  window.fetch = async (input, init) => {
    let parsed;
    try { parsed = new URL(String(input instanceof Request ? input.url : input), location.href); } catch { return realFetch(input, init); }
    const match = /\/sessions\/[^/]+\/(assistant|events|history)(?:\/|$)/.exec(parsed.pathname);
    if (!match) return realFetch(input, init);
    const id = ++seq;
    const kind = match[1];
    const entry = { id, kind, t: now(), req: `${init?.method ?? "GET"} ${parsed.pathname.replace(/^.*\/sessions\/[^/]+/, "")}${parsed.search.slice(0, 40)}` };
    if (kind === "assistant" && init?.body) {
      try {
        const body = JSON.parse(String(init.body));
        entry.commands = (body.commands ?? []).map((command) =>
          `${command.type}:${command.message?.id ?? command.toolCallId ?? ""}:${brief((command.message?.parts ?? []).map((part) => part.text ?? "[img]").join(" "), 40)}`);
        entry.sent = (body.state?.messages ?? []).slice(-6).map(rowBrief);
        entry.sentCount = (body.state?.messages ?? []).length;
        entry.sentSynced = body.state?.synced ?? null;
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
    if (kind === "assistant") {
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
async function record(flow, session, browser) {
  const dir = path.join(OUT, `${flow}-${LABEL}`);
  fs.mkdirSync(dir, { recursive: true });
  const wall0 = Date.now();
  const tag = `${flow}${LABEL}`;
  const U = (n) => `U${n}-${tag}`;
  const R = (n) => `zebra${n}${tag}`;
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 }, recordVideo: { dir, size: { width: 1280, height: 860 } } });
  await context.addInitScript(installRecorder, { wall0, user: [U(1), U(2), U(3)], reply: [R(1), R(2), R(3)] });
  const page = await context.newPage();
  const videoStart = Date.now() - wall0;
  const consoleLog = [];
  page.on("pageerror", (error) => consoleLog.push(`${Date.now() - wall0} pageerror: ${String(error).slice(0, 300)}`));

  const collected = { frames: [], net: [], marks: [] };
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
    await mark(`OTHER CLIENT SEND ${name}`);
    const response = await engine(`/sessions/${session}/message`, { method: "POST", body: JSON.stringify({ content }) });
    response.text().catch(() => undefined);  // an unread stream can stall the engine's send
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

  await page.goto(WEB, { waitUntil: "domcontentloaded" });
  await page.getByText(new RegExp(AGENT.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i")).first().click({ timeout: 30000 });
  await sleep(5000);
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
      await collect();
      // The page's own log goes with the page: keep these marks and its count here.
      const started = await page.evaluate(() => window.__rec.toolsStarted);
      collected.marks.push({ t: Date.now() - wall0, name: "RELOAD" });
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.evaluate((count) => { window.__rec.toolsStarted += count; }, started);
      await sleep(2500);
      await mark("RELOADED");
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
  const at = (name) => marks.find((m) => m.name === name)?.t;
  const readyAt = at("READY") ?? 0;
  // A page reload blanks the page: that is the flow, not a defect.
  const reloading = (t) => at("RELOAD") !== undefined && t >= at("RELOAD") && t <= (at("RELOADED") ?? Infinity) + 3000;
  const baseline = frames.find((frame) => frame.t >= readyAt) ?? frames[0];
  const events = [];
  const defects = { blank: 0, remount: 0, massFade: 0, dupUser: 0, dupReply: 0, dupTools: 0, replyGone: 0, picker: 0, anchorMoved: 0, anchorLost: 0, errorBoxLeft: 0 };
  const flag = (t, name, note, counter) => { events.push({ t, name, note }); if (counter) defects[counter] += 1; };
  const ids = (frame) => new Set(frame.rows.map((row) => /^[a-z](\d+):/.exec(row)?.[1]));
  const over = (counts, base) => counts.some((count, index) => count - (base?.[index] ?? 0) > 1);
  const on = { dupUser: false, dupReply: false, dupTools: false, picker: false, err: false, fade: false };
  const seen = baseline ? baseline.replies.map(() => false) : [];
  const gone = seen.map(() => false);
  let previous = null;
  let anchor = null;
  for (const frame of frames) {
    if (frame.t < readyAt || reloading(frame.t)) { previous = frame; continue; }
    if (previous && !reloading(previous.t)) {
      if (frame.n === 0 && previous.n > 0) flag(frame.t, "BLANK", `rows ${previous.n} -> 0`, "blank");
      if (previous.n >= 3 && frame.n >= 3 && ![...ids(frame)].some((id) => ids(previous).has(id))) {
        flag(frame.t, "REMOUNT-ALL", `every row is a new element (${previous.n} -> ${frame.n} rows)`, "remount");
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
    if (dupTools && !on.dupTools) flag(frame.t, "DUP-TOOLS", `${frame.toolsDrawn} tool calls drawn, ${frame.toolsStarted} started`, "dupTools");
    if (!dupTools && on.dupTools) flag(frame.t, "DUP-TOOLS-END", "");
    on.dupTools = dupTools;
    frame.replies.forEach((count, index) => {
      const shows = count - (baseline?.replies?.[index] ?? 0) > 0;
      if (shows && gone[index]) { gone[index] = false; flag(frame.t, "REPLY-BACK", `reply ${index + 1}`); }
      if (shows) seen[index] = true;
      else if (seen[index] && !gone[index]) { gone[index] = true; flag(frame.t, "REPLY-GONE", `reply ${index + 1} left the screen`, "replyGone"); }
    });
    const picker = frame.pick > (baseline?.pick ?? 0);
    if (picker && !on.picker) flag(frame.t, "PICKER", `${frame.pick} "N / M" switcher(s)`, "picker");
    on.picker = picker;
    const err = frame.err > (baseline?.err ?? 0);
    if (err && !on.err) flag(frame.t, "ERROR-BOX", `"${frame.errText}"`);
    if (!err && on.err) flag(frame.t, "ERROR-BOX-END", "");
    on.err = err;
    if ("anc" in frame) {
      if (frame.anc === null) { if (anchor !== "lost") flag(frame.t, "ANCHOR-LOST", "the row being read was rebuilt", "anchorLost"); anchor = "lost"; }
      else if (anchor === null) anchor = frame.anc;
      else if (anchor !== "lost" && Math.abs(frame.anc - anchor) > 3) { flag(frame.t, "ANCHOR-MOVED", `the row being read moved ${frame.anc - anchor} px`, "anchorMoved"); anchor = frame.anc; }
    }
    previous = frame;
  }
  if (on.err) defects.errorBoxLeft = 1;
  const final = frames.at(-1);
  const summary = {
    flow, label: LABEL, web: WEB, failed, defects,
    final: final ? { users: final.users, replies: final.replies, running: final.run } : null,
    frames: frames.length, videoStartMs: videoStart, sampleError: collected.sampleError ?? null,
  };

  // ---- one timeline: what the page did, what the wire carried, what the frames showed
  const fmt = (t) => `${(t / 1000).toFixed(3).padStart(8)}s`;
  const wire = (entry) => {
    const head = `${entry.kind}#${entry.id}`;
    if (entry.req) return `${head} -> ${entry.req}${entry.commands ? ` commands=${JSON.stringify(entry.commands)} rows=${entry.sentCount} synced=${JSON.stringify(entry.sentSynced)} tail=${JSON.stringify(entry.sent)}` : ""}`;
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
  fs.writeFileSync(path.join(dir, "frames.json"), JSON.stringify({ wall0, videoStart, marks, events, frames }));
  fs.writeFileSync(path.join(dir, "summary.json"), JSON.stringify(summary, null, 1));
  return { summary, dir };
}

// ---------------------------------------------------------------- run
const session = await sessionId();
const browser = await chromium.launch({ headless: !HEADED, executablePath: process.env.CHROME_PATH ?? chromium.executablePath() });
let bad = 0;
try {
  for (const flow of wanted === "all" ? FLOWS : [wanted]) {
    const { summary, dir } = await record(flow, session, browser);
    const found = Object.entries(summary.defects).filter(([, count]) => count > 0).map(([name, count]) => `${name}=${count}`);
    const incomplete = summary.failed ? [`failed: ${summary.failed}`] : [];
    if (found.length || incomplete.length) bad += 1;
    console.log(`${found.length || incomplete.length ? "DEFECT" : "CLEAN "} ${flow.padEnd(10)} ${[...found, ...incomplete].join(" ") || "no defect in any frame"}`);
    console.log(`       ${path.join(dir, "timeline.txt")}`);
  }
} finally {
  await browser.close();
}
process.exit(bad ? 1 : 0);
