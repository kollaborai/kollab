// The only page code a connection directory serves. It holds no app UI: it
// opens the encrypted session to the user's computer, fetches the phone app
// from there, runs it only after the computer's signature checks out, and keeps
// the verified copy on this phone so later launches need no new code from anyone.
import { AppRefused, verifyFile, verifyManifest, type AppManifest } from "../app-verify";
import { hostFingerprint, phoneIdentity, PhoneTransport } from "../phone-transport";

const DB = "kollab-phonelink-app";
type Stored = { host: string; manifest: AppManifest & { protocol: string }; files: Record<string, Uint8Array> };

const shell = document.getElementById("kollab-phone-shell")!;
const text = (tag: string, value: string, style = "") => {
  const el = document.createElement(tag);
  el.textContent = value;
  el.style.cssText = style;
  return el;
};

function screen(message: string, extra: { code?: string; fingerprint?: string; retry?: boolean; alert?: boolean } = {}) {
  const box = text("main", "", "min-height:100dvh;display:grid;place-content:center;justify-items:center;gap:16px;padding:24px;text-align:center");
  box.append(text("h1", "Kollab", "font-size:18px;margin:0"));
  const p = text("p", message, "max-width:320px;margin:0;font-size:14px;color:#aab0b8");
  p.setAttribute("role", extra.alert ? "alert" : "status");
  box.append(p);
  if (extra.code) {
    box.append(text("small", "Pairing Code", "color:#aab0b8"));
    box.append(text("strong", extra.code, "font:600 24px ui-monospace,monospace;letter-spacing:.08em"));
  }
  if (extra.fingerprint) {
    box.append(text("small", "Your Computer's Fingerprint", "color:#aab0b8"));
    box.append(text("strong", extra.fingerprint, "font:600 15px ui-monospace,monospace;letter-spacing:.05em"));
    box.append(text("small", "Both screens should show the same code and fingerprint.", "color:#aab0b8;max-width:300px"));
  }
  if (extra.retry) {
    const button = text("button", "Try Again", "padding:10px 18px;border-radius:8px;border:1px solid #8a9099;background:none;color:inherit;font-size:14px");
    button.onclick = () => location.reload();
    box.append(button);
  }
  shell.replaceChildren(box);
}

function banner(message: string) {
  if (!message) return shell.replaceChildren();
  const bar = text("div", message, "position:fixed;left:0;right:0;bottom:0;padding:8px 16px;text-align:center;font-size:13px;background:#5a4300;color:#fff;z-index:2147483647");
  bar.setAttribute("role", "status");
  shell.replaceChildren(bar);
}

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(DB, 1);
    open.onupgradeneeded = () => open.result.createObjectStore("app");
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
}
async function readStored(host: string): Promise<Stored | null> {
  try {
    const db = await database();
    const value = await new Promise<Stored | undefined>((resolve, reject) => {
      const get = db.transaction("app").objectStore("app").get("current");
      get.onsuccess = () => resolve(get.result);
      get.onerror = () => reject(get.error);
    });
    return value && value.host === host ? value : null;
  } catch {
    return null;
  }
}
async function writeStored(value: Stored) {
  try {
    const db = await database();
    await new Promise<void>((resolve, reject) => {
      const put = db.transaction("app", "readwrite").objectStore("app").put(value, "current");
      put.onsuccess = () => resolve();
      put.onerror = () => reject(put.error);
    });
  } catch { /* The app still runs this time; it is fetched again next launch. */ }
}

async function download(transport: PhoneTransport, path: string, limit: number) {
  const response = await transport.fetch(path);
  if (!response.ok) throw new Error("Your computer could not send the app.");
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.length > limit) throw new AppRefused("The app from your computer was refused: it is too large. Nothing was run.");
  return bytes;
}

/** The computer's current app, verified; or the copy already on this phone when it is still the same. */
async function trustedApp(transport: PhoneTransport, host: string, stored: Stored | null): Promise<Stored> {
  const manifest = await verifyManifest(
    await (await transport.fetch("/phone-app/manifest.json")).json(),
    host,
    stored?.manifest.serial ?? 0,
  );
  if (stored && stored.manifest.bundle === manifest.bundle) {
    try {
      return await recheck(stored, host);
    } catch (error) {
      if (!(error instanceof AppRefused)) throw error; // damaged here: fetch it again below
    }
  }
  screen("Getting Kollab from your computer. This only takes a moment…");
  const files: Record<string, Uint8Array> = {};
  for (const entry of manifest.files) {
    files[entry.name] = await download(transport, "/phone-app/" + entry.name, entry.size);
    await verifyFile(manifest, entry.name, files[entry.name]);
  }
  const next = { host, manifest: manifest as Stored["manifest"], files };
  await writeStored(next);
  return next;
}

/** Stored bytes are checked again at every launch, not trusted because they are local. */
async function recheck(stored: Stored, host: string): Promise<Stored> {
  const manifest = await verifyManifest(stored.manifest, host);
  for (const entry of manifest.files) await verifyFile(manifest, entry.name, stored.files[entry.name] ?? new Uint8Array());
  return stored;
}

async function run(stored: Stored) {
  if (typeof DecompressionStream === "undefined")
    throw new Error("This browser is too old to open Kollab. Update it and try again.");
  const gz = new Blob([stored.files["phone-app.js.gz"] as BlobPart]).stream().pipeThrough(new DecompressionStream("gzip"));
  const script = await new Response(gz).blob();
  const style = document.createElement("style");
  style.textContent = new TextDecoder().decode(stored.files["phone-app.css"] ?? new Uint8Array());
  document.head.append(style);
  const element = document.createElement("script");
  element.src = URL.createObjectURL(script);
  document.head.append(element);
}

async function main() {
  try {
    const identity = await phoneIdentity();
    const fingerprint = await hostFingerprint(identity.host);
    let running = false;
    const transport = new PhoneTransport(identity, (state, code) => {
      if (running) return banner(
        state === "offline" || state === "connecting" ? "Reconnecting to your computer… Keep Kollab running there."
          : state === "temporary" ? "Connected for this window. Browser storage is unavailable, so this link cannot be remembered." : "");
      screen(
        state === "approval" ? "Approve this phone in PhoneLink on your computer. Check that the codes match." : "Connecting to your Kollab computer…",
        { code, fingerprint },
      );
    });
    const stored = await readStored(identity.host);
    let app: Stored;
    let offline = false;
    try {
      await transport.connect();
      app = await trustedApp(transport, identity.host, stored);
    } catch (error) {
      // Out of reach of the computer: the copy verified earlier still opens, and is checked again first.
      if (error instanceof AppRefused || !stored) throw error;
      app = await recheck(stored, identity.host);
      offline = true;
    }
    transport.installFetch();
    running = true;
    banner(offline ? "Reconnecting to your computer… Keep Kollab running there." : "");
    await run(app);
  } catch (error) {
    screen(error instanceof Error ? error.message : String(error), { retry: true, alert: true });
  }
}
void main();
