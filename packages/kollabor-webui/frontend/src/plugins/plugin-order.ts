import type { PluginRecord } from "./registry";

/** Put loaded plugin claimants in backend enable order, preserving the API's order for ties and missing ranks. */
export function pluginsByEnableOrder(plugins: readonly PluginRecord[]): PluginRecord[] {
  const inputOrder = new Map(plugins.map((record, index) => [record.id, index]));
  return [...plugins].sort((a, b) => {
    const aOrder = a.entry.enable_order;
    const bOrder = b.entry.enable_order;
    const aRank = typeof aOrder === "number" && Number.isInteger(aOrder) ? aOrder : Number.POSITIVE_INFINITY;
    const bRank = typeof bOrder === "number" && Number.isInteger(bOrder) ? bOrder : Number.POSITIVE_INFINITY;
    if (aRank !== bRank) return aRank < bRank ? -1 : 1;
    const aListedOrder = a.listedOrder ?? inputOrder.get(a.id) ?? 0;
    const bListedOrder = b.listedOrder ?? inputOrder.get(b.id) ?? 0;
    return aListedOrder - bListedOrder;
  });
}
