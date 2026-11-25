/**
 * The web plugin loader (docs/specs/plugins.md, "Web plugin API" > Loading).
 *
 * At boot the page reads `GET /plugins` and imports every enabled plugin's `web/dist` entry in parallel from
 * `/plugins/{id}/web/<file>?v=<hash>`, then awaits its plugin function. A 5 s limit covers the import and that
 * await; a plugin that throws or runs out of time shows Failed to Load and nothing else changes. Its
 * contributions show only once its function returns, so a timed-out import that resolves later adds nothing.
 * Retry imports `<file>?v=<hash>&retry=<n>` so the browser does not hand back the failed module. Every
 * `plugins-changed` event reads `GET /plugins` again and adds or removes plugins without a reload, and
 * `?plugins=off` in the page URL loads no plugin code at all.
 */
import { useSyncExternalStore } from "react";
import type { PanelField } from "@/api";
import type { EngineApi } from "@/api";
import { inPhoneApp, navigateToPluginPage } from "@/utils/page-route";
import {
  EMPTY_CONTRIBUTIONS,
  nextNoticeKey,
  pluginRegistry,
  pluginViewKey,
  type Contributions,
  type PluginRegistry,
  type PluginViewKey,
} from "./registry";
import {
  createPluginApi,
  followEvents,
  isOpenTarget,
  type OpenTarget,
  type PluginApiDeps,
  type PluginEntry,
  type PluginInventoryResponse,
  type PluginService,
} from "./sdk";

export const LOAD_TIMEOUT_MS = 5_000;

const ID = /^[a-z0-9][a-z0-9_-]{1,39}(?:@dev)?$/;
// `stopping` and `stop-failed`: an enabled plugin keeps its page while services finish or need attention.
const LOADABLE = new Set<PluginEntry["state"]>(["ok", "error", "stopping", "stop-failed"]);
// The export React itself assigns. Code that only reads it (a renderer library) never assigns it.
const OWN_REACT =
  /__(?:CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE|SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED)\s*=(?!=)/;
const INVENTORY_TEXT_LIMIT = 4096;
export const PLUGIN_INVENTORY_LIMITS = {
  services: 128, serviceSecrets: 64, uses: 256, contributions: 256, settings: 256,
  settingOptions: 256, optionLabels: 256, changedFolders: 256,
} as const;
const SENSITIVE_ASSIGNMENT = /(?:^|[^A-Za-z0-9])(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|credential|authorization|bearer)(?![A-Za-z0-9])\s*[:=]\s*([^\s,;]+)/gi;
const AUTH_SCHEME = /\b(?:bearer|basic)\s+[^\s,;]+/i;
const SECRET_VALUE = /\b(?:secret[-_]token[-_][A-Za-z0-9_-]{6,}|sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]*/i;
const SAFE_TEXT_VALUES = new Set(["budget", "length", "required"]);
const INVISIBLE_CONTROLS = /[\u00ad\u200b-\u200d\u202a-\u202e\u2060\u2066-\u2069\ufeff]/;
const SERVICE_ID = /^[a-z0-9][a-z0-9-]{0,39}$/;
const DECLARATION_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const SECRET_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const EXPLICIT_SECRET_IDENTIFIER = /\bsecret[-_]token[-_][A-Za-z0-9_-]{6,}/i;
const CONTRIBUTION_KINDS = ["menu", "views", "pages", "modals", "slash", "renderers"] as const;
const PLUGIN_STATES = new Set<PluginEntry["state"]>(["ok", "error", "off", "stopping", "stop-failed", "changed-on-disk", "skipped"]);
const SERVICE_STATES = new Set<PluginService["state"]>([
  "starting", "running", "backoff", "stopping", "stop-failed", "exited", "stopped", "crash-stopped", "error", "unknown", "failed",
]);
const FIXED_PLUGIN_ERRORS = new Set([
  "Plugin files changed since approval",
  "Plugin metadata could not be loaded",
  "Development plugin metadata could not be loaded",
  "Python entry failed while loading; see the process log for details.",
]);
const FIXED_SERVICE_ERRORS = new Set([
  "Could not prepare plugin engine credentials",
  "Could not start this process. View Log for details.",
  "Could not stop this process. Retry stopping it.",
  "Could not verify the stop. Check the process before retrying.",
]);
const SAFE_PLUGIN_ENTRY_ERROR = "Plugin details could not be loaded.";

const message = (error: unknown) => (error instanceof Error ? error.message : String(error));
const SAFE_WEB_PLUGIN_LOAD_ERROR = "This plugin's web code failed while loading. Check the browser console for details.";

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function exceedsCodePointLimit(value: string, limit: number): boolean {
  let count = 0;
  for (const _ of value) if (++count > limit) return true;
  return false;
}

