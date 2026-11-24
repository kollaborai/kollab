import type { PluginRecord } from "./registry";
import type { PluginApi, RendererSpec } from "./sdk";
import { pluginsByEnableOrder } from "./plugin-order";

/** A renderer a loaded plugin declared, with that plugin's `k` and record. */
export type RendererMatch = { spec: RendererSpec; record: PluginRecord; k: PluginApi };

function findRenderer(
  plugins: readonly PluginRecord[],
  claims: (spec: RendererSpec) => boolean,
): RendererMatch | null {
  for (const record of pluginsByEnableOrder(plugins)) {
    if (!record.k) continue;
    const spec = record.contributions.renderers.find(claims);
    if (spec) return { spec, record, k: record.k };
  }
  return null;
}

/** The first enabled plugin's tool renderer for `tool`, or null. */
export function findToolRenderer(
  plugins: readonly PluginRecord[],
  tool: string,
): RendererMatch | null {
  if (tool === "request_permission") return null;
  return findRenderer(plugins, (spec) => spec.tool === tool);
}

/** The first enabled plugin's tag renderer for `tag`, or null. */
export function findTagRenderer(
  plugins: readonly PluginRecord[],
  tag: string,
): RendererMatch | null {
  return findRenderer(plugins, (spec) => spec.tag === tag);
}

/** Every tag name a loaded plugin renders, once each, in plugin order. */
export function tagRendererNames(plugins: readonly PluginRecord[]): string[] {
  const names = new Set<string>();
  for (const record of plugins) {
    if (!record.k) continue;
    for (const spec of record.contributions.renderers) {
      if (spec.tag) names.add(spec.tag);
    }
  }
  return [...names];
}
