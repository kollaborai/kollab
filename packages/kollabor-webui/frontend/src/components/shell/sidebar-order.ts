/**
 * Sidebar agent order saved by the engine (engine: sidebar_order.py). Local
 * keys use workspace + identity so they survive process restarts. Remote keys
 * add network + device scope to keep identically named agents independent.
 * The saved order ranks rows inside a folder and folders by their first-ranked
 * row; unsaved rows follow arrival order.
 */
export type OrderRow = { key: string; folder: string };
export type FolderGroup<T extends OrderRow> = { folder: string; rows: T[] };

export function orderKey(
  folder: string | null | undefined,
  name: string | null | undefined,
  scope?: readonly string[],
): string {
  const parts = ["pos", ...(scope?.length ? scope : []), folder || "", (name || "").trim().toLowerCase()];
  return JSON.stringify(parts);
}

export function workspaceName(folder: string): string {
  return folder.replace(/\\/g, "/").split("/").filter(Boolean).pop() || "No Folder";
}

export function groupByFolder<T extends OrderRow>(rows: T[], order: string[]): FolderGroup<T>[] {
  const rank = new Map(order.map((key, index) => [key, index]));
  const at = (row: T) => rank.get(row.key) ?? Number.MAX_SAFE_INTEGER;
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
  const shown = groups.flatMap((g) => (g === group ? rows : g.rows).map((row) => row.key));
  // Hidden rows keep their saved spot behind the shown ones.
  const shownKeys = new Set(shown);
  return [...shown, ...saved.filter((savedKey) => !shownKeys.has(savedKey))];
}
