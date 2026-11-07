// The real public test loader and an explicitly selected scratch engine.
// KOLLAB_PHONELINK_TEST_WEBUI=http://127.0.0.1:18040 node tests/phonelink-live-load.mjs
// Creates, approves and revokes only its own named browser-test phone.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { chromium, webkit, _android as android } from "playwright-core";
import { installRecordedVoiceHooks, runRecordedVoiceCheck } from "./phonelink-recorded-voice.mjs";

const origin = process.env.KOLLAB_PHONELINK_TEST_WEBUI;
assert.equal(origin, "http://127.0.0.1:18040", "select the isolated #440 engine explicitly");
const dist = process.env.KOLLAB_PHONELINK_TEST_DIST;
assert.ok(dist?.startsWith("/private/tmp/k440.7vZ8mP/"), "select the intended scratch build explicitly");
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const loaderHash = sha256(await readFile(path.join(dist, "phonelink/loader/loader.js")));
const expectedApp = await Promise.all(["phone-app.css", "phone-app.js.gz"].map(async (name) => {
  const bytes = await readFile(path.join(dist, "phonelink/app", name));
  return { name, size: bytes.length, sha256: sha256(bytes) };
}));
const config = await (await fetch(origin + "/api/config")).json();
assert.equal(config.engine_url, "http://127.0.0.1:17440");
const control = async (method, path, body) => {
  const response = await fetch(config.engine_url + path, {
    method,
    signal: AbortSignal.timeout(15_000),
    headers: { Authorization: "Bearer " + config.token, "Content-Type": "application/json", Origin: config.engine_url },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json();
  assert.ok(response.ok, `${method} scratch control: ${response.status} ${typeof result.detail === "string" ? result.detail : ""}`);
  return result;
};
const watchVoice = async (voiceSessionId, receive, generation) => {
  const abort = new AbortController();
  const cursor = Buffer.from(JSON.stringify({ v: voiceSessionId, g: generation, s: 0 })).toString("base64url");
  const response = await fetch(`${config.engine_url}/voice/sessions/${voiceSessionId}/events?cursor=${cursor}`, {
    headers: { Authorization: "Bearer " + config.token, Origin: config.engine_url }, signal: abort.signal,
  });
  assert.ok(response.ok, "the owned voice event stream must open");
  let failure;
  const finished = (async () => {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        let end;
        while ((end = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
          const data = frame.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
          if (data) receive(JSON.parse(data));
        }
      }
    } catch (error) { if (!abort.signal.aborted) failure = error; }
    finally { reader.releaseLock(); }
  })();
  return { async close() { abort.abort(); await finished; if (failure) throw failure; } };
};
const offer = await control("POST", "/phonelink/phones", { name: "Automated public loader test 440" });
let browser;
let device;
let androidContext;
try {
  const browserName = process.env.PHONE_BROWSER ?? "webkit";
  let page;
  if (browserName === "android" || browserName === "android-chrome") {
    device = (await android.devices()).find((candidate) => candidate.serial() === "emulator-5584");
    assert.ok(device, "the isolated emulator must already be running");
    if (browserName === "android-chrome") {
      androidContext = await device.launchBrowser();
      // This is the owned test emulator's persistent Chrome context. Close
      // only earlier test-loader tabs so they cannot reconnect with shared
      // origin identity while this run links its new phone. Keep stored keys.
      const previous = androidContext.pages().filter((candidate) => {
        try {
          const url = new URL(candidate.url());
          return url.origin === new URL(offer.link).origin && url.pathname === "/relay/v1/phone/k440-efbb2840.html";
        }
        catch { return false; }
      });
      for (const candidate of previous) await candidate.close();
      page = await androidContext.newPage();
    } else {
      page = await (await device.webView({ pkg: "ai.kollabor.kollab" })).page();
    }
  } else {
    browser = await ({ chromium, webkit })[browserName].launch({
      headless: true,
      executablePath: browserName === "webkit" ? process.env.WEBKIT_PATH : process.env.CHROME_PATH,
    });
    page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  }
  if (process.env.PHONE_VOICE_CHECK === "1") {
    assert.notEqual(process.env.PHONE_TRACE, "1", "voice acceptance uses the exact loader bytes");
    const input = process.env.PHONE_VOICE_INPUT;
    assert.ok(input?.startsWith("/private/tmp/k440.7vZ8mP/"));
    await installRecordedVoiceHooks(page, (await readFile(input)).toString("base64"));
  }
  let diagnosticBundle = "";
  if (process.env.PHONE_TRACE === "1") {
    // Diagnostic only: add a stack print to the existing error constructor.
    // This run is not evidence for acceptance of the unmodified artifact.
    await page.route("**/k440-efbb2840.js*", async (route) => {
      try {
        const response = await route.fetch();
        const source = await response.text();
        const message = "Connection to your computer was lost. An action already sent may still finish there.";
        const needle = ['"', "'", "`"].map((quote) => quote + message + quote).find((text) => source.includes(text));
        assert.ok(needle, "the diagnostic error message must exist");
        assert.equal(source.split(needle).length, 2);
        diagnosticBundle = source.replace(needle,
          `(window.__k440LostCount=(window.__k440LostCount||0)+1,window.__k440LostCount<=5&&console.warn("PHONE_LOST_STACK "+new Error().stack),${needle})`);
        await route.fulfill({ response, body: diagnosticBundle });
      } catch (error) {
        console.error("diagnostic route failed:", error.name);
        await route.abort();
      }
    });
  }
  await page.addInitScript((trace) => {
    if (!trace) return;
    const parse = JSON.parse;
    const stringify = JSON.stringify.bind(JSON);
    let traced = 0;
    const emit = (...fields) => {
      if (trace && traced++ < 200) console.log("PHONE_TRACE " + stringify(fields));
    };
    const host = new URLSearchParams(location.hash.slice(1)).get("host");
    const NativeSocket = window.WebSocket;
    window.WebSocket = class extends NativeSocket {
      constructor(...args) {
        super(...args);
        let session = "";
        this.addEventListener("message", (event) => {
          const frame = parse(event.data);
          if (frame.type === "peers") {
            const peer = frame.peers.find((peer) => peer.key === host);
            emit("peers", { present: !!peer, changed: !!session && peer?.session !== session });
            if (peer) session = peer.session;
          } else if (frame.type === "error") emit("relay error", frame.code ?? "unspecified");
        });
        this.addEventListener("close", (event) => emit("socket closed", event.code));
      }
      close(...args) {
        emit("client close", new Error().stack.split("\n").slice(1, 5).map((line) => line.replace(/https?:.*$/, "")));
        return super.close(...args);
      }
    };
    JSON.stringify = (value, ...rest) => {
      if (value?.op === "start") emit("packet start", value.seq,
        value.path?.split("?")[0].replace(/\/[0-9a-f]{12,64}(?=\/|$)/g, "/[id]"));
      else if (value?.kind === "request" && value.payload?.method !== "phone_http") emit("RPC", value.payload?.method);
      return stringify(value, ...rest);
    };
    JSON.parse = (...args) => {
      const value = parse(...args);
      if (typeof value?.seq === "number" && value.error) emit("packet error", value.seq, value.error);
      if (value?.payload?.error) {
        const raw = JSON.stringify(value.payload.error);
        const cause = ["failed", "deadline", "invalid phone session packet", "phone session is unavailable", "phone session changed", "phone session response exceeds its frame limit"]
          .find((code) => raw.includes(code)) ?? "unclassified";
        console.error("phone RPC refusal: " + cause);
      }
      return value;
    };
  }, process.env.PHONE_TRACE === "1");
  page.on("console", (message) => {
    if (message.text().startsWith("PHONE_LOST_STACK ")) {
      console.error(message.text());
      for (const match of message.text().matchAll(/k440-efbb2840\.js[^\s]*?:(\d+):(\d+)/g)) {
        const line = diagnosticBundle.split("\n")[Number(match[1]) - 1] ?? "";
        const column = Number(match[2]) - 1;
        console.error("lost source site:", line.slice(Math.max(0, column - 100), column + 130));
      }
    }
    if (message.text().startsWith("PHONE_TRACE ") ||
        (message.type() === "error" && message.text().startsWith("phone RPC refusal:"))) console.error(message.text());
  });
  page.on("pageerror", (error) => console.error("browser error:", error.name,
    error.message.replace(/(?:https?|wss?):\/\/[^\s"'<>]+/g, "[url]").slice(0, 250)));
  const link = new URL(offer.link);
  link.pathname = "/relay/v1/phone/k440-efbb2840.html";
  link.searchParams.set("rev", loaderHash.slice(0, 16));
  const loaderResponse = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/k440-efbb2840.js"));
  await page.goto(link.toString());
  const servedLoader = await (await loaderResponse).body();
  if (process.env.PHONE_TRACE !== "1") assert.equal(sha256(servedLoader), loaderHash, "served test loader matches this build");
  await page.getByText("Your Computer's Fingerprint", { exact: true }).waitFor({ timeout: 30_000 });
  assert.ok((await page.locator("body").innerText()).includes(offer.fingerprint));
  let pending;
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    const status = await control("GET", "/phonelink");
    pending = status.phones.find((phone) => phone.id === offer.id)?.pending[0];
    if (pending) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  assert.ok(pending, "the scratch computer sees this test phone");
  assert.ok((await page.locator("body").innerText()).includes(pending.code), "both pairing codes match");
  await control("POST", `/phonelink/phones/${offer.id}/approve`, { key: pending.key });
  try {
    try {
      await page.locator("#root > *").first().waitFor({ timeout: 40_000 });
    } catch (error) {
      // No application action has been sent. A relay restart can interrupt the
      // signed manifest/files; retry only this read-only bootstrap once using
      // the saved approval, never the subsequent typed or voice request.
      if (await page.locator("#root > *").count()) throw error;
      console.log(`${browserName}: retrying the interrupted read-only app bootstrap once`);
      await page.reload({ timeout: 30_000 });
      await page.locator("#root > *").first().waitFor({ timeout: 40_000 });
    }
    const appFiles = await page.evaluate(async () => {
      const db = await new Promise((resolve, reject) => {
        const open = indexedDB.open("kollab-phonelink-app", 1);
        open.onsuccess = () => resolve(open.result);
        open.onerror = () => reject(open.error);
      });
      try {
        const app = await new Promise((resolve, reject) => {
          const get = db.transaction("app").objectStore("app").get("current");
          get.onsuccess = () => resolve(get.result);
          get.onerror = () => reject(get.error);
        });
        return app.manifest.files.map(({ name, size, sha256 }) => ({ name, size, sha256 }));
      } finally { db.close(); }
    });
    assert.deepEqual(appFiles, expectedApp, "signed app matches this build");
    await page.getByRole("button", { name: "Toggle Sidebar", exact: true }).click();
    await page.locator('button[title*="phone-voice-test-440-solo"]').click();
    await page.getByRole("textbox", { name: "Message input" }).waitFor({ timeout: 30_000 });
  } catch (error) {
    console.error("app load status:", (await page.locator("body").innerText()).slice(0, 300));
    const target = page.locator('button[title*="phone-voice-test-440-solo"]');
    console.error("exact target row:", await target.count() ? await target.evaluate((button) => button.parentElement.innerText) : "target row missing");
    throw error;
  }
  console.log(`${browserName}: signed app rendered through the public relay; chat composer ready`);
  if (process.env.PHONE_HISTORY_CHECK === "1") {
    const text = (await page.locator('[data-slot="aui_thread-viewport"]').innerText()).toLowerCase();
    console.log("physical voice history on fresh load", JSON.stringify({
      transcriptVisible: text.includes("testing testing one two three"),
      replyVisible: text.includes("i received the transcription"),
    }));
  }
  if (process.env.PHONE_SEND_CHECK === "1") {
    const marker = "OK440_" + randomUUID().slice(0, 8);
    const message = `PhoneLink transport check: reply with exactly ${marker}`;
    const input = page.getByRole("textbox", { name: "Message input" });
    await input.fill(message);
    console.log("composer before submit:", JSON.stringify(await page.locator("form.aui-composer-root").evaluate((form) => ({
      disabled: form.querySelector("textarea")?.disabled,
      buttons: Array.from(form.querySelectorAll("button")).map((button) => ({
        label: button.getAttribute("aria-label"), title: button.getAttribute("title"),
        text: button.innerText, type: button.type, disabled: button.disabled,
      })),
    }))));
    await input.press("Enter");
    console.log("composer cleared after Enter:", (await input.inputValue()).trim() === "");
    try {
      await page.getByText(marker, { exact: true }).first().waitFor({ timeout: 90_000 });
    } catch (error) {
      console.error("send failure state:", JSON.stringify({
        composerCleared: (await input.inputValue()).trim() === "",
        refusals: await page.getByText(/refused|request has ended|Too many app requests|Connection to your computer was lost/).allTextContents(),
      }));
      throw error;
    }
    const deadline = Date.now() + 10_000;
    let persisted = false;
    while (Date.now() < deadline) {
      const history = await control("GET", "/sessions/phone-voice-test-440-solo/history?limit=10");
      // UI streaming can precede history persistence; the engine also wraps
      // the exact user text in its normal environment context.
      const request = history.history.findLastIndex((row) => row.role === "user" &&
        typeof row.content === "string" && row.content.includes(message));
      persisted = request >= 0 && history.history.slice(request + 1).some((row) =>
        row.role === "assistant" && typeof row.content === "string" && row.content.includes(marker));
      if (persisted) break;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    assert.ok(persisted, "the exact isolated session persisted this run's user message and assistant reply");
    console.log(`${browserName}: actual test agent received typed phone request and returned this run's unique marker`);
  }
  if (process.env.PHONE_VOICE_CHECK === "1") {
    const voice = await runRecordedVoiceCheck(page, control, {
      outputPrefix: process.env.PHONE_VOICE_OUTPUT_PREFIX,
      expectedPhrase: process.env.PHONE_VOICE_EXPECTED_PHRASE ?? "phone audio works",
      watchVoice,
    });
    console.log(`${browserName}: recorded speech through the real voice pipeline`, JSON.stringify(voice));
  }
} finally {
  try {
    try { await browser?.close(); }
    finally {
      try { await androidContext?.close(); }
      finally { await device?.close(); }
    }
  } finally {
    await control("DELETE", `/phonelink/phones/${offer.id}`);
    console.log("only the automated test phone was revoked");
  }
}
