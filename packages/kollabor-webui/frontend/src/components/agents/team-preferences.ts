export type TeamSelectionPreference = { members: string[]; lead: string };
export type TeamSelectionPreferences = Record<string, TeamSelectionPreference>;

type StorageLike = Pick<Storage, "getItem" | "setItem">;

export const TEAM_SELECTION_STORAGE_KEY = "kollab.team-launch-selection.v1";

/** Folder and team together form one saved selection; the empty folder is the engine default. */
export function teamSelectionKey(folder: string, team: string): string {
  return JSON.stringify([folder, team]);
}

function browserStorage(): StorageLike | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** Read bounded, validated browser preferences; broken or unavailable storage is non-fatal. */
export function readTeamSelectionPreferences(storage: StorageLike | null = browserStorage()): TeamSelectionPreferences {
  if (!storage) return {};
  try {
    const parsed: unknown = JSON.parse(storage.getItem(TEAM_SELECTION_STORAGE_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const preferences: TeamSelectionPreferences = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (!value || typeof value !== "object" || Array.isArray(value)) continue;
      const candidate = value as { members?: unknown; lead?: unknown };
      if (!Array.isArray(candidate.members) || !candidate.members.every((name) => typeof name === "string")) continue;
      if (typeof candidate.lead !== "string") continue;
      preferences[key] = { members: [...new Set(candidate.members)], lead: candidate.lead };
    }
    return preferences;
  } catch {
    return {};
  }
}

/** Persist only member/lead choices (not mission text); false means storage was unavailable. */
export function writeTeamSelectionPreferences(
  preferences: TeamSelectionPreferences,
  storage: StorageLike | null = browserStorage(),
): boolean {
  if (!storage) return false;
  try {
    storage.setItem(TEAM_SELECTION_STORAGE_KEY, JSON.stringify(preferences));
    return true;
  } catch {
    return false;
  }
}
