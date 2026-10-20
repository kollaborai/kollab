/**
 * Sidebar agent order saved by the engine (engine: sidebar_order.py). Local
 * persona keys use workspace + identity; duplicate live rows add stable session
 * IDs while retaining the persona key as an offline/restart fallback. Remote
 * keys add network + device scope to keep identically named agents independent.
 * The saved order ranks rows inside a folder and folders by their first-ranked
 * row; unsaved rows follow arrival order.
 */
export type OrderRow = {
  key: string;
  folder: string;
  fallbackKeys?: string[];
  persistAfter?: string[];
};
export type FolderGroup<T extends OrderRow> = { folder: string; rows: T[] };

export function orderKey(
  folder: string | null | undefined,
  name: string | null | undefined,
  scope?: readonly string[],
): string {
  const parts = ["pos", ...(scope?.length ? scope : []), folder || "", (name || "").trim().toLowerCase()];
  return JSON.stringify(parts);
}

/** Scope duplicate rows by API session ID; persona keys remain durable fallbacks. */
export function localSessionOrderKey(
  folder: string | null | undefined,
  name: string | null | undefined,
  sessionId: string,
): string {
  return orderKey(folder, name, ["session", sessionId]);
}

/** Durable conversation discriminator, when a history identity is available. */
export function localConversationOrderKey(
  folder: string | null | undefined,
  name: string | null | undefined,
  conversationId: string,
): string {
  return orderKey(folder, name, ["conversation", conversationId]);
}

/** Distinguish an offline persona row if stale API data overlaps a live row. */
export function localOfflineOrderKey(folder: string | null | undefined, name: string | null | undefined): string {
  return orderKey(folder, name, ["offline"]);
}

export type LocalSessionOrderSource = {
  session_id: string;
  conversation_id?: string | null;
  identity?: string | null;
  agent?: string | null;
  workspace?: string | null;
};

export type LocalOfflineOrderSource = {
  identity: string;
  workspace?: string | null;
};

export type LocalOrderRow<Session, Offline> = OrderRow & {
  session?: Session;
  offline?: Offline;
};

/** Build unique local row keys while retaining a persona anchor for offline/restarted rows. */
export function buildLocalOrderRows<Session extends LocalSessionOrderSource, Offline extends LocalOfflineOrderSource>(
  sessions: Session[],
  offline: Offline[],
  saved: string[],
): LocalOrderRow<Session, Offline>[] {
  const counts = new Map<string, number>();
  const identityKey = (folder: string, identity: string) =>
    JSON.stringify([folder, identity.trim().toLowerCase()]);
  const count = (folder: string, identity: string) => {
    const key = identityKey(folder, identity);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  };
  const identityOf = (session: Session) => session.identity || session.agent || session.session_id;
  sessions.forEach((session) => count(session.workspace || "", identityOf(session)));
  offline.forEach((agent) => count(agent.workspace || "", agent.identity));

  const sessionRows = sessions.map((session) => {
    const folder = session.workspace || "";
    const identity = identityOf(session);
    const personaKey = orderKey(folder, identity);
    const sessionKey = localSessionOrderKey(folder, identity, session.session_id);
    const conversationId = session.conversation_id?.trim();
    const conversationKey = conversationId
      ? localConversationOrderKey(folder, identity, conversationId)
      : null;
    const needsSessionKey =
      (counts.get(identityKey(folder, identity)) ?? 0) > 1 ||
      saved.includes(sessionKey) ||
      (conversationKey !== null && saved.includes(conversationKey));
    const aliases = needsSessionKey
      ? [...new Set([...(conversationKey ? [conversationKey] : []), personaKey])].filter((key) => key !== sessionKey)
      : [];
    return {
      key: needsSessionKey ? sessionKey : personaKey,
      folder,
      session,
      ...(aliases.length ? { fallbackKeys: aliases, persistAfter: aliases } : {}),
    };
  });

  const offlineRows = offline.map((agent) => {
    const folder = agent.workspace || "";
    const personaKey = orderKey(folder, agent.identity);
    const offlineKey = localOfflineOrderKey(folder, agent.identity);
    const needsOfflineKey =
      (counts.get(identityKey(folder, agent.identity)) ?? 0) > 1 || saved.includes(offlineKey);
    return {
      key: needsOfflineKey ? offlineKey : personaKey,
      folder,
      offline: agent,
      ...(needsOfflineKey && offlineKey !== personaKey
        ? { fallbackKeys: [personaKey], persistAfter: [personaKey] }
        : {}),
    };
  });
  return [...sessionRows, ...offlineRows];
}

export function workspaceName(folder: string): string {
  return folder.replace(/\\/g, "/").split("/").filter(Boolean).pop() || "No Folder";
}

export function groupByFolder<T extends OrderRow>(rows: T[], order: string[]): FolderGroup<T>[] {
  const rank = new Map(order.map((key, index) => [key, index]));
  const at = (row: T) => {
    const exact = rank.get(row.key);
    if (exact !== undefined) return exact;
    for (const fallbackKey of row.fallbackKeys ?? []) {
      const fallbackRank = rank.get(fallbackKey);
      if (fallbackRank !== undefined) return fallbackRank;
    }
    return Number.MAX_SAFE_INTEGER;
  };
  const sorted = rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => at(a.row) - at(b.row) || a.index - b.index)
    .map(({ row }) => row);
  const groups = new Map<string, T[]>();
  for (const row of sorted) groups.set(row.folder, [...(groups.get(row.folder) ?? []), row]);
  return [...groups].map(([folder, list]) => ({ folder, rows: list }));
}

/** The order to save after `key` moves one place within its folder; null when it cannot move. */
export function moveWithinFolder<T extends OrderRow>(
  groups: FolderGroup<T>[],
  key: string,
  by: -1 | 1,
  saved: string[],
): string[] | null {
  const group = groups.find((g) => g.rows.some((row) => row.key === key));
  if (!group) return null;
  const from = group.rows.findIndex((row) => row.key === key);
  const to = from + by;
  if (to < 0 || to >= group.rows.length) return null;
  const rows = [...group.rows];
  [rows[from], rows[to]] = [rows[to], rows[from]];
  const persistedAliases = new Set<string>();
  const persistRows = (items: T[]) => items.flatMap((row) => {
    const keys = [row.key];
    for (const alias of row.persistAfter ?? []) {
      if (alias !== row.key && !persistedAliases.has(alias)) {
        persistedAliases.add(alias);
        keys.push(alias);
      }
    }
    return keys;
  });
  // Persona anchors follow the first visible session in their group. Hidden
  // rows and aliases retain their saved spot behind the shown keys.
  const shown = groups.flatMap((g) => persistRows(g === group ? rows : g.rows));
  const shownKeys = new Set(shown);
  return [...shown, ...saved.filter((savedKey) => !shownKeys.has(savedKey))];
}
