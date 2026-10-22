import sodium from "libsodium-wrappers";

/** What the phone checks before it runs any page code the computer sends. */
export const APP_PROTOCOL = "kollab-phonelink-app/1";
const KEY = /^[0-9a-f]{64}$/;
const NAME = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const SHA = /^[0-9a-f]{64}$/;
export type AppFile = { name: string; sha256: string; size: number };
export type AppManifest = {
  serial: number;
  files: AppFile[];
  bundle: string;
  host: string;
  signature: string;
};

const hex = (bytes: Uint8Array) => sodium.to_hex(bytes);
const sha256 = async (bytes: Uint8Array) =>
  hex(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes))));

export class AppRefused extends Error {}

/**
 * Accept a manifest only when the host key from the QR signed its file list.
 * The bundle hash is recomputed here, never taken from the sender.
 */
export async function verifyManifest(raw: unknown, hostKey: string, minSerial = 0): Promise<AppManifest> {
  await sodium.ready;
  const refuse = (why: string) => new AppRefused(`The app from your computer was refused: ${why}. Nothing was run.`);
  const m = raw as Partial<AppManifest> & { protocol?: string };
  if (!m || typeof m !== "object" || m.protocol !== APP_PROTOCOL) throw refuse("it is not a Kollab phone app");
  if (m.host !== hostKey || !KEY.test(hostKey)) throw refuse("it names a different computer");
  if (!Number.isSafeInteger(m.serial) || !Array.isArray(m.files) || m.files.length < 1 || m.files.length > 8)
    throw refuse("its file list is malformed");
  for (const f of m.files)
    if (!f || !NAME.test(f.name) || !SHA.test(f.sha256) || !Number.isSafeInteger(f.size) || f.size < 0)
      throw refuse("its file list is malformed");
  const listing = `${m.serial}\n` + m.files.map((f) => `${f.name} ${f.sha256} ${f.size}`).join("\n");
  const bundle = await sha256(sodium.from_string(listing));
  const message = sodium.from_string(`${APP_PROTOCOL}\n${hostKey}\n${bundle}`);
  let signature: Uint8Array;
  try {
    signature = sodium.from_hex(String(m.signature));
  } catch {
    throw refuse("its signature is malformed");
  }
  if (signature.length !== 64 || !sodium.crypto_sign_verify_detached(signature, message, sodium.from_hex(hostKey)))
    throw refuse("its signature does not match your computer's key");
  if (m.serial! < minSerial) throw refuse("it is older than the app already on this phone");
  return { ...(m as AppManifest), bundle };
}

/** A file is trusted only when its hash is the one the signed list names. */
export async function verifyFile(manifest: AppManifest, name: string, bytes: Uint8Array) {
  const entry = manifest.files.find((f) => f.name === name);
  if (!entry || entry.size !== bytes.length || (await sha256(bytes)) !== entry.sha256)
    throw new AppRefused(`The app file ${name} did not match its signature. Nothing was run.`);
}
