import type { PluginRecord } from "./registry";
import type { PluginApi, RendererSpec } from "./sdk";
import { pluginsByEnableOrder } from "./plugin-order";
import { declaredIds, hasSinglePluginDeclarationIdentity } from "./plugin-declarations";

/** A renderer a loaded plugin declared, with that plugin's `k` and record. */
export type RendererMatch = { spec: RendererSpec; record: PluginRecord; k: PluginApi };

type RendererKind = "tool" | "tag";

function rendererOwnerKey(kind: RendererKind, name: string): string {
  return `${kind}\0${name}`;
}

function declaredRendererKeys(list: readonly unknown[] | undefined): Set<string> {
  const keys = new Set<string>();
  const claimBothKinds = (name: string) => {
    keys.add(rendererOwnerKey("tool", name));
    keys.add(rendererOwnerKey("tag", name));
  };
  for (const item of list ?? []) {
    if (typeof item === "string") {
      claimBothKinds(item);
      continue;
    }
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const declaration = item as Record<string, unknown>;
    if (!hasSinglePluginDeclarationIdentity(declaration)) continue;
    if (typeof declaration.tool === "string") keys.add(rendererOwnerKey("tool", declaration.tool));
    else if (typeof declaration.tag === "string") keys.add(rendererOwnerKey("tag", declaration.tag));
    else for (const name of declaredIds([declaration])) claimBothKinds(name);
  }
  return keys;
}

function findRenderer(
  plugins: readonly PluginRecord[],
  kind: RendererKind,
  name: string,
  claims: (spec: RendererSpec) => boolean,
): RendererMatch | null {
  const ordered = pluginsByEnableOrder(plugins);
  const ownerKey = rendererOwnerKey(kind, name);
  const owner = ordered.find((record) => declaredRendererKeys(record.entry.contributes?.renderers).has(ownerKey));
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
  return findRenderer(plugins, "tool", tool, (spec) => spec.tool === tool);
}

/** The first enabled plugin's tag renderer for `tag`, or null. */
export function findTagRenderer(
  plugins: readonly PluginRecord[],
  tag: string,
): RendererMatch | null {
  return findRenderer(plugins, "tag", tag, (spec) => spec.tag === tag);
}

/** Every tag name a loaded plugin renders, once each, in plugin order. */
export function tagRendererNames(plugins: readonly PluginRecord[]): string[] {
  const ordered = pluginsByEnableOrder(plugins);
  const owners = new Map<string, PluginRecord>();
  for (const record of ordered) {
    for (const key of declaredRendererKeys(record.entry.contributes?.renderers)) {
      if (!key.startsWith(rendererOwnerKey("tag", ""))) continue;
      if (!owners.has(key)) owners.set(key, record);
    }
  }
  const names = new Set<string>();
  for (const record of ordered) {
    if (!record.k) continue;
    for (const spec of record.contributions.renderers) {
      if (spec.tag && owners.get(rendererOwnerKey("tag", spec.tag))?.id === record.id) names.add(spec.tag);
    }
  }
  return [...names];
}
