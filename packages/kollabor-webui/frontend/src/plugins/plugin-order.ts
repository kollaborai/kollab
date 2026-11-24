import type { PluginRecord } from "./registry";

/** Put loaded plugin claimants in backend enable order; name order only breaks missing or tied order values. */
export function pluginsByEnableOrder(plugins: readonly PluginRecord[]): PluginRecord[] {
  return [...plugins].sort((a, b) => {
    const aOrder = a.entry.enable_order;
    const bOrder = b.entry.enable_order;
    const aRank = typeof aOrder === "number" && Number.isFinite(aOrder) ? aOrder : Number.POSITIVE_INFINITY;
    const bRank = typeof bOrder === "number" && Number.isFinite(bOrder) ? bOrder : Number.POSITIVE_INFINITY;
    if (aRank !== bRank) return aRank < bRank ? -1 : 1;
    return a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) ||
      (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  });
}
