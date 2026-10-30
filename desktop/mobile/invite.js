// The phone app's link screen opens only a fresh PhoneLink invite (#369). Same rules as the
// loader's phoneIdentity (packages/kollabor-webui/frontend/src/phonelink/phone-transport.ts).
export const PHONE_PATH = "/relay/v1/phone/";
const KEY = /^[0-9a-f]{64}$/;
const ID = /^[0-9a-f]{32}$/;

/** The invite in `text` (a scanned or pasted link), or null. `now` is Unix seconds. */
export function parseInvite(text, now) {
  let url;
  try {
    url = new URL(String(text ?? "").trim());
  } catch {
    return null;
  }
  // Nothing before the fragment but the directory: no query (not even a bare "?"), credentials or other path.
  if (url.protocol !== "https:" || url.href.split("#")[0] !== url.origin + PHONE_PATH) return null;

  const p = new URLSearchParams(url.hash.slice(1));
  const id = p.get("id") ?? "";
  const room = p.get("room") ?? "";
  const host = p.get("host") ?? "";
  if (p.get("v") !== "1" || !ID.test(id) || !KEY.test(room) || !KEY.test(host)) return null;

  const expires = Number(p.get("expires"));
  if (!Number.isSafeInteger(expires) || expires <= now || expires > now + 330) return null;

  return { url: url.href, directory: url.origin + PHONE_PATH };
}

/** The saved directory to open at launch, or null for the link screen (?relink, or nothing valid saved). */
export function launchDirectory(saved, search) {
  return saved && !new URLSearchParams(search).has("relink") && isDirectory(saved) ? saved : null;
}

/** True for a saved directory: https://<host>[:port]/relay/v1/phone/ and nothing else. */
export function isDirectory(value) {
  let url;
  try {
    url = new URL(String(value));
  } catch {
    return false;
  }
  return (
    url.protocol === "https:" &&
    url.pathname === PHONE_PATH &&
    url.search === "" &&
    url.hash === "" &&
    url.username === "" &&
    url.password === "" &&
    String(value) === url.origin + PHONE_PATH
  );
}
