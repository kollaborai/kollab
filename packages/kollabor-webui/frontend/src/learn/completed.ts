const KEY = "kollab.learn.completed";

type Store = Pick<Storage, "getItem" | "setItem">;

function store(): Store | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

/** Topic ids this viewer has finished; empty when storage is unavailable or corrupt. */
export function readCompleted(s: Store | null = store()): string[] {
  try {
    const parsed: unknown = JSON.parse(s?.getItem(KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

/** Returns the new list; the write is best effort so the menu still updates without storage. */
export function markCompleted(id: string, current: string[], s: Store | null = store()): string[] {
  if (current.includes(id)) return current;
  const next = [...current, id];
  try {
    s?.setItem(KEY, JSON.stringify(next));
  } catch {
    // storage blocked: completion lasts for this page view only
  }
  return next;
}
