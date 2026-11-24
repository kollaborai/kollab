import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from "react";
import type { EngineApi } from "@/api";
import { FieldRenderer, serializeDraft, type Draft } from "@/components/panels/FieldRenderer";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { inPhoneApp } from "@/utils/page-route";
import { PLUGIN_TRUST_NOTICE, PluginInstallDialog, describeUses } from "./install";
import { listedPluginEntries, refreshPlugins, retryPlugin, usePluginInventory } from "./loader";
import { PluginMount, PluginFailure } from "./mount";
import { type PluginRecord, usePlugins } from "./registry";
import type { PluginEntry, PluginService } from "./sdk";

const message = (error: unknown) => (error instanceof Error ? error.message : String(error));
const pathFor = (id: string) => `/plugins/${encodeURIComponent(id)}`;
const entryKey = (entry: PluginEntry) => `${entry.kind}:${entry.id}:${entry.source ?? ""}`;

/** Convert panel field paths to the keys expected by the plugin settings API. */
export function settingsValues(entry: Pick<PluginEntry, "id" | "kind">, draft: Draft): Record<string, unknown> {
  const values = serializeDraft(draft);
  if (entry.kind === "built-in") return values;
  const prefix = `plugin-values.${entry.id}.`;
  return Object.fromEntries(Object.entries(values).map(([path, value]) => {
    if (!path.startsWith(prefix) || path.length === prefix.length) {
      throw new Error("Invalid plugin setting path; reload and try again");
    }
    return [path.slice(prefix.length), value];
  }));
}

async function savePluginSettings(api: EngineApi, entry: Pick<PluginEntry, "id" | "kind">, draft: Draft): Promise<void> {
  await api.json(`/plugins/${encodeURIComponent(entry.id)}/settings?kind=${encodeURIComponent(entry.kind)}`, {
    method: "PUT",
    body: JSON.stringify(settingsValues(entry, draft)),
  });
}

/** The failure to show for a plugin: its load error on this page, else the engine's error. Null when none. */
function failureOf(entry: PluginEntry, record: PluginRecord | undefined): string | null {
  if (record?.status === "failed") return record.error ?? "";
  return entry.state === "changed-on-disk" ? null : entry.error;
}

