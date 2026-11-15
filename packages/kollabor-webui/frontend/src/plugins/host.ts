import { PluginEventStream } from "./events";
import { PluginLoader, type WebPluginEntry } from "./loader";
import { PluginRegistry, type PluginDraft, type RegistrySnapshot } from "./registry";
import { createPluginContext, type PluginContext, type PluginOpen } from "./sdk";

export interface PluginEntry extends WebPluginEntry {
  name: string;
  kind: "built-in" | "installed" | "dev";
  source?: string | null;
  read_only?: boolean;
  required?: boolean;
  version?: string | null;
  commit?: string | null;
  error?: string | null;
  settings?: unknown[];
  services?: unknown[];
  uses?: unknown[];
  granted?: unknown[];
  contributes: Record<string, unknown>;
}
export interface PluginHostSnapshot {
  disabled: boolean;
  loading: boolean;
  inventoryError: string | null;
  entries: PluginEntry[];
  registry: RegistrySnapshot;
  errors: Record<string, string>;
  skipped: string[];
  renderFailed: string[];
}
export interface PluginHostOptions {
  json: (path: string, init?: RequestInit) => Promise<unknown>;
  request: (path: string, init?: RequestInit) => Promise<Response>;
  importer: (url: string) => Promise<unknown>;
  storage: Storage;
  disabled: boolean;
  navigate: (pluginId: string, target: PluginOpen) => void;
  openAgent: (sessionId: string) => void;
  copy: (text: string) => Promise<void>;
  toast: (text: string) => void;
  ui: Record<string, unknown>;
  builtinCommands?: string[];
}
interface Resources {
  draft: PluginDraft;
  context: PluginContext & { dispose(): void };
  events: PluginEventStream;
  style: { ready: Promise<void>; remove(): void } | null;
}
const LOADING = "kollab:plugin:loading:";
const SKIPPED = "kollab:plugin:skipped:";

function stylesheet(entry: PluginEntry): Resources["style"] {
  const path = entry.web?.style;
  if (!path) return null;
  if (!path.startsWith("web/dist/")) throw new Error("Invalid plugin stylesheet path");
  const parts = path.slice("web/dist/".length).split("/");
  if (parts.some(part => !part || part.startsWith(".") || part.includes("\\"))) throw new Error("Invalid plugin stylesheet path");
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = `/plugins/${encodeURIComponent(entry.id)}/web/${parts.map(encodeURIComponent).join("/")}?v=${encodeURIComponent(entry.web?.hash ?? "")}`;
  let finish!: () => void;
  const ready = new Promise<void>((resolve, reject) => {
    finish = resolve;
    link.onload = () => resolve();
    link.onerror = () => reject(new Error("Plugin stylesheet could not load"));
  });
  document.head.appendChild(link);
  return { ready, remove: () => { link.remove(); finish(); } };
}

/** Browser-page owner for inventory, plugin resources, recovery, and contributions. */
export class PluginHost {
  readonly registry: PluginRegistry;
  private readonly globalEvents: PluginEventStream;
  private readonly loader: PluginLoader<PluginContext, Resources>;
  private readonly resources = new Map<string, Resources>();
  private readonly listeners = new Set<() => void>();
  private readonly skipped = new Set<string>();
  private readonly renderFailed = new Set<string>();
  private errors: Record<string, string> = {};
  private entries: PluginEntry[] = [];
  private loading = false;
  private inventoryError: string | null = null;
  private started = false;
  private closed = false;
  private sequence = 0;
  private markerSequence = 0;
  private snapshot: PluginHostSnapshot;