function safeText(value: unknown, limit = INVENTORY_TEXT_LIMIT): string | null {
  if (typeof value !== "string" || exceedsCodePointLimit(value, limit) || /[\u0000-\u001f\u007f]/.test(value) || INVISIBLE_CONTROLS.test(value) || containsSensitiveText(value))
    return null;
  const clean = value.trim();
  return clean || null;
}

function containsSensitiveText(value: string): boolean {
  if (AUTH_SCHEME.test(value) || SECRET_VALUE.test(value)) return true;
  for (const match of value.matchAll(SENSITIVE_ASSIGNMENT)) {
    const assigned = match[1].replace(/[.!?]+$/, "").toLowerCase();
    if (!SAFE_TEXT_VALUES.has(assigned)) return true;
  }
  return false;
}

export function safePluginText(value: unknown, limit = INVENTORY_TEXT_LIMIT): string | null {
  return safeText(value, limit);
}

function safeIdentifier(value: unknown, limit = INVENTORY_TEXT_LIMIT): string | null {
  if (typeof value !== "string" || exceedsCodePointLimit(value, limit) || /[\u0000-\u001f\u007f]/.test(value)
    || INVISIBLE_CONTROLS.test(value) || EXPLICIT_SECRET_IDENTIFIER.test(value))
    return null;
  const clean = value.trim();
  return clean || null;
}

export function safePluginIdentifier(value: unknown, limit = INVENTORY_TEXT_LIMIT): string | null {
  return safeIdentifier(value, limit);
}

export function normalizePluginWebPath(value: unknown): string | null {
  const path = safeIdentifier(value, 512);
  if (!path || path.startsWith("/") || path.includes("\\")) return null;
  const parts = path.split("/").filter((part) => part && part !== ".");
  return parts.length >= 3 && parts[0] === "web" && parts[1] === "dist" && !parts.includes("..")
    ? parts.join("/")
    : null;
}

export function normalizeBoundedUnique<T>(
  value: unknown,
  limit: number,
  normalize: (item: unknown) => T | null,
): T[] {
  if (!Array.isArray(value)) return [];
  const result: T[] = [];
  const seen = new Set<T>();
  for (const item of value.slice(0, limit)) {
    const clean = normalize(item);
    if (clean === null || seen.has(clean)) continue;
    seen.add(clean);
    result.push(clean);
  }
  return result;
}

function safeNumber(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= minimum && value <= maximum ? value : null;
}

function safeInteger(value: unknown, minimum = 0): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum ? value : null;
}

function normalizeOpenTarget(value: unknown): OpenTarget | null {
  const raw = record(value);
  if (!raw) return null;
  const keys = Object.keys(raw);
  if (keys.length !== 1 || !["page", "modal", "view", "url"].includes(keys[0])) return null;
  const target = keys[0] === "url" ? safeText(raw.url, 512) : safeIdentifier(raw[keys[0]], 512);
  if (!target) return null;
  return { [keys[0]]: target } as OpenTarget;
}

function normalizeDeclared(value: unknown): string | Record<string, string> | null {
  const text = safeIdentifier(value, 512);
  if (text) return DECLARATION_ID.test(text) ? text : null;
  const raw = record(value);
  if (!raw) return null;
  const next: Record<string, string> = {};
  for (const key of ["id", "name", "tool", "tag", "label", "title"] as const) {
    if (raw[key] === undefined) continue;
    const field = key === "label" || key === "title"
      ? safeText(raw[key], 512)
      : safeIdentifier(raw[key], 512);
    if (!field || (key !== "label" && key !== "title" && !DECLARATION_ID.test(field))) return null;
    next[key] = field;
  }
  return Object.keys(next).some((key) => ["id", "name", "tool", "tag"].includes(key)) ? next : null;
}

export function normalizePluginContributes(value: unknown): PluginEntry["contributes"] {
  const raw = record(value);
  const result: Record<string, unknown[]> = { menu: [], views: [], pages: [], modals: [], slash: [], renderers: [] };
  if (!raw) return result as PluginEntry["contributes"];
  for (const kind of CONTRIBUTION_KINDS) {
    if (!Array.isArray(raw[kind])) continue;
    for (const item of raw[kind].slice(0, PLUGIN_INVENTORY_LIMITS.contributions)) {
      if (kind !== "menu") {
        const declared = normalizeDeclared(item);
        if (declared) result[kind].push(declared);
        continue;
      }
      const row = record(item);
      const id = safeIdentifier(row?.id, 128);
      const label = safeText(row?.label, 256);
      const open = normalizeOpenTarget(row?.open);
      const icon = row?.icon === undefined ? undefined : safeText(row.icon, 64);
      if (id && DECLARATION_ID.test(id) && label && open && (row?.icon === undefined || icon))
        result.menu.push({ id, label, open, ...(icon ? { icon } : {}) });
    }
  }
  return result as PluginEntry["contributes"];
}

