const DECLARATION_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

export function isPluginDeclarationId(value: unknown): value is string {
  return typeof value === "string" && DECLARATION_ID.test(value);
}

export function declaredIds(list: readonly unknown[] | undefined): Set<string> {
  const ids = new Set<string>();
  for (const item of list ?? []) {
    if (typeof item === "string") ids.add(item);
    else if (item && typeof item === "object" && !Array.isArray(item)) {
      for (const key of ["id", "name", "tool", "tag"] as const) {
        const value = (item as Record<string, unknown>)[key];
        if (typeof value === "string") ids.add(value);
      }
    }
  }
  return ids;
}
