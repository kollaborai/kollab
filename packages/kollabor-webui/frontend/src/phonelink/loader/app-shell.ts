// Inside the Kollab phone app (#369) the app puts a relink hook on every page it opens
// (desktop/src-tauri/src/lib.rs), so a loader error can send the phone back to the
// app's own link screen to scan a new QR code.
type AppShell = { relink?: unknown };

/** The phone app's relink action, or null outside the app. */
export function appRelink(): (() => void) | null {
  const app = (globalThis as { __KOLLAB_APP__?: AppShell }).__KOLLAB_APP__;
  const relink = app?.relink;
  return typeof relink === "function" ? () => void relink.call(app) : null;
}
