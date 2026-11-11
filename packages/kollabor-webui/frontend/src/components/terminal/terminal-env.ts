// Pure helpers for the terminal header and its notices. No React, no fetch: the view owns those.

/** GET /sessions/{id}/code/terminal/env: the variables a new shell would not get, by reason. Names only, never values. */
export interface TerminalEnvReport {
  count: number;
  /** The shell the daemon resolved (its basename), whatever `terminal.shell` held. */
  shell?: string;
  hidden: {
    secret: string[];
    kollab: string[];
    denied: string[];
  };
}

/** The error code an engine answers with when the session's daemon predates Code, Git and Terminal. */
export const DAEMON_OUTDATED_CODE = "daemon_outdated";

const HIDDEN_REASONS: ReadonlyArray<readonly [keyof TerminalEnvReport["hidden"], string]> = [
  ["secret", "Secret names"],
  ["kollab", "Kollab names"],
  ["denied", "Denied by setting"],
];

/** "Env: 5 Hidden" for the header; "" when nothing is hidden or the report did not load. */
export function envHiddenLabel(report: TerminalEnvReport | null): string {
  if (!report || report.count <= 0) return "";
  return `Env: ${report.count} Hidden`;
}

/** One line per reason that hid something, then where to change it. "" when nothing is hidden. */
export function envHiddenTitle(report: TerminalEnvReport | null): string {
  if (!report || report.count <= 0) return "";
  const lines: string[] = [];
  for (const [key, label] of HIDDEN_REASONS) {
    const names = report.hidden[key];
    if (names.length > 0) lines.push(`${label} (${names.length}): ${names.join(", ")}`);
  }
  lines.push("Change in Settings: Code And Terminal");
  return lines.join("\n");
}

/** True for the HTTP error envelope `{ success: false, error: { code: "daemon_outdated" } }`. */
export function isDaemonOutdated(payload: unknown): boolean {
  if (!payload || typeof payload !== "object") return false;
  const error = (payload as { error?: unknown }).error;
  if (!error || typeof error !== "object") return false;
  return (error as { code?: unknown }).code === DAEMON_OUTDATED_CODE;
}

/** The shell's own name: "/bin/zsh" -> "zsh", "C:\\...\\cmd.exe" -> "cmd.exe". "" when there is no shell. */
export function shellName(shell: string): string {
  const parts = shell.trim().split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? "";
}
