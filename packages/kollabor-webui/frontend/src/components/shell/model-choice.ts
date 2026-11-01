import type { EngineApi, ModelChoice, Session } from "@/api";

/** Every typed word is part of the row's profile, model or effort: "codex luna max". */
export function matchesChoice(choice: ModelChoice, query: string): boolean {
  // A native row with no level is the model's own default; an ACP one keeps the harness's level.
  const effort = choice.effort || (choice.provider === "acp" ? "" : "default");
  const text = [choice.profile, choice.model, effort].join(" ").toLowerCase();
  return query.toLowerCase().split(/\s+/).every((word) => text.includes(word));
}

/**
 * Puts a running session on a picked profile, model and effort, for this session only. A native
 * profile takes them as the session's own override; an ACP harness owns its model and effort, so
 * they go to the harness's options, as the Model tab sets them.
 */
export async function applyModelChoice(
  api: EngineApi,
  session: Pick<Session, "session_id" | "profile" | "model">,
  choice: ModelChoice,
): Promise<Session> {
  if (choice.provider !== "acp") {
    const next = await api.setSessionProfile(
      session.session_id,
      choice.profile,
      choice.model || undefined,
      choice.effort || "", // "" is the model's own default: it clears an earlier pick
    );
    // An environment variable outranks a pick: say so rather than show it as applied.
    if ((choice.model && next.model !== choice.model) || (next.effort || "") !== (choice.effort || "")) {
      const env = choice.profile.trim().replace(/[^a-zA-Z0-9]/g, "_").toUpperCase();
      throw new Error(
        `${choice.profile} kept ${[next.model, next.effort].filter(Boolean).join(" · ")}. ` +
          `KOLLAB_${env}_MODEL / KOLLAB_${env}_EFFORT and KOLLAB_MODEL / KOLLAB_EFFORT in the agent's environment outrank a pick.`,
      );
    }
    return next;
  }
  const switched = session.profile !== choice.profile;
  if (switched) await api.setSessionProfile(session.session_id, choice.profile);
  const steps: [string, Record<string, string>][] = [];
  if (choice.model) steps.push(["select", { model: choice.model }]);
  if (choice.effort) steps.push(["effort", { level: choice.effort }]);
  for (const [action, payload] of steps) {
    const result = await api.panelAction(session.session_id, "model", action, payload);
    if (result.ok) continue;
    const refused = (result.message || `${choice.profile} refused ${Object.values(payload)[0]}`).replace(/\.?$/, ".");
    if (action !== "effort" || !choice.model) throw new Error(refused);
    // A model it has not run is offered at the levels of its last one: one it
    // refuses puts the model back, so the pick never half-applies.
    const before = switched ? "" : session.model || "";
    const back = before && before !== choice.model
      ? await api.panelAction(session.session_id, "model", "select", { model: before })
      : { ok: false };
    throw new Error(back.ok ? `${refused} It stays on ${before}.` : `${refused} It runs ${choice.model} at the level it had.`);
  }
  // The harness took both; the session record can lag behind it.
  const next = await api.getSession(session.session_id);
  return { ...next, model: choice.model || next.model, effort: choice.effort || next.effort };
}

export const sameChoice = (a: Partial<ModelChoice>, b: Partial<ModelChoice>) =>
  a.profile === b.profile && (a.model || "") === (b.model || "") && (a.effort || "") === (b.effort || "");
