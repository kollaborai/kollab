/** The fields the web loader needs from GET /plugins. */
export interface WebPluginEntry {
  id: string;
  enabled: boolean;
  shadowed: boolean;
  state: string;
  contributes?: Record<string, unknown>;
  web: { entry: string; style: string | null; hash: string } | null;
}

interface LoaderOptions<Api, Value> {
  importer: (url: string) => Promise<unknown>;
  createContext: (entry: WebPluginEntry) => { api: Api; value: Value };
  commit: (id: string, value: Value) => void;
  remove: (id: string) => void;
  fail: (id: string, error: string) => void;
  timeoutMs?: number;
}

interface Attempt {
  entry: WebPluginEntry;
  key: string;
  token: object;
  retry: number;
  pending: Promise<void>;
}

function eligible(entry: WebPluginEntry, alreadyActive: boolean): boolean {
  return !entry.shadowed && entry.web !== null &&
    ((entry.enabled && (entry.state === "ok" || entry.state === "stopping")) || (alreadyActive && entry.state === "stopping"));
}

function entryUrl(entry: WebPluginEntry, retry: number): string {
  const web = entry.web;
  if (!web || !web.entry.startsWith("web/dist/")) throw new Error("Invalid web entry path");
  const parts = web.entry.slice("web/dist/".length).split("/");
  if (parts.some(part => !part || part === "." || part === ".." || part.includes("\\"))) {
    throw new Error("Invalid web entry path");
  }
  const url = `/plugins/${encodeURIComponent(entry.id)}/web/${parts.map(encodeURIComponent).join("/")}?v=${encodeURIComponent(web.hash)}`;
  return retry ? `${url}&retry=${retry}` : url;
}

/**
 * Stages contributions per activation. Only the current, completed activation may
 * commit: removed, replaced and timed-out plugins cannot publish late results.
 * This is the lifecycle core; the host owns the context, registry, CSS and events.
 */
export class PluginLoader<Api, Value> {
  private readonly options: LoaderOptions<Api, Value>;
  private readonly attempts = new Map<string, Attempt>();
  private readonly entries = new Map<string, WebPluginEntry>();
  private readonly retries = new Map<string, number>();

  constructor(options: LoaderOptions<Api, Value>) { this.options = options; }

  async reconcile(entries: WebPluginEntry[]): Promise<void> {
    const desired = new Map(entries.filter(entry => eligible(entry, this.attempts.has(entry.id))).map(entry => [entry.id, entry]));
    this.entries.clear();
    for (const [id, entry] of desired) this.entries.set(id, entry);
    for (const [id, attempt] of this.attempts) {
      const entry = desired.get(id);
      if (!entry || this.key(entry) !== attempt.key) {
        this.attempts.delete(id);
        this.options.remove(id);
      }
    }
    const pending: Promise<void>[] = [];
    for (const [id, entry] of desired) {
      const current = this.attempts.get(id);
      pending.push(current ? current.pending : this.start(entry, this.retries.get(id) ?? 0));
    }
    await Promise.all(pending);
  }

  async retry(id: string): Promise<void> {
    const entry = this.entries.get(id);
    if (!entry) return;
    const retry = (this.retries.get(id) ?? 0) + 1;
    this.retries.set(id, retry);
    this.attempts.delete(id);
    this.options.remove(id);
    await this.start(entry, retry);
  }

  private key(entry: WebPluginEntry): string {
    return JSON.stringify([entry.web?.entry ?? "", entry.web?.style ?? null, entry.web?.hash ?? "", entry.contributes ?? null]);
  }

  private start(entry: WebPluginEntry, retry: number): Promise<void> {
    const token = {};
    const attempt: Attempt = { entry, key: this.key(entry), token, retry, pending: Promise.resolve() };
    this.attempts.set(entry.id, attempt);
    attempt.pending = this.activate(entry, token, retry);
    return attempt.pending;
  }

  private async activate(entry: WebPluginEntry, token: object, retry: number): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let expired = false;
    try {
      const { api, value } = this.options.createContext(entry);
      const work = (async () => {
        const module = await this.options.importer(entryUrl(entry, retry));
        if (expired || this.attempts.get(entry.id)?.token !== token) return;
        const activate = (module as { default?: unknown } | null)?.default;
        if (typeof activate !== "function") throw new Error("Plugin has no default activation function");
        await activate(api);
      })();
      const timeout = new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          expired = true;
          reject(new Error("Plugin loading timed out"));
        }, this.options.timeoutMs ?? 5000);
      });
      await Promise.race([work, timeout]);
      if (this.attempts.get(entry.id)?.token === token) this.options.commit(entry.id, value);
    } catch (error) {
      if (this.attempts.get(entry.id)?.token === token) {
        this.options.fail(entry.id, error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }
}
