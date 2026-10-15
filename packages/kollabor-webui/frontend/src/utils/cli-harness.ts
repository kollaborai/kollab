import type { Session } from "../api";

export type CliHarness = {
  brand: "codex" | "claude" | "gemini" | null;
  label: string;
};

/** A native API model is not a CLI, even when its model name is Claude or Gemini. */
export function cliHarness(session: Pick<Session, "provider" | "profile" | "harness">): CliHarness | null {
  if (session.provider && session.provider !== "acp") return null;
  const profile = session.profile || "";
  if (!session.harness && session.provider !== "acp" && !["codex-cli", "claude-code", "gemini-cli", "antigravity"].includes(profile)) return null;
  const name = (session.harness || "").toLowerCase();
  if (profile === "codex-cli" || /^codex\b/.test(name)) return { brand: "codex", label: "Codex CLI" };
  if (profile === "claude-code" || /^claude[- ]code\b/.test(name)) return { brand: "claude", label: "Claude Code CLI" };
  if (profile === "antigravity" || /^(google )?antigravity\b/.test(name)) return { brand: "gemini", label: "Antigravity CLI" };
  if (profile === "gemini-cli" || /^(google )?gemini\b/.test(name)) return { brand: "gemini", label: "Gemini CLI" };
  return { brand: null, label: "Agent CLI" };
}
