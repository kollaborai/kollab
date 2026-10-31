import type { EngineApi, ModelChoice, Session } from "@/api";

/** Every typed word is part of the row's profile, model, effort or loadout: "codex luna max". */
export function matchesChoice(choice: ModelChoice, query: string): boolean {
  const text = [choice.loadout, choice.profile, choice.model, choice.effort || "default"].join(" ").toLowerCase();
  return query.toLowerCase().split(/\s+/).every((word) => text.includes(word));
}

/**
 * Puts a running session on a picked profile, model and effort, for this session only. A native
 * profile takes them as the session's own override; an ACP harness owns its model and effort, so
 * they go to the harness's options, as the Model tab sets them.
 */
export async function applyModelChoice(
  api: EngineApi,
  session: Pick<Session, "session_id" | "profile">,
  choice: ModelChoice,
): Promise<Session> {
  if (choice.provider !== "acp") {
    return api.setSessionProfile(session.session_id, choice.profile, choice.model || undefined, choice.effort || undefined);
  }
  if (session.profile !== choice.profile) await api.setSessionProfile(session.session_id, choice.profile);
  const steps: [string, Record<string, string>][] = [];
  if (choice.model) steps.push(["select", { model: choice.model }]);
  if (choice.effort) steps.push(["effort", { level: choice.effort }]);
  for (const [action, payload] of steps) {
    const result = await api.panelAction(session.session_id, "model", action, payload);
    if (!result.ok) throw new Error(result.message || `${choice.profile} refused ${Object.values(payload)[0]}.`);
  }
  return api.getSession(session.session_id);
}

export const sameChoice = (a: Partial<ModelChoice>, b: Partial<ModelChoice>) =>
  a.profile === b.profile && (a.model || "") === (b.model || "") && (a.effort || "") === (b.effort || "");
