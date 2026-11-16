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

  constructor(id: string, private readonly declarations?: Record<string, unknown>) {
    this.records = { id, menus: [], views: [], pages: [], modals: [], settings: [], commands: [], renderers: [], notices: [] };
  }
  private declared(group: string, id: string): Record<string, unknown> | null {
    if (!this.declarations) return {};
    const declarations = this.declarations[group];
    const normalized = id.replace(/^\//, "");
    const found = Array.isArray(declarations) ? declarations.find(item =>
      typeof item === "string" ? item.replace(/^\//, "") === normalized :
        item && typeof item === "object" && [item.id, item.name, item.tool, item.tag].includes(id),
    ) : undefined;
    if (found === undefined) {
      console.warn(`Plugin ${this.records.id} attempted undeclared ${group} contribution: ${id}`);
      return null;
    }
    return typeof found === "object" && found !== null ? found as Record<string, unknown> : {};
  }
  menu(id: string, options: Omit<PluginMenu, "id"> = {}) {
    const declared = this.declared("menu", id);
    if (declared) this.records.menus.push({ ...options, ...declared, id } as PluginMenu);
  }
  view(view: PluginView) { if (this.declared("views", view.id)) this.records.views.push(view); }
  page(page: PluginPage) { if (this.declared("pages", page.id)) this.records.pages.push(page); }
  modal(modal: PluginModal) { if (this.declared("modals", modal.id)) this.records.modals.push(modal); }
  settings(settings: PluginSettings) { this.records.settings.push(settings); }
  command(command: PluginCommand) { if (this.declared("slash", command.name)) this.records.commands.push(command); }
  renderer(renderer: PluginRenderer) {
    const id = renderer.tool ?? renderer.tag;
    if (id && this.declared("renderers", id)) this.records.renderers.push(renderer);
  }
  notice(notice: PluginNotice) { this.records.notices.push(notice); }
}

/** One browser-page registry. Identity belongs to the plugin, not an unqualified contribution id. */
export class PluginRegistry {
  private readonly drafts = new Map<string, PluginRecords>();
  private readonly listeners = new Set<() => void>();
  private builtins: Set<string>;
  private enableOrder = new Map<string, number>();
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
  setEnableOrder(ids: readonly string[]) {
    const next = new Map(ids.map((id, index) => [id, index]));
    if (next.size === this.enableOrder.size && [...next].every(([id, rank]) => this.enableOrder.get(id) === rank)) return;
    this.enableOrder = next;
    this.publish();
  }
  stage(id: string, declarations?: Record<string, unknown>) { return new PluginDraft(id, declarations); }
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
    const plugins = [...this.drafts.values()].sort((a, b) =>
      (this.enableOrder.get(a.id) ?? Infinity) - (this.enableOrder.get(b.id) ?? Infinity),
    ).map(plugin => {
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
