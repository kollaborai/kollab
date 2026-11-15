import type {
  PluginCommand, PluginMenu, PluginModal, PluginNotice, PluginPage, PluginRenderer,
  PluginSettings, PluginView,
} from "./sdk";

export interface PluginRecords {
  id: string;
  menus: PluginMenu[];
  views: PluginView[];
  pages: PluginPage[];
  modals: PluginModal[];
  settings: PluginSettings[];
  commands: PluginCommand[];
  renderers: PluginRenderer[];
  notices: PluginNotice[];
}

export interface CommandConflict { pluginId: string; command: string; owner: string }
export interface RegistrySnapshot { plugins: PluginRecords[]; conflicts: CommandConflict[] }

/** Contributions are not visible to the app until the activation succeeds. */
export class PluginDraft {
  readonly records: PluginRecords;

  constructor(id: string) {
    this.records = { id, menus: [], views: [], pages: [], modals: [], settings: [], commands: [], renderers: [], notices: [] };
  }
  menu(id: string, options: Omit<PluginMenu, "id"> = {}) { this.records.menus.push({ id, ...options }); }
  view(view: PluginView) { this.records.views.push(view); }
  page(page: PluginPage) { this.records.pages.push(page); }
  modal(modal: PluginModal) { this.records.modals.push(modal); }
  settings(settings: PluginSettings) { this.records.settings.push(settings); }
  command(command: PluginCommand) { this.records.commands.push(command); }
  renderer(renderer: PluginRenderer) { this.records.renderers.push(renderer); }
  notice(notice: PluginNotice) { this.records.notices.push(notice); }
}

/** One browser-page registry. Identity belongs to the plugin, not an unqualified contribution id. */
export class PluginRegistry {
  private readonly drafts = new Map<string, PluginRecords>();
  private readonly listeners = new Set<() => void>();
  private builtins: Set<string>;
  private snapshot: RegistrySnapshot = { plugins: [], conflicts: [] };

  constructor(builtinCommands: readonly string[] = []) {
    this.builtins = new Set(builtinCommands.map(PluginRegistry.commandKey));
  }

  private static commandKey(name: string) { return name.replace(/^\//, "").toLowerCase(); }
  setBuiltinCommands(commands: readonly string[]) {
    const next = new Set(commands.map(PluginRegistry.commandKey));
    if (next.size === this.builtins.size && [...next].every(name => this.builtins.has(name))) return;
    this.builtins = next;
    this.publish();
  }
  stage(id: string) { return new PluginDraft(id); }
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  commit(draft: PluginDraft) {
    const records = draft.records;
    // Copy arrays so a late callback cannot mutate the published registry.
    this.drafts.set(records.id, {
      ...records, menus: [...records.menus], views: [...records.views], pages: [...records.pages],
      modals: [...records.modals], settings: [...records.settings], commands: [...records.commands],
      renderers: [...records.renderers], notices: [...records.notices],
    });
    this.publish();
  }

  remove(id: string) {
    if (this.drafts.delete(id)) this.publish();
  }

  private publish() {
    const owners = new Map([...this.builtins].map(name => [name, "built-in"]));
    const conflicts: CommandConflict[] = [];
    const plugins = [...this.drafts.values()].map(plugin => {
      const commands = plugin.commands.filter(command => {
        const key = PluginRegistry.commandKey(command.name);
        const owner = owners.get(key);
        if (owner) {
          conflicts.push({ pluginId: plugin.id, command: command.name, owner });
          return false;
        }
        owners.set(key, plugin.id);
        return true;
      });
      return { ...plugin, commands };
    });
    this.snapshot = { plugins, conflicts };
    for (const listener of this.listeners) listener();
  }
}
