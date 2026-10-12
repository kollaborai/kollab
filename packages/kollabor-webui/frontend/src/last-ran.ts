/** How long ago an epoch-seconds time was: "5m Ago", "3h Ago", "3d Ago", "2mo Ago". */
export function since(epoch: number, now = Date.now()): string {
  const minutes = Math.max(1, Math.floor((now / 1000 - epoch) / 60));
  if (minutes < 60) return `${minutes}m Ago`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)}h Ago`;
  const days = Math.floor(minutes / (24 * 60));
  return days < 60 ? `${days}d Ago` : `${Math.floor(days / 30)}mo Ago`;
}

/** When a gem last ran in this folder (the engine's `last_ran`): "Ran 3d Ago". Empty if it never ran. */
export function lastRanLabel(lastRan: number | null | undefined, now = Date.now()): string {
  return lastRan ? `Ran ${since(lastRan, now)}` : "";
}
