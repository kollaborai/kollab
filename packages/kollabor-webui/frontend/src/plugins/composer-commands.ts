import type { ComposerPaletteItem } from "@/components/assistant-ui/composer-palette";
import { DEFAULT_SLASH_COMMANDS } from "@/api";
import { PANEL_COMMAND_NAMES } from "@/components/panels/panel-model";
import type { PluginRecord, PluginsSnapshot } from "./registry";
import type { CommandSpec, PluginApi } from "./sdk";
import { pluginsByEnableOrder } from "./plugin-order";
import { declaredIds } from "./plugin-declarations";

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

export type PluginCommandCatalogStatus = "loading" | "ready" | "empty" | "unavailable" | "fallback";

export function pluginCommandCatalogReady(status: PluginCommandCatalogStatus): boolean {
  return status === "ready" || status === "empty";
}

/** Hold unresolved slash text only while a plugin may claim it or its draft is recoverable. */
export function pluginCommandSubmissionPending(
  text: string,
  status: PluginCommandCatalogStatus,
  snapshot: Pick<PluginsSnapshot, "off" | "plugins">,
  reserved: ReadonlySet<string>,
  canPersistDraft = true,
): boolean {
  const name = /^\/(\S+)(?:\s+[\s\S]*)?$/.exec(text.trim())?.[1]?.toLowerCase();
  if (!name || snapshot.off || reserved.has(name) || snapshot.plugins.length === 0) return false;
  const declaresName = (plugin: PluginRecord) =>
    [...declaredIds(plugin.entry.contributes?.slash)].some((declared) => declared.toLowerCase() === name);
  if (status === "loading") return canPersistDraft || snapshot.plugins.some(declaresName);
  return snapshot.plugins.some(
    (plugin) => plugin.status === "loading" && declaresName(plugin),
  );
}

/** A failed async command can restore its draft only into the same untouched composer session. */
export function canRestorePluginCommandDraft(
  currentRevision: number,
  clearedRevision: number,
  currentSession: string | null | undefined,
  commandSession: string | null | undefined,
): boolean {
  return currentRevision === clearedRevision && Object.is(currentSession, commandSession);
}

/** App-owned and native command names and aliases, lower case, without the slash. */
export function reservedCommandKeys(
  commands: readonly { name: string; aliases?: readonly string[] }[],
): Set<string> {
  const keys = new Set<string>();
  for (const command of [...DEFAULT_SLASH_COMMANDS, ...commands]) {
    for (const name of [command.name, ...(command.aliases ?? [])]) {
      keys.add(name.replace(/^\//, "").toLowerCase());
    }
  }
  for (const name of PANEL_COMMAND_NAMES) keys.add(name.replace(/^\//, "").toLowerCase());
  // The engine handles /exit even when a session's live command catalog has not arrived yet.
  keys.add("exit");
  return keys;
}

/** Resolve loaded plugin commands: a native name wins, then the first enabled plugin. */
function resolvePluginCommands(
  plugins: readonly PluginRecord[],
  reserved: ReadonlySet<string>,
): PluginCommandResolution {
  const taken = new Set([...reservedCommandKeys([]), ...reserved]);
  const owners = new Map<string, PluginRecord>();
  const entries: PluginCommandEntry[] = [];
  const conflicts: PluginCommandConflict[] = [];
  const conflictKeys = new Set<string>();
  const ordered = pluginsByEnableOrder(plugins);
  for (const record of ordered) {
    if (!record.entry.web?.entry) continue;
    for (const declared of declaredIds(record.entry.contributes?.slash)) {
      const key = declared.toLowerCase();
      if (!taken.has(key) && !owners.has(key)) owners.set(key, record);
    }
  }
  const emitted = new Set<string>();
  for (const record of ordered) {
    if (!record.k) continue;
    for (const command of record.contributions.commands) {
      const key = command.name.toLowerCase();
      const winner = owners.get(key);
      if (taken.has(key) || !winner || winner.id !== record.id) {
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
      if (emitted.has(key)) continue;
      emitted.add(key);
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

/** Warn when another loaded plugin already owns a slash command. */
export function warnPluginCommandConflicts(conflicts: readonly PluginCommandConflict[]): void {
  for (const conflict of conflicts) {
    console.warn(
      `Plugin command /${conflict.commandName} from ${conflict.pluginName} (${conflict.pluginId}) is hidden; ` +
        `${conflict.winnerName} (${conflict.winnerPluginId}) wins.`,
    );
  }
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
  void error;
  return `Could not run /${name}. Check the browser console for redacted details.`;
}
