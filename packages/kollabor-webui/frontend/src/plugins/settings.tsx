import { useState } from "react";
import { Box, Plus } from "lucide-react";
import type { EngineApi, PanelField, Session } from "@/api";
import { FieldRenderer, type Draft } from "@/components/panels/FieldRenderer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { PluginRenderBoundary, usePluginSnapshot } from "./components";
import type { PluginEntry, PluginHost } from "./host";
import { PluginInstallDialog } from "./install";

type PluginService = { name: string; state: string; desired?: string; since?: number | null; restarts?: number; heartbeat?: number | null; error?: string | null };
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const pathFor = (id: string) => `/plugins/${encodeURIComponent(id)}`;

function ServiceRow({ service, api, id, canManage, refresh, onError }: {
  service: PluginService; api: EngineApi; id: string; canManage: boolean; refresh: () => Promise<void>; onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string | null>(null);
  const state = service.state === "running" ? "Running" : service.state === "stopping" ? "Stopping" :
    service.state === "exited" ? "Exited" : service.state === "failed" || service.state === "crash_stopped" ? "Stopped After 5 Crashes" :
    service.state === "stopped" ? "Stopped" : service.state;
  const control = async (action: string) => {
    setBusy(true);
    try { await api.json(`${pathFor(id)}/services/${encodeURIComponent(service.name)}/${action}`, { method: "POST" }); await refresh(); }
    catch (error) { onError(message(error)); } finally { setBusy(false); }
  };
  const showLog = async () => {
    setBusy(true);
    try { const response = await api.request(`${pathFor(id)}/services/${encodeURIComponent(service.name)}/log`); setLog(await response.text()); }
    catch (error) { onError(message(error)); } finally { setBusy(false); }
  };
  return <div className="flex min-w-0 flex-wrap items-center gap-2 py-1 text-sm">
    <span className="font-medium">{service.name}</span><span className="text-muted-foreground">{state}</span>
    {service.error ? <span className="text-destructive break-words">{service.error}</span> : null}
    <div className="ml-auto flex flex-wrap gap-1">
      <Button type="button" variant="ghost" size="xs" disabled={busy} onClick={() => { void showLog(); }}>View Log</Button>
      {canManage && service.state === "running" ? <><Button type="button" variant="outline" size="xs" disabled={busy} onClick={() => { void control("restart"); }}>Restart</Button>
        <Button type="button" variant="outline" size="xs" disabled={busy} onClick={() => { void control("stop"); }}>Stop</Button></> : null}
      {canManage && service.state !== "running" && service.state !== "stopping" ? <Button type="button" variant="outline" size="xs" disabled={busy} onClick={() => { void control("start"); }}>Start</Button> : null}
    </div>
    <Dialog open={log !== null} onOpenChange={open => { if (!open) setLog(null); }}><DialogContent>
      <DialogHeader><DialogTitle>{service.name} Log</DialogTitle><DialogDescription>Most recent redacted output.</DialogDescription></DialogHeader>
      <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words text-xs">{log || "No log output."}</pre>
    </DialogContent></Dialog>
  </div>;
}

function PluginDetail({ entry, host, api, session, canManage, onUpdate }: { entry: PluginEntry; host: PluginHost; api: EngineApi; session?: Session; canManage: boolean; onUpdate: (id: string) => void }) {
  const snapshot = usePluginSnapshot(host);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<"disable" | "remove" | null>(null);
  const fields = (entry.settings ?? []).filter((field): field is PanelField => Boolean(field && typeof field === "object" && typeof (field as PanelField).path === "string"));
  const services = (entry.services ?? []).filter((item): item is PluginService => Boolean(item && typeof item === "object" && typeof (item as PluginService).name === "string"));
  const plugin = snapshot.registry.plugins.find(item => item.id === entry.id);
  const context = host.getContext(entry.id);
  const custom = plugin?.settings[0];
  const failure = snapshot.errors[entry.id] ?? (entry.state === "changed-on-disk" ? null : entry.error);
  const canChange = canManage && !entry.read_only && !entry.required && !entry.shadowed;
  const perform = async (action: "enable" | "disable" | "remove") => {
    setBusy(true); setError(null);
    try { await api.json(`${pathFor(entry.id)}/${action}`, { method: "POST" }); setConfirmation(null); await host.refresh(); }
    catch (caught) { setError(message(caught)); } finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true); setError(null);
    try { await api.json(`${pathFor(entry.id)}/settings`, { method: "PUT", body: JSON.stringify(draft) }); setDraft({}); await host.refresh(); }
    catch (caught) { setError(message(caught)); } finally { setBusy(false); }
  };
  const status = failure ? (snapshot.renderFailed.includes(entry.id) ? "Failed to Render" : "Failed to Load") :
    entry.state === "changed-on-disk" ? "Changed on Disk" : entry.state === "stopping" ? "Stopping" : entry.enabled ? "On" : "Off";
  return <section aria-label={`${entry.name} details`} className="min-w-0 py-1">
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <h2 className="min-w-0 truncate font-semibold">{entry.name}</h2>
      <Badge variant={failure || entry.state === "changed-on-disk" ? "destructive" : "secondary"}>{status}</Badge>
      {canManage && !entry.read_only ? <Switch aria-label={`${entry.name} enabled`} checked={entry.enabled} disabled={!canChange || busy}
        onCheckedChange={checked => { if (checked) void perform("enable"); else setConfirmation("disable"); }} /> : null}
      {canChange && entry.kind === "installed" ? <div className="ml-auto flex flex-wrap gap-2"><Button type="button" size="xs" variant="outline" disabled={busy} onClick={() => onUpdate(entry.id)}>Update</Button><Button type="button" size="xs" variant="ghost" disabled={busy} onClick={() => setConfirmation("remove")}>Remove</Button></div> : null}
    </div>
    <p className="text-muted-foreground mt-2 break-words text-xs">{entry.kind === "built-in" ? "Built In" : entry.kind === "dev" ? "Development" : "Installed"}{entry.source ? ` · ${entry.source}` : ""}</p>
    {error ? <p role="alert" className="text-destructive mt-2 break-words text-sm">{error}</p> : null}
    {entry.state === "changed-on-disk" && entry.error ? <p className="text-destructive mt-2 break-words text-sm">{entry.error}. Update or reinstall the plugin to review its files again.</p> : null}
    <div className="flex min-w-0 flex-col gap-4 py-4">
      {failure ? <div className="flex flex-wrap gap-2"><Button type="button" size="xs" onClick={() => { void host.retry(entry.id); }}>Retry</Button><details className="text-sm"><summary>View Error</summary><pre className="whitespace-pre-wrap break-words">{failure}</pre></details></div> : null}
      {entry.kind === "dev" && entry.source ? <p className="text-muted-foreground break-all text-sm">Development folder: {entry.source}</p> : null}
      {services.length ? <div><h3 className="text-sm font-semibold">Background Processes</h3>{services.map(service => <ServiceRow key={service.name} service={service} api={api} id={entry.id} canManage={canChange} refresh={() => host.refresh()} onError={setError} />)}</div> : null}
      {entry.uses?.length ? <div><h3 className="text-sm font-semibold">What it uses</h3><ul className="text-muted-foreground list-disc pl-5 text-sm">{entry.uses.map((value, i) => <li key={i}>{typeof value === "string" ? value : JSON.stringify(value)}</li>)}</ul></div> : null}
      {fields.length ? <div className="flex flex-col gap-3"><h3 className="text-sm font-semibold">Settings</h3>{fields.map(field => <FieldRenderer key={field.path} field={field} draft={draft} disabled={!canManage || busy} onChange={setDraft} />)}
        {canManage ? <Button type="button" size="sm" className="self-start" disabled={busy || !Object.keys(draft).length} onClick={() => { void save(); }}>Save Settings</Button> : null}</div> : null}
      {custom && context ? <div data-plugin={entry.id}><PluginRenderBoundary name={entry.name} onError={error => host.reportRenderError(entry.id, error)} onRetry={() => { void host.retry(entry.id); }} onTurnOff={() => setConfirmation("disable")}>
        <custom.render k={context} session={session} />
      </PluginRenderBoundary></div> : null}
    </div>
    <Dialog open={confirmation !== null} onOpenChange={open => { if (!open && !busy) setConfirmation(null); }}><DialogContent>
      <DialogHeader><DialogTitle>{confirmation === "remove" ? "Remove" : "Turn off"} {entry.name}?</DialogTitle><DialogDescription>Its background processes will stop. Any working plugin runs must finish before this action can continue.</DialogDescription></DialogHeader>
      <DialogFooter><Button type="button" variant="outline" disabled={busy} onClick={() => setConfirmation(null)}>Cancel</Button><Button type="button" disabled={busy || !confirmation} onClick={() => { if (confirmation) void perform(confirmation); }}>{confirmation === "remove" ? "Remove" : "Turn Off"}</Button></DialogFooter>
    </DialogContent></Dialog>
  </section>;
}

