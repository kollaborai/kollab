/**
 * Pure model for the System Prompts page: libraries, roles and the rows each
 * one lists. Kept free of React and browser APIs so the vitest suite can drive
 * every decision the page makes.
 */
import type {
  Composition,
  CompositionVariant,
  LibrarySection,
  PromptLibrary,
  PromptSection,
} from "@/prompts-api";

export const DEFAULTS_LIBRARY = "defaults";
export const ALL_VARIANT = "all";

/** What the page has open: a shared library, or a role that picks from them. */
export type PromptTarget = { kind: "library" | "role"; name: string };

/** One row of the middle list. `ref` is the key, or "library/key" for a user library. */
export type SectionRow = {
  id: string;
  key: string;
  title: string;
  tokens: number;
  /** "role": the role's own file; "library": a library section; "per-turn": always sent. */
  origin: "role" | "library" | "per-turn";
  library: string | null;
  ref: string;
  checkable: boolean;
  included: boolean;
  usedBy: number | null;
  edited: boolean;
  overridden: boolean;
};

export type SectionGroup = { id: string; name: string; library: string | null; rows: SectionRow[] };

const ACRONYMS: Record<string, string> = { mcp: "MCP", ai: "AI", llm: "LLM", api: "API", ui: "UI" };

/** "01-tool-workflow" -> "Tool Workflow"; "mcp" -> "MCP". */
export function titleFromKey(key: string): string {
  const words = key.replace(/^\d+[-_]/, "").split(/[-_\s]+/).filter(Boolean);
  if (!words.length) return key;
  return words
    .map((word) => ACRONYMS[word.toLowerCase()] ?? word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function libraryLabel(name: string): string {
  return name === DEFAULTS_LIBRARY ? "Defaults" : titleFromKey(name);
}

/** Folder under _base/sections that holds the file: "protocols" -> "Protocols"; none -> "Core". */
export function sectionFolder(sourcePath: string | null): string {
  if (!sourcePath) return "Core";
  const parts = sourcePath.split(/[\\/]/);
  const at = parts.lastIndexOf("sections");
  if (at < 0 || at >= parts.length - 2) return "Core";
  return titleFromKey(parts[at + 1]);
}

export function isPerTurn(section: { kind: string }): boolean {
  return section.kind === "runtime";
}

export function isBaseSection(sourcePath: string | null): boolean {
  return Boolean(sourcePath && /[\\/]_base[\\/]/.test(sourcePath));
}

export function variantOf(composition: Composition | null, variant: string): CompositionVariant {
  return composition?.variants[variant] ?? { exclude: [], add: [] };
}

export function variantLabel(name: string): string {
  if (name === ALL_VARIANT) return "All Models";
  if (name === "local") return "Local Models";
  return titleFromKey(name);
}

/** Variant names, All Models first. */
export function variantNames(composition: Composition | null): string[] {
  const names = Object.keys(composition?.variants ?? {}).filter((n) => n !== ALL_VARIANT);
  return [ALL_VARIANT, ...names.sort()];
}

function groupBy(rows: SectionRow[], name: (row: SectionRow) => string, library: string | null): SectionGroup[] {
  const groups: SectionGroup[] = [];
  for (const row of rows) {
    const label = name(row);
    let group = groups.find((g) => g.name === label);
    if (!group) {
      group = { id: `${library ?? "role"}:${label}`, name: label, library, rows: [] };
      groups.push(group);
    }
    group.rows.push(row);
  }
  return groups;
}

function libraryRow(
  library: PromptLibrary,
  section: LibrarySection,
  included: boolean,
  checkable: boolean,
): SectionRow {
  const perTurn = isPerTurn(section);
  const ref = library.builtin ? section.key : `${library.name}/${section.key}`;
  return {
    id: `${library.name}:${section.key}`,
    key: section.key,
    title: library.builtin ? titleFromKey(section.key) : section.title || titleFromKey(section.key),
    tokens: section.tokens,
    origin: perTurn ? "per-turn" : "library",
    library: library.name,
    ref,
    checkable: checkable && !perTurn,
    included: perTurn || included,
    usedBy: perTurn ? null : section.used_by,
    edited: section.edited,
    overridden: false,
  };
}

/** A library's own list: its sections by folder, the per-turn context last. */
export function libraryGroups(library: PromptLibrary): SectionGroup[] {
  const rows = library.sections.map((s) => libraryRow(library, s, true, false));
  const shared = rows.filter((r) => r.origin !== "per-turn");
  const folders = new Map(library.sections.map((s) => [s.key, sectionFolder(s.source_path)]));
  const groups = library.builtin
    ? groupBy(shared, (r) => folders.get(r.key) ?? "Core", library.name)
    : [{ id: `${library.name}:all`, name: "Sections", library: library.name, rows: shared }];
  const perTurn = rows.filter((r) => r.origin === "per-turn");
  if (perTurn.length) groups.push({ id: "per-turn", name: "Per-Turn Context", library: null, rows: perTurn });
  return groups;
}

/**
 * A role's checklist, in the order the prompt renders: its own sections,
 * every library section (ticked when the variant sends it), then the
 * per-turn context that every role gets.
 */
export function roleGroups(
  roleSections: readonly PromptSection[],
  libraries: readonly PromptLibrary[],
  variant: CompositionVariant,
): SectionGroup[] {
  const own: SectionRow[] = roleSections
    .filter((s) => !isBaseSection(s.source_path) && !isPerTurn(s) && s.source_path)
    .map((s) => ({
      id: `role:${s.key}`,
      key: s.key,
      title: titleFromKey(s.key) === "Header" ? "Identity" : titleFromKey(s.key),
      tokens: s.tokens,
      origin: "role",
      library: null,
      ref: s.key,
      checkable: false,
      included: true,
      usedBy: null,
      edited: false,
      overridden: s.overridden,
    }));
  const overridden = new Set(roleSections.filter((s) => s.overridden).map((s) => s.key));
  const groups: SectionGroup[] = [];
  if (own.length) groups.push({ id: "role", name: "Role Sections", library: null, rows: own });
  let perTurn: SectionRow[] = [];
  for (const library of libraries) {
    const rows = library.sections.map((s) => {
      const ref = library.builtin ? s.key : `${library.name}/${s.key}`;
      const included = library.builtin ? !variant.exclude.includes(s.key) : variant.add.includes(ref);
      const row = libraryRow(library, s, included, true);
      return { ...row, overridden: library.builtin && overridden.has(s.key) };
    });
    perTurn = perTurn.concat(rows.filter((r) => r.origin === "per-turn"));
    const shared = rows.filter((r) => r.origin !== "per-turn");
    const folders = new Map(library.sections.map((s) => [s.key, sectionFolder(s.source_path)]));
    for (const group of library.builtin
      ? groupBy(shared, (r) => folders.get(r.key) ?? "Core", library.name)
      : [{ id: `${library.name}:all`, name: "Sections", library: library.name, rows: shared }]) {
      if (group.rows.length) groups.push(group);
    }
  }
  if (perTurn.length) groups.push({ id: "per-turn", name: "Per-Turn Context", library: null, rows: perTurn });
  return groups;
}

/** The composition with one row ticked or unticked in one variant. */
export function toggleRow(
  composition: Composition | null,
  variant: string,
  row: SectionRow,
  include: boolean,
): Composition {
  const current = variantOf(composition, variant);
  const next: CompositionVariant = { ...current, exclude: [...current.exclude], add: [...current.add] };
  if (row.library === DEFAULTS_LIBRARY) {
    next.exclude = include ? next.exclude.filter((k) => k !== row.key) : [...new Set([...next.exclude, row.key])];
  } else {
    next.add = include ? [...new Set([...next.add, row.ref])] : next.add.filter((r) => r !== row.ref);
  }
  return { variants: { ...(composition?.variants ?? {}), [variant]: next } };
}

/** Sections and tokens the variant sends from the libraries (role files not counted). */
export function includedTotals(groups: readonly SectionGroup[]): { sections: number; tokens: number } {
  const rows = groups.flatMap((g) => g.rows).filter((r) => r.included);
  return { sections: rows.length, tokens: rows.reduce((n, r) => n + (r.tokens || 0), 0) };
}

/** True when the editor text differs from the section's stored content. */
export function isDirty(draft: string, stored: string | null): boolean {
  return draft !== (stored ?? "");
}

/** Line diff of the draft against what is saved: removed lines first, then the draft. */
export function diffLines(before: string, after: string): { mark: "+" | "-" | " "; text: string }[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const inA = new Set(a);
  const inB = new Set(b);
  return [
    ...a.filter((line) => !inB.has(line)).map((text) => ({ mark: "-" as const, text })),
    ...b.map((text) => ({ mark: inA.has(text) ? (" " as const) : ("+" as const), text })),
  ];
}
