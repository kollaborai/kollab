import type { ComposerPaletteItem } from "@/components/assistant-ui/composer-palette";
import type { PluginRecord } from "./registry";
import type { CommandSpec, PluginApi } from "./sdk";

/** A plugin command the composer can run. `k` is its owner's, for error toasts. */
export type PluginCommandEntry = {
  name: string;
  description: string;
  pluginId: string;
  run: CommandSpec["run"];
  k: PluginApi;
};

/** Native command names and aliases, lower case, without the slash. */
export function reservedCommandKeys(
  commands: readonly { name: string; aliases?: readonly string[] }[],
): Set<string> {
  const keys = new Set<string>();
  for (const command of commands) {
    for (const name of [command.name, ...(command.aliases ?? [])]) {
      keys.add(name.replace(/^\//, "").toLowerCase());
    }
  }
  return keys;
}

/** Commands from loaded plugins. A native name wins, then the first plugin in the list. */
export function pluginCommandEntries(
  plugins: readonly PluginRecord[],
  reserved: ReadonlySet<string>,
): PluginCommandEntry[] {
  const taken = new Set(reserved);
  const entries: PluginCommandEntry[] = [];
  for (const record of plugins) {
    if (!record.k) continue;
    for (const command of record.contributions.commands) {
      const key = command.name.toLowerCase();
      if (taken.has(key)) continue;
      taken.add(key);
      entries.push({
        name: command.name,
        description: command.description,
        pluginId: record.id,
        run: command.run,
        k: record.k,
      });
    }
  }
  return entries;
}

/** The plugin command a message runs (`/name args`), or null. Whole name, any case. */
export function matchPluginCommand(
  text: string,
  entries: readonly PluginCommandEntry[],
): { entry: PluginCommandEntry; args: string } | null {
  const match = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(text.trim());
  if (!match) return null;
  const name = (match[1] ?? "").toLowerCase();
  const entry = entries.find((candidate) => candidate.name.toLowerCase() === name);
  return entry ? { entry, args: (match[2] ?? "").trim() } : null;
}

/** Palette rows for plugin commands, in the "plugins" category. */
export function pluginCommandItems(
  entries: readonly PluginCommandEntry[],
  label: (name: string) => string,
): ComposerPaletteItem[] {
  return entries.map(
    (entry): ComposerPaletteItem => ({
      id: entry.name,
      label: label(entry.name),
      description: entry.description,
      type: "command",
      category: "plugins",
      insertText: entry.name,
    }),
  );
}

/** One-line toast text for a plugin command that threw or rejected. */
export function pluginCommandFailure(name: string, error: unknown): string {
  const raw = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  const detail = raw.replace(/\s+/g, " ").trim();
  return detail ? `Could not run /${name}: ${detail}` : `Could not run /${name}.`;
}