function normalizeUse(value: unknown): string | null {
  if (value === "agents" || value === "agents:unattended") return value;
  if (typeof value !== "string" || value.length > 1024) return null;
  const secret = /^secret:([A-Za-z_][A-Za-z0-9_]{0,63})$/.exec(value);
  if (secret) return value;
  const declaration = /^(run|net|files):(.+)$/.exec(value);
  if (declaration) {
    const detail = safeIdentifier(declaration[2], 1000);
    if (detail) return `${declaration[1]}:${detail}`;
  }
  const other = /^([a-z][a-z0-9_-]{0,63}):([A-Za-z0-9_./,-]{1,256})$/.exec(value);
  return other && safeIdentifier(value, 1024) ? value : null;
}

export function normalizePluginUses(value: unknown, onInvalid?: () => void): string[] {
  return normalizeBoundedUnique(value, PLUGIN_INVENTORY_LIMITS.uses, (item) => {
    const use = normalizeUse(item);
    if (use === null) onInvalid?.();
    return use;
  });
}

function normalizeSettings(value: unknown): PanelField[] {
  if (!Array.isArray(value)) return [];
  const fields: PanelField[] = [];
  for (const item of value.slice(0, PLUGIN_INVENTORY_LIMITS.settings)) {
    const raw = record(item);
    const type = raw?.type;
    const path = safeIdentifier(raw?.path, 512);
    const label = safeText(raw?.label, 256);
    if (!raw || !path || !label || !["checkbox", "slider", "spinbox", "dropdown", "text_input", "textarea", "label"].includes(String(type)))
      continue;
    const value = raw.value === null || raw.value === undefined ? null
      : typeof raw.value === "boolean" ? raw.value
      : typeof raw.value === "number" ? safeNumber(raw.value, -Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER)
      : typeof raw.value === "string" ? safeText(raw.value)
      : null;
    const optionalText = (field: string, limit = 1024): string | null | undefined =>
      raw[field] === undefined ? undefined : raw[field] === null ? null : safeText(raw[field], limit);
    const optionalNumber = (field: string): number | null | undefined =>
      raw[field] === undefined ? undefined : raw[field] === null ? null : safeNumber(raw[field], -Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER);
    const options = raw.options === undefined ? undefined : raw.options === null ? null
      : Array.isArray(raw.options) ? raw.options.slice(0, PLUGIN_INVENTORY_LIMITS.settingOptions).map((option) => safeText(option, 256)).filter((option): option is string => option !== null)
      : null;
    const labels = record(raw.option_labels);
    const normalizedLabels = Object.create(null) as Record<string, string>;
    let labelCount = 0;
    if (labels) for (const key in labels) {
      if (!Object.prototype.hasOwnProperty.call(labels, key)) continue;
      if (labelCount >= PLUGIN_INVENTORY_LIMITS.optionLabels) break;
      labelCount += 1;
      const safeKey = safeText(key, 256);
      const safeLabel = safeText(labels[key], 256);
      if (safeKey && safeLabel) normalizedLabels[safeKey] = safeLabel;
    }
    const optionLabels = labels ? normalizedLabels : raw.option_labels === null ? null : undefined;
    fields.push({
      type: type as PanelField["type"], path, label,
      editable: raw.editable === true,
      ...(raw.help === undefined ? {} : { help: optionalText("help") ?? null }),
      value: raw.secret === true ? null : value,
      ...(raw.min_value === undefined ? {} : { min_value: optionalNumber("min_value") ?? null }),
      ...(raw.max_value === undefined ? {} : { max_value: optionalNumber("max_value") ?? null }),
      ...(raw.step === undefined ? {} : { step: optionalNumber("step") ?? null }),
      ...(options === undefined ? {} : { options }),
      ...(optionLabels === undefined ? {} : { option_labels: optionLabels }),
      ...(raw.save_path === undefined ? {} : { save_path: optionalText("save_path", 512) ?? null }),
      ...(raw.placeholder === undefined ? {} : { placeholder: optionalText("placeholder") ?? null }),
      ...(raw.managed_by === undefined ? {} : { managed_by: optionalText("managed_by", 256) ?? null }),
      ...(raw.secret === undefined ? {} : { secret: raw.secret === true }),
      ...(raw.is_set === undefined ? {} : { is_set: raw.is_set === true }),
      ...(raw.action === undefined ? {} : { action: optionalText("action", 128) ?? null }),
    });
  }
  return fields;
}