function elapsedLabel(seconds: number): string {
  const totalMinutes = Math.max(1, Math.floor(seconds / 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours ? `${hours} h${minutes ? ` ${minutes} min` : ""}` : `${totalMinutes} min`;
}

function serviceStatus(service: PluginService, now: number): string {
  if (service.state === "running") {
    const since = typeof service.since === "number" ? service.since : null;
    const heartbeat = typeof service.heartbeat === "number" ? service.heartbeat : null;
    const monitorSince = typeof service.heartbeat_monitor_since === "number" ? service.heartbeat_monitor_since : null;
    const heartbeatBaseline = heartbeat === null ? monitorSince
      : monitorSince === null ? heartbeat
      : Math.max(heartbeat, monitorSince);
    const interval = service.heartbeat_s;
    if (typeof interval === "number" && interval > 0 && heartbeatBaseline !== null && now - heartbeatBaseline > interval)
      return `Running · No Heartbeat for ${elapsedLabel(now - heartbeatBaseline)}`;
    return since === null ? "Running" : `Running · ${elapsedLabel(now - since)}`;
  }
  switch (service.state) {
    case "starting": return "Starting";
    case "stopping": return "Stopping";
    case "backoff": return "Restarting";
    case "exited": return "Exited";
    case "failed":
    case "crash-stopped": return "Stopped After 5 Crashes";
    case "stopped": return "Stopped";
    case "stop-failed": return "Failed to Stop";
    case "error": return "Failed to Start";
    case "unknown": return "Unknown";
    default: return "Unknown";
  }
}

function pluginStatus(entry: PluginEntry, record: PluginRecord | undefined): string {
  const failure = failureOf(entry, record);
  if (failure !== null)
    return record?.status === "failed" || entry.state === "error" ? "Failed to Load" : "Error";
  return entry.shadowed ? "Shadowed by a Dev Folder"
    : entry.state === "changed-on-disk" ? "Changed on Disk"
    : entry.state === "stopping" ? "Stopping"
    : entry.state === "stop-failed" ? "Failed to Stop"
    : entry.enabled ? "On" : "Off";
}

export function pluginInventoryOmissionSummary(entry: Pick<PluginEntry, "omitted" | "services">): string | null {
  const serviceSecretCount = entry.services.reduce((total, service) => total + service.secrets_omitted, 0);
  const omitted = [
    [entry.omitted.services, "service"],
    [entry.omitted.uses, "use declaration"],
    [entry.omitted.granted, "granted capability"],
    [entry.omitted.settings, "setting"],
    [entry.omitted.setting_options, "setting option"],
    [entry.omitted.option_labels, "option label"],
    [entry.omitted.contributions, "contribution declaration"],
    [serviceSecretCount, "service secret"],
  ] as const;
  const details = omitted
    .filter(([count]) => count > 0)
    .map(([count, label]) => `${count} ${label}${count === 1 ? "" : "s"}`);
  return details.length ? `Inventory omitted: ${details.join(", ")}.` : null;
}

function ServiceRow({ service, api, id, now, canControl, enabled, disabled, operationBusy, onBusyChange, onError }: {
  service: PluginService; api: EngineApi; id: string; now: number; canControl: boolean; enabled: boolean; disabled: boolean; operationBusy: boolean;
  onBusyChange: (busy: boolean) => void; onError: (message: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string | null>(null);
  const secrets = Array.isArray(service.secrets)
    ? service.secrets.filter((secret): secret is string => typeof secret === "string")
    : [];
  const logOpener = useRef<HTMLElement | null>(null);
  const restoreLogFocus = (event: Event) => {
    if (!logOpener.current?.isConnected) return;
    event.preventDefault();
    logOpener.current.focus();
  };
  const base = `${pathFor(id)}/services/${encodeURIComponent(service.name)}`;
  const control = async (action: "start" | "stop" | "restart") => {
    setBusy(true);
    onBusyChange(true);
    onError(null);
    try {
      await api.json(`${base}/${action}`, { method: "POST" });
      await refreshPlugins();
      onError(null);
    } catch (caught) {
      onError(message(caught));
      await refreshPlugins();
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  };
  const showLog = async () => {
    setBusy(true);
    onBusyChange(true);
    onError(null);
    try {
      const response = await api.request(`${base}/log`);
      setLog(await response.text());
    } catch (caught) {
      onError(message(caught));
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  };
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2 py-1 text-sm">
      <span className="font-medium">{service.name}</span>
      <span className="text-muted-foreground">{serviceStatus(service, now)}</span>
      {secrets.length ? <span className="text-muted-foreground break-words">Receives Secrets: {secrets.join(", ")}</span> : null}
      {service.error ? <span className="text-destructive break-words">{service.error}</span> : null}
      <div className="ml-auto flex flex-wrap gap-1">
        <Button type="button" variant="ghost" size="xs" className="pointer-coarse:min-h-11" aria-label={`View Log ${service.name}`} disabled={busy || disabled || operationBusy} onClick={(event) => { logOpener.current = event.currentTarget; void showLog(); }}>View Log</Button>
        {canControl && service.state === "running" ? (
          <>
            {enabled ? <Button type="button" variant="outline" size="xs" className="pointer-coarse:min-h-11" aria-label={`Restart ${service.name}`} disabled={busy || disabled || operationBusy} onClick={() => { void control("restart"); }}>Restart</Button> : null}
            <Button type="button" variant="outline" size="xs" className="pointer-coarse:min-h-11" aria-label={`Stop ${service.name}`} disabled={busy || disabled || operationBusy} onClick={() => { void control("stop"); }}>Stop</Button>
          </>
        ) : null}
        {canControl && service.state === "stop-failed" ? (
          <Button type="button" variant="outline" size="xs" className="pointer-coarse:min-h-11" aria-label={`Retry Stop ${service.name}`} disabled={busy || disabled || operationBusy} onClick={() => { void control("stop"); }}>Retry Stop</Button>
        ) : null}
        {canControl && enabled && ["stopped", "exited", "crash-stopped"].includes(service.state) ? (
          <Button type="button" variant="outline" size="xs" className="pointer-coarse:min-h-11" aria-label={`Start ${service.name}`} disabled={busy || disabled || operationBusy} onClick={() => { void control("start"); }}>Start</Button>
        ) : null}
      </div>
      <Dialog open={log !== null} onOpenChange={(open) => { if (!open) setLog(null); }}>
        <DialogContent onCloseAutoFocus={restoreLogFocus}>
          <DialogHeader>
            <DialogTitle>{service.name} Log</DialogTitle>
            <DialogDescription>Most recent redacted output.</DialogDescription>
          </DialogHeader>
          <pre role="region" tabIndex={0} aria-label={`${service.name} log`} className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words text-xs">{log || "No log output."}</pre>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * The controls a plugin's Settings detail offers. Every plugin gets the switch; built-in services and removal 404
 * on the engine. Over PhoneLink (`remote`) every row is read-only.
 */
export function pluginRowControls(entry: Pick<PluginEntry, "kind" | "shadowed" | "state">, remote = false) {
  const managed = entry.kind !== "built-in";
  const live = !remote && !entry.shadowed && entry.state !== "stopping";
  return {
    managed,
    switchEnabled: live && entry.kind !== "dev" && entry.state !== "changed-on-disk",
    services: managed && live && entry.state !== "changed-on-disk",
    updateRemove: managed && live && entry.kind === "installed",
  };
}

function PluginDetail({ entry, record, api, remote, now, onUpdate, onBusyChange, onRemoved, operationBusy, detailRef }: {
  entry: PluginEntry; record: PluginRecord | undefined; api: EngineApi; remote: boolean; now: number; onUpdate: (id: string, opener: HTMLElement) => void;
  onBusyChange: (busy: boolean) => void; onRemoved: () => void; operationBusy: boolean; detailRef: RefObject<HTMLElement | null>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<"disable" | "remove" | null>(null);
  const confirmationOpener = useRef<HTMLElement | null>(null);
  const restoreConfirmationFocus = (event: Event) => {
    if (!confirmationOpener.current?.isConnected) return;
    event.preventDefault();
    confirmationOpener.current.focus();
  };
  const [settingsDraft, setSettingsDraft] = useState<Draft>({});
  const { managed, switchEnabled, services, updateRemove } = pluginRowControls(entry, remote);
  const stopping = entry.state === "stopping";
  const failure = failureOf(entry, record);
  const uses = describeUses(entry.uses);
  const usesCount = uses.throughKollab.length + uses.onItsOwn.length + uses.other.length;
  const inventoryOmissionSummary = pluginInventoryOmissionSummary(entry);
  const SettingsView = record?.contributions.settings?.render;
  const settings = entry.settings ?? [];
  const setOperationBusy = (value: boolean) => {
    setBusy(value);
    onBusyChange(value);
  };
  const saveSettings = async () => {
    setOperationBusy(true);
    setError(null);
    try {
      await savePluginSettings(api, entry, settingsDraft);
      setSettingsDraft({});
      await refreshPlugins();
    } catch (caught) {
      setError(message(caught));
    } finally {
      setOperationBusy(false);
    }
  };
  const perform = async (action: "enable" | "disable" | "remove") => {
    setOperationBusy(true);
    setError(null);
    try {
      await api.json(`${pathFor(entry.id)}/${action}`, { method: "POST" });
      setConfirmation(null);
      await refreshPlugins();
      if (action === "remove") onRemoved();
    } catch (caught) {
      setError(message(caught));
      await refreshPlugins();
      // Keep the dialog open while the plugin is still on, so the error shows there; otherwise close it and the page shows the error.
      const current = listedPluginEntries().find((item) => entryKey(item) === entryKey(entry));
      if (action !== "enable" && !current?.enabled) setConfirmation(null);
    } finally {
      setOperationBusy(false);
    }
  };
  const status = pluginStatus(entry, record);
  return (
    <section ref={detailRef} tabIndex={-1} aria-label={`${entry.name} details`} className="min-w-0 py-1">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <h2 className="min-w-0 truncate font-semibold">{entry.name}</h2>
        <span className={`text-xs font-medium ${failure !== null || entry.state === "changed-on-disk" || entry.state === "stop-failed" ? "text-destructive" : "text-muted-foreground"}`}>{status}</span>
        {entry.kind !== "dev" ? (
          <label className="inline-flex pointer-coarse:flex pointer-coarse:size-11 pointer-coarse:items-center pointer-coarse:justify-center"
            onPointerDown={(event) => {
              const trigger = event.currentTarget.querySelector("button");
              if (trigger) confirmationOpener.current = trigger;
            }}>
            <Switch
              aria-label={`${entry.name} enabled`}
              checked={entry.enabled}
              disabled={!switchEnabled || busy || operationBusy}
              onCheckedChange={(checked) => {
                if (checked) void perform("enable");
                else {
                  const active = document.activeElement;
                  confirmationOpener.current = active instanceof HTMLElement && active !== document.body
                    ? active : confirmationOpener.current;
                  setError(null);
                  setConfirmation("disable");
                }
              }}
            />
          </label>
        ) : null}
        {updateRemove ? (
          <div className="ml-auto flex flex-wrap gap-2">
            <Button type="button" size="xs" variant="outline" className="pointer-coarse:min-h-11" disabled={busy || operationBusy} onClick={(event) => onUpdate(entry.id, event.currentTarget)}>Update</Button>
            <Button type="button" size="xs" variant="ghost" className="pointer-coarse:min-h-11" disabled={busy || operationBusy} onClick={(event) => { confirmationOpener.current = event.currentTarget; setError(null); setConfirmation("remove"); }}>Remove</Button>
          </div>
        ) : null}
      </div>
      <p className="text-muted-foreground mt-2 break-words text-xs">
        {entry.kind === "built-in" ? "Built In" : entry.kind === "dev" ? "Development" : "Installed"}
        {managed && entry.source ? ` · ${entry.source}` : ""}
      </p>
      {inventoryOmissionSummary ? <p role="status" className="text-muted-foreground text-xs">{inventoryOmissionSummary}</p> : null}
      {stopping ? <p role="status" className="text-muted-foreground mt-2 text-sm">Waiting for the plugin to stop.</p> : null}
      {error && confirmation === null ? <p role="alert" className="text-destructive mt-2 break-words text-sm">{error}</p> : null}
      {entry.state === "changed-on-disk" && entry.error ? (
        <p className="text-destructive mt-2 break-words text-sm">{entry.error}. Update or reinstall the plugin to review its files again.</p>
      ) : null}
      <div className="flex min-w-0 flex-col gap-4 py-4">
        {failure !== null ? (
          <PluginFailure error={failure} inset={false} onRetry={() => { if (record) retryPlugin(entry.id); else void refreshPlugins(); }} />
        ) : null}
        {entry.kind === "dev" && entry.source ? <p className="text-muted-foreground break-all text-sm">Development Folder: {entry.source}</p> : null}
        {(entry.kind === "installed" || entry.kind === "dev") && entry.version ? <p className="text-muted-foreground break-words text-xs">Version: {entry.version}</p> : null}
        {entry.services.length ? (
          <div>
            <h3 className="text-sm font-semibold">Background Processes</h3>
            {entry.services.map((service) => (
              <ServiceRow key={service.name} service={service} api={api} id={entry.id} now={now} canControl={services && !operationBusy} enabled={entry.enabled} disabled={busy} operationBusy={operationBusy} onBusyChange={onBusyChange} onError={setError} />
            ))}
          </div>
        ) : null}
        {usesCount ? (
          <div>
            <h3 className="text-sm font-semibold">What It Uses</h3>
            {([["Through Kollab", uses.throughKollab], ["On Its Own", uses.onItsOwn], ["Other Declarations", uses.other]] as const).map(([label, values]) =>
              values.length ? (
                <div key={label} className="mt-2 text-sm">
                  <h4 className="font-medium">{label}</h4>
                  <ul className="text-muted-foreground list-disc pl-5">
                    {values.map((value, index) => <li key={index} className="break-words">{value}</li>)}
                  </ul>
                </div>
              ) : null,
            )}
            <p className="text-muted-foreground mt-2 text-xs">{PLUGIN_TRUST_NOTICE}</p>
          </div>
        ) : null}
        {settings.length ? (
          <div data-plugin={entry.id} className="min-w-0">
            <h3 className="text-sm font-semibold">{entry.settings_section?.title ?? "Settings"}</h3>
            {settings.map((field) => (
              <FieldRenderer
                key={field.path}
                field={field}
                draft={settingsDraft}
                disabled={remote || busy || operationBusy || entry.state === "changed-on-disk"}
                onChange={setSettingsDraft}
              />
            ))}
            {!remote ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="pointer-coarse:min-h-11"
                disabled={busy || operationBusy || !Object.keys(settingsDraft).length}
                onClick={() => { void saveSettings(); }}
              >
                Save
              </Button>
            ) : null}
          </div>
        ) : null}
        {SettingsView && record?.k ? (
          <PluginMount record={record}>
            <SettingsView k={record.k} />
          </PluginMount>
        ) : null}
      </div>
      <Dialog open={confirmation !== null} onOpenChange={(open) => { if (!open && !busy && !operationBusy) setConfirmation(null); }}>
        <DialogContent onCloseAutoFocus={restoreConfirmationFocus}>
          <DialogHeader>
            <DialogTitle>{confirmation === "remove" ? "Remove" : "Turn Off"} {entry.name}?</DialogTitle>
            <DialogDescription>{confirmation === "remove" ? "Background processes will stop. Cancel active plugin agent runs first; plugin requests must finish before removal." : "Background processes will stop. Cancel active plugin agent runs before turning it off."}</DialogDescription>
          </DialogHeader>
          {error ? <p role="alert" className="text-destructive break-words text-sm">{error}</p> : null}
          <DialogFooter>
            <Button type="button" variant="outline" className="pointer-coarse:min-h-11" disabled={busy || operationBusy} onClick={() => setConfirmation(null)}>Cancel</Button>
            <Button type="button" variant="destructive" className="pointer-coarse:min-h-11" disabled={busy || operationBusy || !confirmation} onClick={() => { if (confirmation) void perform(confirmation); }}>
              {confirmation === "remove" ? "Remove" : "Turn Off"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

/** Settings > Plugins: every plugin the engine lists (installed, development, built in), its detail, and Add Plugin. */
export function PluginsSettings({ api }: { api: EngineApi }) {
  const snapshot = usePlugins();
  const inventory = usePluginInventory();
  const listId = useId();
  const [operation, setOperation] = useState<{ action: "install" | "update"; pluginId?: string } | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [busyPluginKey, setBusyPluginKey] = useState<string | null>(null);
  const [focusAfterRemoval, setFocusAfterRemoval] = useState(false);
  const [browserNow, setBrowserNow] = useState(() => Date.now() / 1000);
  const now = browserNow + inventory.serverTimeOffsetSeconds;
  const operationOpener = useRef<HTMLElement | null>(null);
  const detailRef = useRef<HTMLElement>(null);
  const listRef = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    if (!focusAfterRemoval) return;
    (detailRef.current ?? listRef.current)?.focus();
    setFocusAfterRemoval(false);
  }, [focusAfterRemoval]);
  const restoreOperationFocus = (event: Event) => {
    if (!operationOpener.current?.isConnected) return;
    event.preventDefault();
    operationOpener.current.focus();
  };
  useEffect(() => {
    const timer = window.setInterval(() => setBrowserNow(Date.now() / 1000), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const reportPluginBusy = (key: string, busy: boolean) => {
    setBusyPluginKey((current) => busy ? key : current === key ? null : current);
  };
  const records = new Map<string, PluginRecord>(snapshot.plugins.map((record) => [record.id, record]));
  const entries = inventory.entries;
  const groups = [
    { title: "Installed", entries: entries.filter((entry) => entry.kind === "installed") },
    { title: "Development", entries: entries.filter((entry) => entry.kind === "dev") },
    { title: "Built In", entries: entries.filter((entry) => entry.kind === "built-in") },
  ];
  const selected = entries.find((entry) => entryKey(entry) === selectedKey) ?? entries[0];
  const remote = inPhoneApp(window.location.pathname);
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
      {snapshot.off ? <p className="text-muted-foreground mb-4 text-sm">Plugins are off for this page. Remove <code>?plugins=off</code> and reload.</p> : null}
      {remote ? <p className="text-muted-foreground mb-4 text-sm">Manage plugins on this computer</p> : null}
      {inventory.error ? <p role="alert" className="text-destructive mb-4 break-words text-sm">{inventory.error}</p> : null}
      {!snapshot.listed && !snapshot.off ? <p className="text-muted-foreground mb-4 text-sm">Loading plugins…</p> : null}
      <div className="grid min-w-0 gap-5 md:grid-cols-[minmax(10rem,15rem)_minmax(0,1fr)] md:gap-6">
        <nav ref={listRef} tabIndex={-1} aria-label="Plugin list" className="min-w-0">
          {groups.filter((group) => group.entries.length || group.title === "Installed").map((group) => (
            <section key={group.title} className="mb-5">
              <div className="mb-2 flex items-center justify-between gap-2">
                <h2 className="text-muted-foreground text-xs font-semibold">{group.title}</h2>
                {group.title === "Installed" && !snapshot.off && !remote ? (
                  <Button type="button" size="xs" variant="outline" className="pointer-coarse:min-h-11" disabled={busyPluginKey !== null} onClick={(event) => { operationOpener.current = event.currentTarget; setOperation({ action: "install" }); }}>
                  Add Plugin
                  </Button>
                ) : null}
              </div>
              {group.title === "Installed" && !group.entries.length && snapshot.listed && !inventory.error && !snapshot.off ? (
                <p className="text-muted-foreground mb-2 text-sm">No plugins yet.</p>
              ) : null}
              <div className="flex flex-col gap-1">
                {group.entries.map((entry) => {
                  const key = entryKey(entry);
                  const isSelected = selected !== undefined && entryKey(selected) === key;
                  const descriptionId = `${listId}-${entry.kind}-${entry.id}`;
                  const failure = failureOf(entry, records.get(entry.id));
                  const status = pluginStatus(entry, records.get(entry.id));
                  return (
                    <Button
                      key={key}
                      type="button"
                      variant={isSelected ? "secondary" : "ghost"}
                      className="h-auto min-h-12 w-full min-w-0 justify-start gap-2 whitespace-normal px-2 py-2 text-left"
                      aria-label={entry.name}
                      aria-describedby={descriptionId}
                      aria-pressed={isSelected}
                      disabled={busyPluginKey !== null}
                      onClick={() => {
                        setSelectedKey(key);
                        if (window.matchMedia("(width < 48rem)").matches) {
                          requestAnimationFrame(() => {
                            detailRef.current?.scrollIntoView({ block: "start" });
                            detailRef.current?.focus({ preventScroll: true });
                          });
                        }
                      }}
                    >
                      <span className="flex min-w-0 flex-col items-start">
                        <span className="max-w-full truncate font-medium">{entry.name}</span>
                        <span id={descriptionId} className="text-muted-foreground flex max-w-full flex-col items-start text-xs">
                          <span>{status}</span>
                          {entry.services.map((service) => (
                            <span key={service.name} className="max-w-full break-words">
                              {entry.services.length > 1 ? <span>{service.name}: </span> : null}
                              <span>{serviceStatus(service, now)}</span>
                            </span>
                          ))}
                        </span>
                      </span>
                    </Button>
                  );
                })}
              </div>
            </section>
          ))}
        </nav>
        {selected ? (
          <PluginDetail
            key={entryKey(selected)}
            entry={selected}
            record={records.get(selected.id)}
            api={api}
            remote={remote}
            now={now}
            operationBusy={busyPluginKey === entryKey(selected)}
            onBusyChange={(busy) => reportPluginBusy(entryKey(selected), busy)}
            onRemoved={() => setFocusAfterRemoval(true)}
            detailRef={detailRef}
            onUpdate={(pluginId, opener) => {
              operationOpener.current = opener;
              setOperation({ action: "update", pluginId });
            }}
          />
        ) : null}
      </div>
      {operation ? (
        <PluginInstallDialog
          api={api}
          action={operation.action}
          pluginId={operation.pluginId}
          onClose={() => setOperation(null)}
          onApplied={() => refreshPlugins()}
          onRestoreFocus={restoreOperationFocus}
        />
      ) : null}
    </div>
  );
}
