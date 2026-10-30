// The phone app's start page (#369): link this phone by scanning or pasting the PhoneLink QR link,
// then hand the webview to the loader. Later launches go straight to the saved directory.
import { launchDirectory, parseInvite } from "./invite.js";

const DIRECTORY = "kollab.phone.directory";
const NOT_PHONELINK = "This is not a PhoneLink link. Copy it from Settings\u00a0→\u00a0PhoneLink on your computer.";

const root = document.getElementById("app");
const scanner = (command, args) => window.__TAURI_INTERNALS__.invoke("plugin:barcode-scanner|" + command, args);

let scanning = false;

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text) el.textContent = text;
  if (className) el.className = className;
  return el;
}

function button(label, className, onClick) {
  const el = node("button", label, className);
  el.type = "button";
  el.onclick = onClick;
  return el;
}

function show(...children) {
  const main = node("main");
  main.append(...children);
  root.replaceChildren(main);
}

function linkScreen() {
  show(
    node("h1", "Kollab"),
    node("p", "Open Settings\u00a0→\u00a0PhoneLink on your computer, then scan its QR code."),
    button("Scan QR Code", "fill", startScan),
    button("Paste Link Instead", "link", () => pasteScreen())
  );
}

function pasteScreen(value = "", error = "") {
  const field = document.createElement("textarea");
  field.id = "link";
  field.value = value;
  field.setAttribute("aria-label", "PhoneLink link");
  field.setAttribute("autocapitalize", "off");
  field.setAttribute("autocomplete", "off");
  field.setAttribute("spellcheck", "false");
  if (error) field.className = "bad";
  const note = node("p", error, "err");
  note.setAttribute("role", "alert");
  show(
    node("h1", "Kollab"),
    node("p", "Paste the link from Settings\u00a0→\u00a0PhoneLink on your computer."),
    field,
    ...(error ? [note] : []),
    button("Link This Phone", "fill", () => openInvite(field.value)),
    button("Scan QR Code Instead", "link", startScan)
  );
}

function scanScreen() {
  document.documentElement.classList.add("scanning");
  const finder = node("div", "", "finder");
  finder.append(node("i"), node("i"), node("i"), node("i"));
  show(
    finder,
    node("p", "Point at the QR code in Settings\u00a0→\u00a0PhoneLink.", "hint"),
    button("Cancel", "over", cancelScan)
  );
}

function endScan() {
  scanning = false;
  document.documentElement.classList.remove("scanning");
}

async function startScan() {
  if (scanning) return; // a second tap while the camera starts
  scanning = true;
  try {
    let state = (await scanner("check_permissions")).camera;
    if (state !== "granted") state = (await scanner("request_permissions")).camera;
    if (state !== "granted") throw new Error(state);
  } catch {
    endScan();
    return pasteScreen();
  }
  scanScreen();
  let result;
  try {
    result = await scanner("scan", { windowed: true, formats: ["QR_CODE"] });
  } catch {
    const cancelled = !scanning;
    endScan();
    return cancelled ? linkScreen() : pasteScreen();
  }
  if (!scanning) return;
  endScan();
  openInvite(result?.content ?? "");
}

async function cancelScan() {
  endScan();
  linkScreen();
  try { await scanner("cancel"); } catch { /* already stopped */ }
}

// A directory the phone cannot reach (offline, or a certificate it does not trust) would leave it on the
// webview's error page, with no way back here, so every link and launch checks it first.
async function reachable(directory) {
  show(node("h1", "Kollab"), node("p", "Connecting to your Kollab computer…"));
  try {
    await fetch(directory + "status", { mode: "no-cors", cache: "no-store" });
    return true;
  } catch {
    return false;
  }
}

const cantReach = (directory) =>
  `Kollab can't reach ${new URL(directory).host}. Check this phone's internet connection, then try again.`;

async function openInvite(text) {
  const invite = parseInvite(text, Date.now() / 1000);
  if (!invite) return pasteScreen(String(text ?? "").trim(), NOT_PHONELINK);
  if (!(await reachable(invite.directory))) return pasteScreen(String(text).trim(), cantReach(invite.directory));
  try { localStorage.setItem(DIRECTORY, invite.directory); } catch { /* the link still opens now */ }
  location.replace(invite.url);
}

async function resume(directory) {
  if (await reachable(directory)) return location.replace(directory);
  show(
    node("h1", "Kollab"),
    node("p", cantReach(directory)),
    button("Scan QR Code", "fill", startScan),
    button("Try Again", "", () => resume(directory))
  );
}

let saved = null;
try { saved = localStorage.getItem(DIRECTORY); } catch { /* no saved directory */ }
const directory = launchDirectory(saved, location.search);
if (directory) resume(directory);
else linkScreen();