function normalizeService(value: unknown): PluginService | null {
  const raw = record(value);
  const name = safeIdentifier(raw?.name, 40);
  if (!raw || !name || !SERVICE_ID.test(name)) return null;
  const state = SERVICE_STATES.has(raw.state as PluginService["state"]) ? raw.state as PluginService["state"] : "unknown";
  const desired = raw.desired === "running" || raw.desired === "stopped" ? raw.desired : "stopped";
  const error = typeof raw.error === "string" && FIXED_SERVICE_ERRORS.has(raw.error) ? raw.error : null;
  const rawSecrets = Array.isArray(raw.secrets) ? raw.secrets : [];
  const secrets = normalizeBoundedUnique(raw.secrets, PLUGIN_INVENTORY_LIMITS.serviceSecrets, (secret) =>
    typeof secret === "string" && SECRET_NAME.test(secret) ? secret : null,
  );
  const secretsOmitted = (safeInteger(raw.secrets_omitted) ?? 0)
    + Math.max(0, rawSecrets.length - PLUGIN_INVENTORY_LIMITS.serviceSecrets);
  return {
    name,
    state,
    desired,
    pid: safeInteger(raw.pid, 1),
    since: safeNumber(raw.since),
    restarts: safeInteger(raw.restarts) ?? 0,
    heartbeat: safeNumber(raw.heartbeat),
    heartbeat_s: safeNumber(raw.heartbeat_s, Number.MIN_VALUE, 86_400),
    heartbeat_monitor_since: safeNumber(raw.heartbeat_monitor_since),
    stop_deadline: safeNumber(raw.stop_deadline),
    error,
    secrets,
    secrets_omitted: secretsOmitted,
  };
}

function inheritedOmission(raw: Record<string, unknown>, key: string): number {
  return safeInteger(record(raw.omitted)?.[key]) ?? 0;
}

function excessItems(value: unknown, limit: number): number {
  return Array.isArray(value) ? Math.max(0, value.length - limit) : 0;
}

export function pluginContributionOmissionCount(value: unknown): number {
  const raw = record(value);
  if (!raw) return 0;
  return CONTRIBUTION_KINDS.reduce(
    (total, kind) => total + excessItems(raw[kind], PLUGIN_INVENTORY_LIMITS.contributions),
    0,
  );
}

function settingOmissions(...collections: unknown[]): { settings: number; setting_options: number; option_labels: number } {
  const result = { settings: 0, setting_options: 0, option_labels: 0 };
  for (const collection of collections) {
    if (!Array.isArray(collection)) continue;
    result.settings += excessItems(collection, PLUGIN_INVENTORY_LIMITS.settings);
    for (const item of collection) {
      const setting = record(item);
      result.setting_options += excessItems(setting?.options, PLUGIN_INVENTORY_LIMITS.settingOptions);
      const labels = record(setting?.option_labels);
      if (labels)
        result.option_labels += Math.max(0, Object.keys(labels).length - PLUGIN_INVENTORY_LIMITS.optionLabels);
    }
  }
  return result;
}

