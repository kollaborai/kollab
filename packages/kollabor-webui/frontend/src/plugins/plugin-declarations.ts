const DECLARATION_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const IDENTITY_FIELDS = ["id", "name", "tool", "tag"] as const;

export type PluginRendererKind = "tool" | "tag";
export type PluginRendererDeclaration = { kind: PluginRendererKind; name: string };

export function isPluginDeclarationId(value: unknown): value is string {
  return typeof value === "string" && DECLARATION_ID.test(value);
}

/** Keep tag declarations in step with the SDK's renderer registration grammar. */
export function isPluginRendererTagName(value: unknown): value is string {
  if (typeof value !== "string") return false;
  return /^[A-Za-z][A-Za-z0-9_-]*$/.exec(value)?.[0] === value;
}

export function hasSinglePluginDeclarationIdentity(
  value: Record<string, unknown>,
  required?: (typeof IDENTITY_FIELDS)[number],
): boolean {
  const identities = IDENTITY_FIELDS.filter((key) => value[key] !== undefined);
  return identities.length === 1 && (required === undefined || identities[0] === required);
}

export function declaredIds(list: readonly unknown[] | undefined): Set<string> {
  const ids = new Set<string>();
  for (const item of list ?? []) {
    if (typeof item === "string") ids.add(item);
    else if (item && typeof item === "object" && !Array.isArray(item)) {
      const row = item as Record<string, unknown>;
      if (!hasSinglePluginDeclarationIdentity(row)) continue;
      const key = IDENTITY_FIELDS.find((field) => row[field] !== undefined);
      const value = key ? row[key] : undefined;
      if (typeof value === "string") ids.add(value);
    }
  }
  return ids;
}

/** Renderer declarations must name their namespace; legacy untyped identities own neither kind. */
export function pluginRendererDeclaration(value: unknown): PluginRendererDeclaration | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (hasSinglePluginDeclarationIdentity(row, "tool") && isPluginDeclarationId(row.tool)) {
    return { kind: "tool", name: row.tool };
  }
  if (hasSinglePluginDeclarationIdentity(row, "tag") && isPluginRendererTagName(row.tag)) {
    return { kind: "tag", name: row.tag };
  }
  return null;
}

export function declaredRendererIds(
  list: readonly unknown[] | undefined,
  kind: PluginRendererKind,
): Set<string> {
  const ids = new Set<string>();
  for (const item of list ?? []) {
    const declaration = pluginRendererDeclaration(item);
    if (declaration?.kind === kind) ids.add(declaration.name);
  }
  return ids;
}
