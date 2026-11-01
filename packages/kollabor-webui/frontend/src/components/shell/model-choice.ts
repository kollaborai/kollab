import type { EngineApi, ModelChoice, Session } from "@/api";

/** Every typed word is part of the row's profile, model or effort: "codex luna max". */
export function matchesChoice(choice: ModelChoice, query: string): boolean {
  // A native row with no level is the model's own default; an ACP one keeps the harness's level.
  const effort = choice.effort || (choice.provider === "acp" ? "" : "default");
  const text = [choice.profile, choice.model, effort].join(" ").toLowerCase();
  return query.toLowerCase().split(/\s+/).every((word) => text.includes(word));
}

/** A pick that did not fully apply. The message says what runs, and ``session`` is that state. */
export class PickError extends Error {
  session?: Session;

  constructor(message: string, session?: Session) {
    super(message);
    this.session = session;
  }
}

const label = (state?: { model?: string; effort?: string }) =>
  [state?.model, state?.effort].filter(Boolean).join(" · ");

/**
 * The picker's label: the model a session runs, its level and its profile. An ACP harness owns its
 * model, so one it has not reported stays unnamed rather than showing the profile's saved one.
 */
export function choiceLabel(
  value: Pick<ModelChoice, "profile" | "model" | "effort">,
  profile?: { model?: string | null; provider?: string | null },
): string[] {
  const model = value.model || (profile?.provider === "acp" ? "" : profile?.model || "");
  return [model || value.profile, value.effort, model ? value.profile : ""];
}

/** The session the engine reports, with the pick laid over a record that can lag behind the harness,
 * unless its profile was switched elsewhere meanwhile: then it is the engine's as it is. */
const ours = (state: Session, profile: string | undefined, fields: { model?: string; effort?: string }): Session =>
  state.profile && state.profile !== profile ? state : { ...state, ...fields };

/** A switch whose answer is lost (a 502, a dropped connection) may still have applied: say what runs. */
async function switchProfile(api: EngineApi, ...args: Parameters<EngineApi["setSessionProfile"]>): Promise<Session> {
  try {
    return await api.setSessionProfile(...args);
  } catch (error) {
    const now = await api.getSession(args[0]).catch(() => undefined);
    if (!now) throw error;
    const failed = (error instanceof Error ? error.message : String(error)).replace(/\.?$/, ".");
    throw new PickError(`${failed} It now runs ${[now.profile, label(now)].filter(Boolean).join(" · ")}.`, now);
  }
}

/** Put the harness back on its earlier model and level: a model switch can move the level too. */
async function restore(api: EngineApi, id: string, before: { model: string; effort: string }): Promise<boolean> {
  try {
    if (!(await api.panelAction(id, "model", "select", { model: before.model })).ok) return false;
    return !before.effort || (await api.panelAction(id, "model", "effort", { level: before.effort })).ok;
  } catch {
    return false;
  }
}

/**
 * Puts a running session on a picked profile, model and effort, for this session only. A native
 * profile takes them as the session's own override; an ACP harness owns its model and effort, so
 * they go to the harness's options, as the Model tab sets them.
 */
export async function applyModelChoice(
  api: EngineApi,
  session: Pick<Session, "session_id" | "profile" | "model" | "effort">,
  choice: ModelChoice,
): Promise<Session> {
  if (choice.provider !== "acp") {
    const next = await switchProfile(
      api,
      session.session_id,
      choice.profile,
      choice.model || undefined,
      choice.effort || "", // "" is the model's own default: it clears an earlier pick
    );
    // An environment variable outranks a pick: say so rather than show it as applied.
    if ((choice.model && next.model !== choice.model) || (next.effort || "") !== (choice.effort || "")) {
      const env = choice.profile.trim().replace(/[^a-zA-Z0-9]/g, "_").toUpperCase();
      throw new PickError(
        `${choice.profile} kept ${[next.model, next.effort].filter(Boolean).join(" · ")}. ` +
          `KOLLAB_${env}_MODEL / KOLLAB_${env}_EFFORT and KOLLAB_MODEL / KOLLAB_EFFORT in the agent's environment outrank a pick.`,
        next, // the switch applied: the panel shows what runs
      );
    }
    return next;
  }
  const switched = session.profile !== choice.profile;
  if (switched) await switchProfile(api, session.session_id, choice.profile);
  const steps: [string, Record<string, string>][] = [];
  if (choice.model) steps.push(["select", { model: choice.model }]);
  if (choice.effort) steps.push(["effort", { level: choice.effort }]);
  let moved = false; // the harness took the picked model
  for (const [action, payload] of steps) {
    // A request that fails (a 502, a dropped connection) may still have applied.
    const result: { ok: boolean; message?: string; unsure?: boolean } = await api
      .panelAction(session.session_id, "model", action, payload)
      .catch((error: unknown) => ({ ok: false, message: error instanceof Error ? error.message : String(error), unsure: true }));
    if (result.ok) {
      moved ||= action === "select";
      continue;
    }
    const failed = (result.message || `${choice.profile} refused ${Object.values(payload)[0]}`).replace(/\.?$/, ".");
    if (!moved && !result.unsure && !switched) throw new Error(failed); // nothing changed
    // A model it has not run is offered at the levels of its last one: one it
    // refuses puts the model and level back, so the pick never half-applies.
    const before = switched ? undefined : { model: session.model || "", effort: session.effort || "" };
    if (before?.model && (await restore(api, session.session_id, before))) {
      throw new PickError(`${failed} It stays on ${label(before)}.`, ours(await api.getSession(session.session_id), session.profile, before));
    }
    // The switch applied even if no option did: say which harness runs now.
    const now = await api.getSession(session.session_id);
    const runs = [switched ? now.profile : "", label(now)].filter(Boolean).join(" · ");
    throw new PickError(`${failed} It now runs ${runs || "with no model named"}.`, now);
  }
  // The harness took both; the session record can lag behind it.
  const next = await api.getSession(session.session_id);
  return ours(next, choice.profile, { model: choice.model || next.model, effort: choice.effort || next.effort });
}

export const sameChoice = (a: Partial<ModelChoice>, b: Partial<ModelChoice>) =>
  a.profile === b.profile && (a.model || "") === (b.model || "") && (a.effort || "") === (b.effort || "");
