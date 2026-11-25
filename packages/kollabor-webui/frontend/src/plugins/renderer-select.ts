import type { PluginRecord } from "./registry";
import type { PluginApi, RendererSpec } from "./sdk";
import { pluginsByEnableOrder } from "./plugin-order";
import { declaredIds } from "./plugin-declarations";

/** A renderer a loaded plugin declared, with that plugin's `k` and record. */
export type RendererMatch = { spec: RendererSpec; record: PluginRecord; k: PluginApi };

function findRenderer(
  plugins: readonly PluginRecord[],
  name: string,
  claims: (spec: RendererSpec) => boolean,
): RendererMatch | null {
  const ordered = pluginsByEnableOrder(plugins);
  const owner = ordered.find((record) => declaredIds(record.entry.contributes?.renderers).has(name));
  if (!owner?.k) return null;
  const spec = owner.contributions.renderers.find(claims);
  return spec ? { spec, record: owner, k: owner.k } : null;
}

/** The first enabled plugin's tool renderer for `tool`, or null. */
export function findToolRenderer(
  plugins: readonly PluginRecord[],
  tool: string,
): RendererMatch | null {
  if (tool === "request_permission") return null;
  return findRenderer(plugins, tool, (spec) => spec.tool === tool);
}

/** The first enabled plugin's tag renderer for `tag`, or null. */
export function findTagRenderer(
  plugins: readonly PluginRecord[],
  tag: string,
): RendererMatch | null {
  return findRenderer(plugins, tag, (spec) => spec.tag === tag);
}

/** Every tag name a loaded plugin renders, once each, in plugin order. */
export function tagRendererNames(plugins: readonly PluginRecord[]): string[] {
  const ordered = pluginsByEnableOrder(plugins);
  const owners = new Map<string, PluginRecord>();
  for (const record of ordered) {
    for (const name of declaredIds(record.entry.contributes?.renderers)) {
      if (!owners.has(name)) owners.set(name, record);
    }
  }
  const names = new Set<string>();
  for (const record of ordered) {
    if (!record.k) continue;
    for (const spec of record.contributions.renderers) {
      if (spec.tag && owners.get(spec.tag)?.id === record.id) names.add(spec.tag);
    }
  }
  return [...names];
}