  constructor(private readonly options: PluginHostOptions) {
    this.registry = new PluginRegistry(options.builtinCommands);
    this.globalEvents = new PluginEventStream(options.request, "/plugins/events", undefined, () => { void this.refresh(); });
    this.snapshot = this.makeSnapshot();
    this.registry.subscribe(() => this.publish());
    this.loader = new PluginLoader({
      importer: async url => {
        const id = decodeURIComponent(url.split("/")[2]);
        const resource = this.resources.get(id);
        await resource?.style?.ready;
        if (!resource || this.closed || this.resources.get(id) !== resource) throw new Error("Plugin load was cancelled");
        const marker = String(++this.markerSequence);
        this.putStorage(LOADING + id, marker);
        try { return await options.importer(url); }
        finally {
          try {
            if (this.options.storage.getItem(LOADING + id) === marker) this.removeStorage(LOADING + id);
          } catch (error) { console.warn("Plugin recovery storage is unavailable", error); }
        }
      },
      createContext: entry => {
        this.release(entry.id);
        const draft = this.registry.stage(entry.id, (entry as PluginEntry).contributes ?? {});
        const events = new PluginEventStream(options.request, `/plugins/${encodeURIComponent(entry.id)}/events`);
        const context = createPluginContext({
          id: entry.id, draft, json: options.json, events,
          navigate: target => options.navigate(entry.id, target),
          openAgent: options.openAgent, copy: options.copy, toast: options.toast, ui: options.ui,
        });
        const resource: Resources = { draft, context, events, style: stylesheet(entry as PluginEntry) };
        this.resources.set(entry.id, resource);
        return { api: context, value: resource };
      },
      commit: (id, resource) => {
        if (this.resources.get(id) !== resource || this.closed) return;
        delete this.errors[id];
        this.renderFailed.delete(id);
        this.registry.commit(resource.draft);
      },
      remove: id => { this.release(id); this.registry.remove(id); this.removeStorage(LOADING + id); },
      fail: (id, error) => {
        this.renderFailed.delete(id);
        this.removeStorage(LOADING + id);
        this.release(id);
        this.registry.remove(id);
        this.errors = { ...this.errors, [id]: error };
        this.publish();
      },
    });
  }

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };

  async start() {
    if (this.started || this.closed || this.options.disabled) return;
    this.started = true;
    this.recoverMarkers();
    this.globalEvents.on("plugins-changed", () => { void this.refresh(); });
    await this.refresh();
  }

  async refresh() {
    if (this.closed || this.options.disabled) return;
    const sequence = ++this.sequence;
    this.loading = true;
    this.publish();
    try {
      const response = await this.options.json("/plugins") as { schema?: unknown; plugins?: unknown };
      if (sequence !== this.sequence || this.closed) return;
      if (response.schema !== 1 || !Array.isArray(response.plugins)) throw new Error("Invalid plugin inventory");
      this.entries = response.plugins as PluginEntry[];
      this.inventoryError = null;
      const included = this.entries.filter(entry => !this.skipped.has(entry.id));
      const desired = new Set(included.filter(entry => entry.enabled && !entry.shadowed && (entry.state === "ok" || entry.state === "stopping") && entry.web).map(entry => entry.id));
      this.errors = Object.fromEntries(Object.entries(this.errors).filter(([id]) => desired.has(id)));
      for (const id of this.renderFailed) if (!desired.has(id)) this.renderFailed.delete(id);
      this.loading = false;
      this.publish();
      await this.loader.reconcile(included);
    } catch (error) {
      if (sequence !== this.sequence || this.closed) return;
      this.loading = false;
      this.inventoryError = error instanceof Error ? error.message : String(error);
      this.publish();
    }
  }

  navigate(id: string, target: PluginOpen) { this.options.navigate(id, target); }
  getContext(id: string) { return this.resources.get(id)?.context; }
  reportRenderError(id: string, error: unknown) {
    if (!this.resources.has(id)) return;
    this.release(id);
    this.errors = { ...this.errors, [id]: error instanceof Error ? error.message : String(error) };
    this.renderFailed.add(id);
    this.registry.remove(id);
    this.publish();
  }
  async retry(id: string) { await this.loader.retry(id); }
  async turnOnAgain(id: string) {
    this.skipped.delete(id);
    this.removeStorage(SKIPPED + id);
    this.publish();
    await this.refresh();
  }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.sequence++;
    this.globalEvents.close();
    for (const id of [...this.resources.keys()]) {
      this.release(id);
      this.registry.remove(id);
      this.removeStorage(LOADING + id);
    }
  }

  private release(id: string) {
    const resource = this.resources.get(id);
    if (!resource) return;
    this.resources.delete(id);
    resource.context.dispose();
    resource.events.close();
    resource.style?.remove();
  }
  private recoverMarkers() {
    try {
      const keys = Array.from({ length: this.options.storage.length }, (_, i) => this.options.storage.key(i)).filter((key): key is string => key !== null);
      for (const key of keys) {
        if (key.startsWith(LOADING)) {
          const id = key.slice(LOADING.length);
          this.skipped.add(id);
          this.putStorage(SKIPPED + id, "1");
          this.removeStorage(key);
        } else if (key.startsWith(SKIPPED)) this.skipped.add(key.slice(SKIPPED.length));
      }
    } catch (error) { console.warn("Plugin recovery storage is unavailable", error); }
    this.publish();
  }
  private putStorage(key: string, value: string) {
    try { this.options.storage.setItem(key, value); } catch (error) { console.warn("Plugin recovery storage is unavailable", error); }
  }
  private removeStorage(key: string) {
    try { this.options.storage.removeItem(key); } catch (error) { console.warn("Plugin recovery storage is unavailable", error); }
  }
  private makeSnapshot(): PluginHostSnapshot {
    return { disabled: this.options.disabled, loading: this.loading, inventoryError: this.inventoryError,
      entries: this.entries, registry: this.registry.getSnapshot(), errors: this.errors, skipped: [...this.skipped], renderFailed: [...this.renderFailed] };
  }
  private publish() {
    this.snapshot = this.makeSnapshot();
    for (const listener of this.listeners) listener();
  }
}
