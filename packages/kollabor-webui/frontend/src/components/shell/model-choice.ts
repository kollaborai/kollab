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

/** What a chat runs, as one comparable value: the three things a pick sets. */
export const runningKey = (session: Pick<Session, "profile" | "model" | "effort">) =>
  [session.profile, session.model, session.effort].map((part) => part ?? "").join("\n");

/** What a chat runs after a pick failed: the engine's read-back when it gave one, else the chat as it was. */
export const stateAfterPick = (reason: unknown, session: Session): Session =>
  reason instanceof PickError && reason.session ? reason.session : session;

const label = (state?: { model?: string; effort?: string }) =>
  [state?.model, state?.effort].filter(Boolean).join(" · ");

/** What a pick is called in a sentence: its model and level, or the profile when it names no model. */
export const pickLabel = (pick: Pick<ModelChoice, "profile" | "model" | "effort">) =>
  [pick.model || pick.profile, pick.effort].filter(Boolean).join(" · ");

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
  if (choice.model || choice.effort) {
    // One action: the agent takes the model and level as one change, one change at a time, and puts the
    // model back when it refuses the level, so a pick never half-applies or mixes with one made in
    // another window. It names its profile: the agent refuses it once a switch elsewhere moved the session.
    const pick = { model: choice.model, level: choice.effort, profile: choice.profile };
    const result: { ok: boolean; message?: string } = await api
      .panelAction(session.session_id, "model", "pick", pick)
      .catch((error: unknown) => ({ ok: false, message: error instanceof Error ? error.message : String(error) }));
    if (!result.ok) {
      // A request that fails (a 502, a dropped connection) may still have applied, and a switch did.
      const failed = (result.message || `${choice.profile} refused ${label(choice)}`).replace(/\.?$/, ".");
      const now = await api.getSession(session.session_id);
      const runs = [switched ? now.profile : "", label(now)].filter(Boolean).join(" · ");
      throw new PickError(`${failed} It now runs ${runs || "with no model named"}.`, now);
    }
  }
  // The engine reads the harness's options back before an action answers, so the record is what runs,
  // a newer pick made elsewhere meanwhile included.
  return api.getSession(session.session_id);
}

export const sameChoice = (a: Partial<ModelChoice>, b: Partial<ModelChoice>) =>
  a.profile === b.profile && (a.model || "") === (b.model || "") && (a.effort || "") === (b.effort || "");