/** Normalize the untrusted GET /plugins row before it can reach Settings, failure panels or plugin loading. */
export function normalizePluginEntry(value: unknown): PluginEntry | null {
  const raw = record(value);
  const id = safeIdentifier(raw?.id, 44);
  if (!raw || !id || !ID.test(id)) return null;
  const kind = raw.kind === "built-in" || raw.kind === "installed" || raw.kind === "dev" ? raw.kind : "installed";
  const state = PLUGIN_STATES.has(raw.state as PluginEntry["state"]) ? raw.state as PluginEntry["state"] : "off";
  const version = safeText(raw.version, 256);
  const source = safeText(raw.source);
  const commit = typeof raw.commit === "string" && /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/i.test(raw.commit) ? raw.commit : null;
  const suppliedError = typeof raw.error === "string" && FIXED_PLUGIN_ERRORS.has(raw.error) ? raw.error : null;
  const error = suppliedError ?? (state === "error" ? SAFE_PLUGIN_ENTRY_ERROR : null);
  const section = record(raw.settings_section);
  const sectionTitle = safeText(section?.title, 256);
  const webRaw = record(raw.web);
  const webEntry = normalizePluginWebPath(webRaw?.entry);
  const webStyle = webRaw?.style === undefined || webRaw.style === null ? null : normalizePluginWebPath(webRaw.style);
  const webHash = typeof webRaw?.hash === "string" && /^[0-9a-f]{1,64}$/i.test(webRaw.hash) ? webRaw.hash : null;
  const web = webEntry && (webRaw?.style == null || webStyle) && webHash
    ? { entry: webEntry, style: webStyle, hash: webHash }
    : null;
  const rawServices = Array.isArray(raw.services) ? raw.services : [];
  const services = rawServices
    .slice(0, PLUGIN_INVENTORY_LIMITS.services).flatMap((service) => {
      const normalized = normalizeService(service);
      return normalized ? [normalized] : [];
    })
  let invalidUses = 0;
  const uses = normalizePluginUses(raw.uses, () => { invalidUses += 1; });
  if (raw.uses !== undefined && !Array.isArray(raw.uses)) invalidUses += 1;
  const rawGranted = Array.isArray(raw.granted) ? raw.granted : [];
  let invalidGranted = 0;
  const granted = normalizeBoundedUnique(raw.granted, PLUGIN_INVENTORY_LIMITS.uses, (item) => {
    const use = normalizeUse(item);
    if (use === null) invalidGranted += 1;
    return use;
  });
  if (raw.granted !== undefined && !Array.isArray(raw.granted)) invalidGranted += 1;
  const settingsSection = record(raw.settings_section);
  const settingOmissionsFromInput = settingOmissions(raw.settings, settingsSection?.widgets);
  const reportedOmissions = record(raw.omitted);
  const omitted = {
    services: (safeInteger(reportedOmissions?.services) ?? 0)
      + Math.max(0, rawServices.length - PLUGIN_INVENTORY_LIMITS.services),
    uses: (safeInteger(reportedOmissions?.uses) ?? 0)
      + Math.max(0, (Array.isArray(raw.uses) ? raw.uses.length : 0) - PLUGIN_INVENTORY_LIMITS.uses) + invalidUses,
    granted: (safeInteger(reportedOmissions?.granted) ?? 0)
      + Math.max(0, rawGranted.length - PLUGIN_INVENTORY_LIMITS.uses) + invalidGranted,
    settings: (safeInteger(reportedOmissions?.settings) ?? 0) + settingOmissionsFromInput.settings,
    setting_options: (safeInteger(reportedOmissions?.setting_options) ?? 0) + settingOmissionsFromInput.setting_options,
    option_labels: (safeInteger(reportedOmissions?.option_labels) ?? 0) + settingOmissionsFromInput.option_labels,
    contributions: (safeInteger(reportedOmissions?.contributions) ?? 0) + pluginContributionOmissionCount(raw.contributes),
  };
  return {
    id,
    name: safeText(raw.name, 256) ?? id,
    version,
    kind,
    source,
    commit,
    enabled: raw.enabled === true,
    enable_order: safeInteger(raw.enable_order, 1),
    shadowed: raw.shadowed === true,
    state,
    error,
    web,
    contributes: normalizePluginContributes(raw.contributes),
    services,
    settings: normalizeSettings(raw.settings),
    settings_section: settingsSection ? { title: sectionTitle ?? "Settings" } : null,
    uses,
    granted,
    omitted,
  };
}

export type PluginInventory = {
  entries: readonly PluginEntry[];
  error: string | null;
  serverTimeOffsetSeconds: number;
};

// Every listed plugin, including off, shadowed and failed ones. The registry holds only the loaded ones.
let inventory: PluginInventory = { entries: [], error: null, serverTimeOffsetSeconds: 0 };
const inventoryListeners = new Set<() => void>();

function setInventory(next: PluginInventory): void {
  inventory = next;
  for (const listener of inventoryListeners) listener();
}

/** Every listed plugin as the last successful read returned it. */
export const listedPluginEntries = (): readonly PluginEntry[] => inventory.entries;

function subscribeInventory(listener: () => void): () => void {
  inventoryListeners.add(listener);
  return () => {
    inventoryListeners.delete(listener);
  };
}

/** The listed plugins and the error of the last read; a failed read keeps the last good list. */
export function usePluginInventory(): PluginInventory {
  return useSyncExternalStore(
    subscribeInventory,
    () => inventory,
    () => inventory,
  );
}

/** `?plugins=off`: the way back in when a plugin freezes the page. */
export function pluginsOff(search: string): boolean {
  return new URLSearchParams(search).get("plugins") === "off";
}

/** Whether a listed plugin runs on this page: on, not shadowed by a dev folder, unchanged, with something to show. */
export function isActive(entry: PluginEntry): boolean {
  const shows =
    Boolean(entry.web) || Object.values(entry.contributes ?? {}).some((list) => Array.isArray(list) && list.length > 0);
  return ID.test(entry.id) && entry.enabled === true && !entry.shadowed && LOADABLE.has(entry.state) && shows;
}

/** The web UI URL of a plugin's entry module or stylesheet; null for a path outside `web/dist/`. */
export function assetUrl(entry: PluginEntry, file: "entry" | "style", retry = 0): string | null {
  const web = entry.web;
  const path = file === "entry" ? web?.entry : web?.style;
  if (!web || typeof path !== "string" || !path.startsWith("web/dist/") || !ID.test(entry.id)) return null;
  const parts = path.slice("web/dist/".length).split("/");
  if (parts.some((part) => !part || part.startsWith("."))) return null;
  const query = `v=${encodeURIComponent(web.hash)}${retry > 0 ? `&retry=${retry}` : ""}`;
  return `/plugins/${entry.id}/web/${parts.map(encodeURIComponent).join("/")}?${query}`;
}

