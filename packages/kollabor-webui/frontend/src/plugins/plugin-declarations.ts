const DECLARATION_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const IDENTITY_FIELDS = ["id", "name", "tool", "tag"] as const;

export function isPluginDeclarationId(value: unknown): value is string {
  return typeof value === "string" && DECLARATION_ID.test(value);
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
