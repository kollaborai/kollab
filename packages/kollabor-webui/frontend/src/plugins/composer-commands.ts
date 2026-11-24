import type { ComposerPaletteItem } from "@/components/assistant-ui/composer-palette";
import type { PluginRecord } from "./registry";
import type { CommandSpec, PluginApi } from "./sdk";
import { pluginsByEnableOrder } from "./plugin-order";

/** A plugin command the composer can run. `k` is its owner's, for error toasts. */
export type PluginCommandEntry = {
  name: string;
  description: string;
  pluginId: string;
  run: CommandSpec["run"];
  k: PluginApi;
};

/** A loaded plugin command hidden by another plugin's earlier enable order. */
export type PluginCommandConflict = {
  pluginId: string;
  pluginName: string;
  commandName: string;
  winnerPluginId: string;
  winnerName: string;
};

type PluginCommandResolution = {
  entries: PluginCommandEntry[];
  conflicts: PluginCommandConflict[];
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

/** Resolve loaded plugin commands: a native name wins, then the first enabled plugin. */
function resolvePluginCommands(
  plugins: readonly PluginRecord[],
  reserved: ReadonlySet<string>,
): PluginCommandResolution {
  const taken = new Set(reserved);
  const owners = new Map<string, PluginRecord>();
  const entries: PluginCommandEntry[] = [];
  const conflicts: PluginCommandConflict[] = [];
  const conflictKeys = new Set<string>();
  for (const record of pluginsByEnableOrder(plugins)) {
    if (!record.k) continue;
    for (const command of record.contributions.commands) {
      const key = command.name.toLowerCase();
      if (taken.has(key)) {
        const winner = owners.get(key);
        const conflictKey = JSON.stringify([record.id, key]);
        if (winner && winner.id !== record.id && !conflictKeys.has(conflictKey)) {
          conflicts.push({
            pluginId: record.id,
            pluginName: record.name,
            commandName: command.name,
            winnerPluginId: winner.id,
            winnerName: winner.name,
          });
          conflictKeys.add(conflictKey);
        }
        continue;
      }
      taken.add(key);
      owners.set(key, record);
      entries.push({
        name: command.name,
        description: command.description,
        pluginId: record.id,
        run: command.run,
        k: record.k,
      });
    }
  }
  return { entries, conflicts };
}

/** Commands from loaded plugins, with native names taking precedence. */
export function pluginCommandEntries(
  plugins: readonly PluginRecord[],
  reserved: ReadonlySet<string>,
): PluginCommandEntry[] {
  return resolvePluginCommands(plugins, reserved).entries;
}

/** Duplicate plugin commands and the enabled-first winner that currently owns each name. */
export function pluginCommandConflicts(
  plugins: readonly PluginRecord[],
  reserved: ReadonlySet<string>,
): PluginCommandConflict[] {
  return resolvePluginCommands(plugins, reserved).conflicts;
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