/** A bundle that carries its own copy of React (its hooks would not work with the app's React). */
export function bundlesOwnReact(source: string): boolean {
  return OWN_REACT.test(source);
}

const IMPORT_SPECIFIERS = [
  /\b(?:import|export)\s*(?:[^;'\"]*?\s*from\s*)?["']([^"']+)["']/g,
  /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g,
];

/** Check the entry and its local module graph; bundled React is often emitted as a shared chunk. */
async function bundleImportsOwnReact(
  pluginId: string,
  entryUrl: string,
  entrySource: string,
  fetchText: (url: string) => Promise<string>,
): Promise<boolean> {
  const origin = "https://kollab-plugin.invalid";
  const rootPath = `/plugins/${pluginId}/web/`;
  const visited = new Set<string>();

  async function inspect(url: string, source: string): Promise<boolean> {
    const resolved = new URL(url, origin);
    if (resolved.origin !== origin || !resolved.pathname.startsWith(rootPath)) return false;
    const key = `${resolved.pathname}${resolved.search}`;
    if (visited.has(key)) return false;
    visited.add(key);
    if (bundlesOwnReact(source)) return true;

    const specifiers = new Set<string>();
    for (const pattern of IMPORT_SPECIFIERS) {
      pattern.lastIndex = 0;
      for (let match = pattern.exec(source); match; match = pattern.exec(source)) specifiers.add(match[1]);
    }
    for (const specifier of specifiers) {
      if (!specifier.startsWith(".") && !specifier.startsWith("/")) continue;
      const dependency = new URL(specifier, resolved);
      if (dependency.origin !== origin || !dependency.pathname.startsWith(rootPath)) continue;
      // Keep the entry's cache key on fetched chunks so a retry checks the same version the browser imports.
      if (!dependency.search) {
        for (const keyName of ["v", "retry"]) {
          const value = resolved.searchParams.get(keyName);
          if (value !== null) dependency.searchParams.set(keyName, value);
        }
      }
      if (await inspect(`${dependency.pathname}${dependency.search}`, await fetchText(`${dependency.pathname}${dependency.search}`)))
        return true;
    }
    return false;
  }

  return inspect(entryUrl, entrySource);
}

export type LoaderDeps = {
  list(): Promise<PluginInventoryResponse>;
  importModule(url: string): Promise<unknown>;
  fetchText(url: string): Promise<string>;
  addStyle?(id: string, href: string): () => void;
  api: Pick<PluginApiDeps, "request" | "stream">;
  timeoutMs?: number;
  /** False lists the plugins but imports and styles none of them (PhoneLink). Defaults to true. */
  loadCode?: boolean;
};

/** What the app lends plugins: switching the open chat's tab and opening a chat. */
export type PluginHost = { openView(key: PluginViewKey): void; openSession(sessionId: string): void };

const NO_HOST: PluginHost = { openView() {}, openSession() {} };
let host = NO_HOST;

/** App registers its view switch and chat opener; returns the unregister. */
export function setPluginHost(next: PluginHost): () => void {
  host = next;
  return () => {
    if (host === next) host = NO_HOST;
  };
}

/** Opens a menu row's, a notice's or `k.navigate`'s target. */
export function openTarget(pluginId: string, target: OpenTarget, registry: PluginRegistry = pluginRegistry): void {
  if ("page" in target) navigateToPluginPage(pluginId, target.page);
  else if ("modal" in target) registry.openModal(pluginId, target.modal);
  else if ("view" in target) host.openView(pluginViewKey(pluginId, target.view));
  else if (/^https?:\/\//i.test(target.url)) window.open(target.url, "_blank", "noopener,noreferrer");
}

type Attempt = { token: symbol; hash: string | null; dispose(): void };

export function createPluginLoader(registry: PluginRegistry, deps: LoaderDeps) {
  const timeoutMs = deps.timeoutMs ?? LOAD_TIMEOUT_MS;
  const attempts = new Map<string, Attempt>();
  const pending = new Set<Promise<void>>();
  let queue: Promise<void> = Promise.resolve();
  let stopped = false;

  const current = (id: string, token: symbol) => attempts.get(id)?.token === token;
  const find = (id: string) => registry.getSnapshot().plugins.find((record) => record.id === id);

  function drop(id: string) {
    attempts.get(id)?.dispose();
    attempts.delete(id);
    registry.remove(id);
  }

  function load(entry: PluginEntry, retries: number, listedOrder: number): Promise<void> {
    attempts.get(entry.id)?.dispose();
    const token = Symbol(entry.id);
    let staged: Contributions = EMPTY_CONTRIBUTIONS;
    let live = false;
    // Timed out, failed, replaced or removed: nothing of this attempt runs any more.
    let abandoned = false;
    let unstyle = () => {};
    const { k, dispose } = createPluginApi(entry, {
      ...deps.api,
      contribute(change) {
        if (!current(entry.id, token)) return;
        if (live) registry.contribute(entry.id, change);
        else staged = change(staged);
      },
      open: (target) => openTarget(entry.id, target, registry),
      openSession: (sessionId) => host.openSession(sessionId),
    });
    attempts.set(entry.id, {
      token,
      hash: entry.web?.hash ?? null,
      dispose() {
        abandoned = true;
        dispose();
        unstyle();
      },
    });
    const web = entry.web;
    registry.put({
      id: entry.id,
      name: entry.name,
      entry,
      listedOrder,
      status: web ? "loading" : "ready",
      error: null,
      retries,
      k: web ? null : k,
      contributions: EMPTY_CONTRIBUTIONS,
    });
    if (!web) {
      live = true;
      return Promise.resolve();
    }
    const url = assetUrl(entry, "entry", retries);
    const work = (async () => {
      if (!url) throw new Error(`${web.entry} is not a file inside web/dist`);
      const source = await deps.fetchText(url);
      if (abandoned) return;
      if (await bundleImportsOwnReact(entry.id, url, source, deps.fetchText))
        throw new Error(`${web.entry} bundles its own React: mark react as external`);
      const module = await deps.importModule(url);
      // A module that arrives after the limit is never set up.
      if (abandoned) return;
      const setup = (module as { default?: unknown } | null)?.default;
      if (typeof setup !== "function") throw new Error(`${web.entry} does not default-export definePlugin(...).`);
      await setup(k);
    })();
    // A failure after the limit has nowhere to go.
    work.catch(() => {});
    let timer: ReturnType<typeof setTimeout> | undefined;
    const limit = new Promise<never>((_, reject) => {
      timer = setTimeout(
        () => reject(new Error(`${entry.name} did not finish loading within ${timeoutMs / 1000} s.`)),
        timeoutMs,
      );
    });
    const settled = Promise.race([work, limit])
      .then(
        () => {
          if (!current(entry.id, token)) return;
          live = true;
          const style = deps.addStyle ? assetUrl(entry, "style") : null;
          if (style) unstyle = deps.addStyle!(entry.id, style);
          registry.update(entry.id, (record) => ({ ...record, status: "ready", error: null, k, contributions: staged }));
        },
        (error: unknown) => {
          // Whatever it still does later reaches nothing.
          abandoned = true;
          dispose();
          if (!current(entry.id, token)) return;
          console.error(`Plugin ${entry.id} web code failed while loading`, error);
          registry.update(entry.id, (record) => ({ ...record, status: "failed", error: SAFE_WEB_PLUGIN_LOAD_ERROR }));
        },
      )
      .finally(() => {
        clearTimeout(timer);
        pending.delete(settled);
      });
    pending.add(settled);
    return settled;
  }

  async function reconcile(): Promise<void> {
    if (stopped) return;
    let entries: PluginEntry[];
    let serverTimeOffsetSeconds: number;
    try {
      const response = await deps.list();
      if (stopped) return;
      const inventoryResponse = record(response);
      if (!inventoryResponse || !Array.isArray(inventoryResponse.plugins))
        throw new Error("Invalid plugin inventory response.");
      const receivedAt = Date.now() / 1000;
      const listed = inventoryResponse.plugins;
      const serverTime = inventoryResponse?.server_time;
      serverTimeOffsetSeconds = inventory.serverTimeOffsetSeconds;
      if (typeof serverTime === "number" && Number.isFinite(serverTime)) {
        const offset = serverTime - receivedAt;
        if (Number.isFinite(offset)) serverTimeOffsetSeconds = offset;
      }
      entries = listed.map((item) => {
        const normalized = normalizePluginEntry(item);
        if (!normalized) throw new Error("Invalid plugin inventory response.");
        return normalized;
      });
    } catch (error) {
      if (stopped) return;
      setInventory({ entries: inventory.entries, error: message(error), serverTimeOffsetSeconds: inventory.serverTimeOffsetSeconds });
      registry.setListed(message(error));
      return;
    }
    setInventory({ entries, error: null, serverTimeOffsetSeconds });
    // With loadCode off (PhoneLink) the list still fills, and no plugin is imported or styled.
    const active = deps.loadCode === false ? [] : entries.filter(isActive);
    const keep = new Set(active.map((entry) => entry.id));
    for (const { id } of registry.getSnapshot().plugins) if (!keep.has(id)) drop(id);
    active.forEach((entry, listedOrder) => {
      // The same build stays as it is; its row reads the new state (Stopping, Failed to Load ...).
      if (find(entry.id) && attempts.get(entry.id)?.hash === (entry.web?.hash ?? null))
        registry.update(entry.id, (record) => ({ ...record, name: entry.name, entry, listedOrder }));
      else void load(entry, 0, listedOrder);
    });
    registry.setListed(null);
  }

  return {
    /** Reads `GET /plugins` and adds, replaces or removes plugins to match. */
    refresh(): Promise<void> {
      if (stopped) return Promise.resolve();
      const run = queue.then(reconcile);
      queue = run.catch(() => {});
      return run;
    },
    /** Imports a plugin again, as `?v=<hash>&retry=<n>`. */
    retry(id: string): Promise<void> {
      const record = find(id);
      if (!record) return Promise.resolve();
      const inputOrder = registry.getSnapshot().plugins.findIndex((item) => item.id === id);
      return load(record.entry, record.retries + 1, record.listedOrder ?? Math.max(inputOrder, 0));
    },
    /** An engine notice (`ctx.notify`) for a loaded plugin. */
    notice(data: unknown) {
      if (!data || typeof data !== "object") return;
      const { plugin_id: id, text, button } = data as Record<string, unknown>;
      if (typeof id !== "string" || !find(id) || typeof text !== "string" || !text.trim() || /[\r\n]/.test(text)) return;
      const action =
        button && typeof button === "object" && typeof (button as { label?: unknown }).label === "string" &&
        isOpenTarget((button as { open?: unknown }).open)
          ? { label: (button as { label: string }).label, open: (button as { open: OpenTarget }).open }
          : undefined;
      const key = nextNoticeKey();
      registry.contribute(id, (current) => ({
        ...current,
        notices: [...current.notices, action ? { key, text, button: action } : { key, text }],
      }));
    },
    /** Resolves once every import in flight has settled. */
    async idle() {
      while (pending.size) await Promise.all([...pending]);
    },
    stop() {
      stopped = true;
      for (const attempt of attempts.values()) attempt.dispose();
      attempts.clear();
    },
  };
}

export type PluginLoader = ReturnType<typeof createPluginLoader>;

let runtime: PluginLoader | null = null;

/** Retry from a Failed to Load or Failed to Render panel. */
export function retryPlugin(id: string): void {
  void runtime?.retry(id);
}

/** Reads `GET /plugins` again; resolves once the list has been updated. */
export function refreshPlugins(): Promise<void> {
  return runtime?.refresh() ?? Promise.resolve();
}

/**
 * Starts plugins for this page: lists them, loads them, and follows `GET /plugins/events` for
 * `plugins-changed` and engine notices. Returns the stop. PhoneLink's phone app lists the plugins but loads none
 * of their code: it cannot `import()` from the computer.
 */
export function startPluginRuntime(api: EngineApi, where: Pick<Location, "pathname" | "search"> = window.location): () => void {
  if (pluginsOff(where.search)) {
    pluginRegistry.setOff();
    return () => {};
  }
  const loader = createPluginLoader(pluginRegistry, {
    list: () => api.json<PluginInventoryResponse>("/plugins", { cache: "no-store" }),
    loadCode: !inPhoneApp(where.pathname),
    importModule: (url) => import(/* @vite-ignore */ url),
    async fetchText(url) {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Could not read ${url.split("?")[0]} (HTTP ${response.status}).`);
      return response.text();
    },
    addStyle(id, href) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      // Not `data-plugin`: that attribute marks only the mount plugin CSS scopes to.
      link.dataset.pluginStyle = id;
      document.head.append(link);
      return () => link.remove();
    },
    api: {
      request: (path, init) => api.request(path, init),
      stream: (path, signal, onEvent, onOpen) => api.streamSse(path, signal, onEvent, onOpen),
    },
  });
  runtime = loader;
  const controller = new AbortController();
  let listed = false;
  const refresh = () => {
    listed = true;
    void loader.refresh();
  };
  void followEvents(
    // The list is read when the stream opens (so no change falls between the two), or once anyway when an
    // engine without the stream refuses it.
    (path, signal, onEvent, onOpen) =>
      api.streamSse(path, signal, onEvent, onOpen).catch((error: unknown) => {
        if (!listed) refresh();
        throw error;
      }),
    "/plugins/events",
    controller.signal,
    (event) => {
      const { topic, data } = (event ?? {}) as { topic?: unknown; data?: unknown };
      if (topic === "plugins-changed") void loader.refresh();
      else if (topic === "notice") loader.notice(data);
    },
    refresh,
  );
  return () => {
    controller.abort();
    loader.stop();
    if (runtime === loader) runtime = null;
  };
}