export function PluginsSettings({ host, api, session, canManage }: { host: PluginHost; api: EngineApi; session?: Session; canManage: boolean }) {
  const snapshot = usePluginSnapshot(host);
  const [operation, setOperation] = useState<{ action: "install" | "update"; pluginId?: string } | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const entryKey = (entry: PluginEntry) => `${entry.kind}:${entry.id}:${entry.source ?? ""}`;
  const groups = [
    { title: "Installed", entries: snapshot.entries.filter(entry => entry.kind === "installed") },
    { title: "Development", entries: snapshot.entries.filter(entry => entry.kind === "dev") },
    { title: "Built In", entries: snapshot.entries.filter(entry => entry.kind === "built-in") },
  ];
  const entries = groups.flatMap(group => group.entries);
  const selected = entries.find(entry => entryKey(entry) === selectedKey) ?? entries[0];
  return <div data-plugin className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
    {!canManage ? <p className="text-muted-foreground mb-4 text-sm">Manage plugins on this computer</p> : null}
    {snapshot.disabled ? <p className="text-muted-foreground mb-4 text-sm">Plugins are off for this page. Remove <code>?plugins=off</code> and reload.</p> : null}
    {snapshot.inventoryError ? <p role="alert" className="text-destructive text-sm">{snapshot.inventoryError}</p> : null}
    {snapshot.loading && !snapshot.entries.length ? <p className="text-muted-foreground text-sm">Loading plugins…</p> : null}
    {!snapshot.loading && !snapshot.entries.length && !snapshot.disabled ? <p className="text-muted-foreground mb-4 text-sm">No plugins installed.</p> : null}
    <div className="grid min-w-0 gap-5 md:grid-cols-[minmax(10rem,15rem)_minmax(0,1fr)] md:gap-6">
      <nav aria-label="Plugin list" className="min-w-0">
        {groups.filter(group => group.entries.length || group.title === "Installed").map(group => <section key={group.title} className="mb-5">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="text-muted-foreground text-xs font-semibold uppercase">{group.title}</h2>
            {group.title === "Installed" && canManage && !snapshot.disabled ? <Button type="button" size="xs" variant="outline" onClick={() => setOperation({ action: "install" })}><Plus className="size-3" /> Add Plugin</Button> : null}
          </div>
          <div className="flex flex-col gap-1">{group.entries.map(entry => {
            const key = entryKey(entry);
            const failure = snapshot.errors[entry.id] ?? (entry.state === "changed-on-disk" ? null : entry.error);
            const status = failure ? "Failed" : entry.state === "changed-on-disk" ? "Changed on Disk" : entry.state === "stopping" ? "Stopping" : entry.enabled ? "On" : "Off";
            return <Button key={key} type="button" variant={selected && entryKey(selected) === key ? "secondary" : "ghost"}
              className="h-auto min-h-12 w-full min-w-0 justify-start gap-2 px-2 py-2 text-left" aria-label={entry.name}
              aria-pressed={Boolean(selected && entryKey(selected) === key)} onClick={() => setSelectedKey(key)}>
              <Box className="size-4 shrink-0" /><span className="flex min-w-0 flex-col items-start"><span className="max-w-full truncate font-medium">{entry.name}</span>
                <span className="text-muted-foreground text-xs">{status}</span></span>
            </Button>;
          })}</div>
        </section>)}
        <p className="text-muted-foreground text-xs">The terminal's /config lists the same plugins and settings.</p>
      </nav>
      {selected ? <PluginDetail key={entryKey(selected)} entry={selected} host={host} api={api} session={session} canManage={canManage} onUpdate={pluginId => setOperation({ action: "update", pluginId })} /> : null}
    </div>
    {operation ? <PluginInstallDialog api={api} action={operation.action} pluginId={operation.pluginId}
      onClose={() => setOperation(null)} onApplied={() => host.refresh()} /> : null}
  </div>;
}
