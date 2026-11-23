import { useId, useRef, useState, type RefObject } from "react";
import { Plus } from "lucide-react";
import type { EngineApi } from "@/api";
import { serializeDraft, type Draft } from "@/components/panels/FieldRenderer";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { inPhoneApp } from "@/utils/page-route";
import { PLUGIN_TRUST_NOTICE, PluginInstallDialog, describeUses } from "./install";
import { listedPluginEntries, refreshPlugins, retryPlugin, usePluginInventory } from "./loader";
import { PluginBoundary, PluginFailure } from "./mount";
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

/** The failure to show for a plugin: its load error on this page, else the engine's error. Null when none. */
function failureOf(entry: PluginEntry, record: PluginRecord | undefined): string | null {
  if (record?.status === "failed") return record.error ?? "";
  return entry.state === "changed-on-disk" ? null : entry.error;
}

function serviceStatus(service: PluginService): string {
  switch (service.state) {
    case "running": return "Running";
    case "stopping": return "Stopping";
    case "exited": return "Exited";
    case "failed":
    case "crash-stopped": return "Stopped After 5 Crashes";
    case "stopped": return "Stopped";
    case "stop-failed": return "Failed to Stop";
    default: return service.state;
  }
}

function ServiceRow({ service, api, id, canControl, enabled, onError }: {
  service: PluginService; api: EngineApi; id: string; canControl: boolean; enabled: boolean; onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string | null>(null);
  const base = `${pathFor(id)}/services/${encodeURIComponent(service.name)}`;
  const control = async (action: "start" | "stop" | "restart") => {
    setBusy(true);
    try {
      await api.json(`${base}/${action}`, { method: "POST" });
      await refreshPlugins();
    } catch (caught) {
      onError(message(caught));
      await refreshPlugins();
    } finally {
      setBusy(false);
    }
  };
  const showLog = async () => {
    setBusy(true);
    try {
      const response = await api.request(`${base}/log`);
      setLog(await response.text());
    } catch (caught) {
      onError(message(caught));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2 py-1 text-sm">
      <span className="font-medium">{service.name}</span>
      <span className="text-muted-foreground">{serviceStatus(service)}</span>
      {service.error ? <span className="text-destructive break-words">{service.error}</span> : null}
      <div className="ml-auto flex flex-wrap gap-1">
        <Button type="button" variant="ghost" size="xs" className="pointer-coarse:min-h-11" disabled={busy} onClick={() => { void showLog(); }}>View Log</Button>
        {canControl && service.state === "running" ? (
          <>
            {enabled ? <Button type="button" variant="outline" size="xs" className="pointer-coarse:min-h-11" disabled={busy} onClick={() => { void control("restart"); }}>Restart</Button> : null}
            <Button type="button" variant="outline" size="xs" className="pointer-coarse:min-h-11" disabled={busy} onClick={() => { void control("stop"); }}>Stop</Button>
          </>
        ) : null}
        {canControl && service.state === "stop-failed" ? (
          <Button type="button" variant="outline" size="xs" className="pointer-coarse:min-h-11" disabled={busy} onClick={() => { void control("stop"); }}>Retry Stop</Button>
        ) : null}
        {canControl && enabled && service.state !== "running" && service.state !== "stopping" && service.state !== "stop-failed" ? (
          <Button type="button" variant="outline" size="xs" className="pointer-coarse:min-h-11" disabled={busy} onClick={() => { void control("start"); }}>Start</Button>
        ) : null}
      </div>
      <Dialog open={log !== null} onOpenChange={(open) => { if (!open) setLog(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{service.name} Log</DialogTitle>
            <DialogDescription>Most recent redacted output.</DialogDescription>
          </DialogHeader>
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words text-xs">{log || "No log output."}</pre>
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
  return { managed, switchEnabled: live, services: managed && live, updateRemove: managed && live && entry.kind === "installed" };
}

function PluginDetail({ entry, record, api, remote, onUpdate, detailRef }: {
  entry: PluginEntry; record: PluginRecord | undefined; api: EngineApi; remote: boolean; onUpdate: (id: string) => void; detailRef: RefObject<HTMLElement | null>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<"disable" | "remove" | null>(null);
  const { managed, switchEnabled, services, updateRemove } = pluginRowControls(entry, remote);
  const stopping = entry.state === "stopping";
  const failure = failureOf(entry, record);
  const uses = describeUses(entry.uses);
  const usesCount = uses.throughKollab.length + uses.onItsOwn.length + uses.other.length;
  const SettingsView = record?.contributions.settings?.render;
  const perform = async (action: "enable" | "disable" | "remove") => {
    setBusy(true);
    setError(null);
    try {
      await api.json(`${pathFor(entry.id)}/${action}`, { method: "POST" });
      setConfirmation(null);
      await refreshPlugins();
    } catch (caught) {
      setError(message(caught));
      await refreshPlugins();
      // Keep the dialog open while the plugin is still on, so the error shows there; otherwise close it and the page shows the error.
      const current = listedPluginEntries().find((item) => entryKey(item) === entryKey(entry));
      if (action !== "enable" && !current?.enabled) setConfirmation(null);
    } finally {
      setBusy(false);
    }
  };
  const status = failure !== null ? (record?.status === "failed" ? "Failed to Load" : "Error")
    : entry.state === "changed-on-disk" ? "Changed on Disk" : stopping ? "Stopping" : entry.enabled ? "On" : "Off";
  return (
    <section ref={detailRef} tabIndex={-1} aria-label={`${entry.name} details`} className="min-w-0 py-1">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <h2 className="min-w-0 truncate font-semibold">{entry.name}</h2>
        <span className={`text-xs font-medium ${failure !== null || entry.state === "changed-on-disk" ? "text-destructive" : "text-muted-foreground"}`}>{status}</span>
        <label className="inline-flex pointer-coarse:flex pointer-coarse:size-11 pointer-coarse:items-center pointer-coarse:justify-center">
          <Switch
            aria-label={`${entry.name} enabled`}
            checked={entry.enabled}
            disabled={!switchEnabled || busy}
            onCheckedChange={(checked) => { if (checked) void perform("enable"); else setConfirmation("disable"); }}
          />
        </label>
        {updateRemove ? (
          <div className="ml-auto flex flex-wrap gap-2">
            <Button type="button" size="xs" variant="outline" className="pointer-coarse:min-h-11" disabled={busy} onClick={() => onUpdate(entry.id)}>Update</Button>
            <Button type="button" size="xs" variant="ghost" className="pointer-coarse:min-h-11" disabled={busy} onClick={() => setConfirmation("remove")}>Remove</Button>
          </div>
        ) : null}
      </div>
      <p className="text-muted-foreground mt-2 break-words text-xs">
        {entry.kind === "built-in" ? "Built In" : entry.kind === "dev" ? "Development" : "Installed"}
        {managed && entry.source ? ` · ${entry.source}` : ""}
      </p>
      {stopping ? <p role="status" className="text-muted-foreground mt-2 text-sm">Waiting for the plugin to stop.</p> : null}
      {error && confirmation === null ? <p role="alert" className="text-destructive mt-2 break-words text-sm">{error}</p> : null}
      {entry.state === "changed-on-disk" && entry.error ? (
        <p className="text-destructive mt-2 break-words text-sm">{entry.error}. Update or reinstall the plugin to review its files again.</p>
      ) : null}
      <div className="flex min-w-0 flex-col gap-4 py-4">
        {failure !== null ? (
          <PluginFailure error={failure} inset={false} onRetry={() => { if (record) retryPlugin(entry.id); else void refreshPlugins(); }} />
        ) : null}
        {entry.kind === "dev" && entry.source ? <p className="text-muted-foreground break-all text-sm">Development folder: {entry.source}</p> : null}
        {entry.services.length ? (
          <div>
            <h3 className="text-sm font-semibold">Background Processes</h3>
            {entry.services.map((service) => (
              <ServiceRow key={service.name} service={service} api={api} id={entry.id} canControl={services} enabled={entry.enabled} onError={setError} />
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
        {SettingsView && record?.k ? (
          <div data-plugin={entry.id}>
            <PluginBoundary name={entry.name}>
              <SettingsView k={record.k} />
            </PluginBoundary>
          </div>
        ) : null}
      </div>
      <Dialog open={confirmation !== null} onOpenChange={(open) => { if (!open && !busy) setConfirmation(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{confirmation === "remove" ? "Remove" : "Turn off"} {entry.name}?</DialogTitle>
            <DialogDescription>Its background processes will stop. Any working plugin runs must finish before this action can continue.</DialogDescription>
          </DialogHeader>
          {error ? <p role="alert" className="text-destructive break-words text-sm">{error}</p> : null}
          <DialogFooter>
            <Button type="button" variant="outline" className="pointer-coarse:min-h-11" disabled={busy} onClick={() => setConfirmation(null)}>Cancel</Button>
            <Button type="button" className="pointer-coarse:min-h-11" disabled={busy || !confirmation} onClick={() => { if (confirmation) void perform(confirmation); }}>
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
  const detailRef = useRef<HTMLElement>(null);
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
      {inventory.error ? <p role="alert" className="text-destructive mb-4 text-sm">{inventory.error}</p> : null}
      {!snapshot.listed && !snapshot.off ? <p className="text-muted-foreground mb-4 text-sm">Loading plugins…</p> : null}
      <div className="grid min-w-0 gap-5 md:grid-cols-[minmax(10rem,15rem)_minmax(0,1fr)] md:gap-6">
        <nav aria-label="Plugin list" className="min-w-0">
          {groups.filter((group) => group.entries.length || group.title === "Installed").map((group) => (
            <section key={group.title} className="mb-5">
              <div className="mb-2 flex items-center justify-between gap-2">
                <h2 className="text-muted-foreground text-xs font-semibold">{group.title}</h2>
                {group.title === "Installed" && !snapshot.off && !remote ? (
                  <Button type="button" size="xs" variant="outline" className="pointer-coarse:min-h-11" onClick={() => setOperation({ action: "install" })}>
                    <Plus className="size-3" /> Add Plugin
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
                  const status = failure !== null ? "Failed" : entry.state === "changed-on-disk" ? "Changed on Disk" : entry.state === "stopping" ? "Stopping" : entry.enabled ? "On" : "Off";
                  return (
                    <Button
                      key={key}
                      type="button"
                      variant={isSelected ? "secondary" : "ghost"}
                      className="h-auto min-h-12 w-full min-w-0 justify-start gap-2 px-2 py-2 text-left"
                      aria-label={entry.name}
                      aria-describedby={descriptionId}
                      aria-pressed={isSelected}
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
                              <span>{serviceStatus(service)}</span>
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
            detailRef={detailRef}
            onUpdate={(pluginId) => setOperation({ action: "update", pluginId })}
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
        />
      ) : null}
    </div>
  );
}
