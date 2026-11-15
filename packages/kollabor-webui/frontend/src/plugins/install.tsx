import { useEffect, useRef, useState } from "react";
import type { EngineApi } from "@/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

type Job = {
  job_id: string; status: "staging" | "ready" | "installing" | "complete" | "error" | "cancelled";
  action?: "install" | "update"; plugin_id?: string; commit?: string; files_sha256?: string;
  source?: string; ref?: string; previous_commit?: string; changed_folders?: string[];
  error?: string | null; warning?: string | null;
  manifest?: { name?: string; version?: string; description?: string; uses?: unknown; services?: unknown; python?: unknown; engine?: unknown; web?: unknown; agents?: unknown; contributes?: unknown; hooks?: unknown; mcp?: unknown };
  changes?: { added?: string[]; modified?: string[]; removed?: string[]; new_grants?: string[]; new_dependencies?: string[] };
};
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

function DetailsList({ label, values }: { label: string; values: unknown }) {
  if (!values || (Array.isArray(values) && !values.length) || (typeof values === "object" && !Object.keys(values).length)) return null;
  const lines = Array.isArray(values) ? values : typeof values === "object" ? Object.entries(values).map(([key, value]) => `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`) : [values];
  return <div><h3 className="text-sm font-semibold">{label}</h3><ul className="text-muted-foreground list-disc break-all pl-5 text-sm">{lines.map((item, index) => <li key={index}>{typeof item === "string" ? item : JSON.stringify(item)}</li>)}</ul></div>;
}

/** Preview stages untrusted source; only the explicit second action confirms its exact bytes. */
export function PluginInstallDialog({ api, action, pluginId, onClose, onApplied }: {
  api: EngineApi; action: "install" | "update"; pluginId?: string; onClose: () => void; onApplied: () => Promise<void>;
}) {
  const [url, setUrl] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollVersion, setPollVersion] = useState(0);
  const started = useRef(false);
  const start = async () => {
    setBusy(true); setError(null);
    try {
      const endpoint = action === "install" ? "/plugins/install/preview" : `/plugins/${encodeURIComponent(pluginId ?? "")}/update/preview`;
      const result = await api.json<Job>(endpoint, { method: "POST", body: JSON.stringify(action === "install" ? { url: url.trim() } : {}) });
      if (!result.job_id) throw new Error("The plugin manager did not return a review job.");
      setJob(result);
    } catch (caught) { setError(errorText(caught)); } finally { setBusy(false); }
  };
  useEffect(() => {
    if (action === "update" && !started.current) { started.current = true; void start(); }
  }, [action]);
  useEffect(() => {
    if (!job || job.status !== "staging") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const next = await api.json<Job>(`/plugins/jobs/${encodeURIComponent(job.job_id)}`, { signal: controller.signal });
        if (cancelled) return;
        setJob(next);
        if (next.status === "staging") timer = setTimeout(() => { void poll(); }, 750);
      } catch (caught) { if (!cancelled) setError(errorText(caught)); }
    };
    void poll();
    return () => { cancelled = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [api, job?.job_id, job?.status, pollVersion]);
  const close = async () => {
    if (busy || job?.status === "installing") return;
    if (job && job.status !== "complete" && job.status !== "cancelled" && job.status !== "error") {
      setBusy(true); setError(null);
      try { await api.json(`/plugins/jobs/${encodeURIComponent(job.job_id)}`, { method: "DELETE" }); }
      catch (caught) { setError(errorText(caught)); setBusy(false); return; }
      setBusy(false);
    }
    onClose();
  };
  const confirm = async () => {
    if (!job || job.status !== "ready" || !job.commit || !job.files_sha256) return;
    setBusy(true); setError(null);
    try {
      const result = await api.json<Job>(`/plugins/jobs/${encodeURIComponent(job.job_id)}/confirm`, {
        method: "POST", body: JSON.stringify({ commit: job.commit, files_sha256: job.files_sha256 }),
      });
      setJob(result);
      if (result.status !== "complete") throw new Error(result.error || "The plugin manager did not complete the operation.");
      await onApplied();
      onClose();
    } catch (caught) { setError(errorText(caught)); } finally { setBusy(false); }
  };
  const ready = job?.status === "ready" && Boolean(job.commit && job.files_sha256);
  const uses = Array.isArray(job?.manifest?.uses) ? job.manifest.uses.filter((item): item is string => typeof item === "string") : [];
  const services = job?.manifest?.services && typeof job.manifest.services === "object" && !Array.isArray(job.manifest.services)
    ? Object.entries(job.manifest.services) : [];
  const contributions = job?.manifest?.contributes && typeof job.manifest.contributes === "object" && !Array.isArray(job.manifest.contributes)
    ? Object.entries(job.manifest.contributes).flatMap(([kind, entries]) => Array.isArray(entries) && entries.length ? [`${entries.length} ${kind}`] : []) : [];
  const throughKollab = [
    ...(uses.includes("agents") ? ["Can start agent runs through Kollab."] : []),
    ...(uses.includes("agents:unattended") ? ["Can run agent tools without asking; those agents can do anything you can."] : []),
    ...uses.filter(item => item.startsWith("secret:")).map(item => `Can request the secret ${item.slice(7)} through Kollab.`),
    ...services.flatMap(([name, value]) => {
      if (!value || typeof value !== "object" || !("secrets" in value) || !Array.isArray(value.secrets)) return [];
      const secrets = value.secrets.filter((item: unknown): item is string => typeof item === "string");
      return secrets.length ? [`Background process ${name} receives: ${secrets.join(", ")}.`] : [];
    }),
  ];
  const onItsOwn = uses.flatMap(item => {
    const [kind, ...rest] = item.split(":");
    const value = rest.join(":");
    if (!value) return [];
    if (kind === "run") return [`Runs: ${value}.`];
    if (kind === "net") return [`Talks to: ${value}.`];
    if (kind === "files") return [`Reads or writes: ${value}.`];
    return [];
  });
  return <Dialog open onOpenChange={open => { if (!open) void close(); }}>
    <DialogContent data-plugin className="flex max-h-[100dvh] min-h-0 flex-col overflow-hidden max-sm:h-[100dvh] max-sm:max-w-none max-sm:rounded-none">
      <DialogHeader><DialogTitle>{action === "install" ? "Add Plugin" : "Update Plugin"}</DialogTitle>
        <DialogDescription>Review the exact source and permissions before anything is installed or run.</DialogDescription></DialogHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pr-1">
        {action === "install" && !job ? <label className="flex flex-col gap-2 text-sm">Git URL<Input value={url} onChange={event => setUrl(event.target.value)} placeholder="https://github.com/owner/plugin.git" autoComplete="url" /></label> : null}
        {job?.status === "staging" ? <p role="status" className="text-muted-foreground text-sm">Fetching and checking the plugin…</p> : null}
        {job?.status === "error" ? <p role="alert" className="text-destructive text-sm">{job.error || "Plugin review failed."}</p> : null}
        {ready && job ? <>
          <p className="text-sm font-semibold">{job.manifest?.name ?? job.plugin_id ?? "Plugin"}</p>
          {job.manifest?.description ? <p className="text-muted-foreground text-sm">{job.manifest.description}</p> : null}
          {job.source ? <p className="break-all text-sm">From: {job.source}{job.ref ? ` (${job.ref})` : ""}</p> : null}
          {job.previous_commit ? <p className="break-all text-xs">Previous commit: {job.previous_commit}</p> : null}
          <p className="break-all text-xs">Commit: {job.commit}</p><p className="break-all text-xs">Files SHA-256: {job.files_sha256}</p>
          <DetailsList label="Changed folders" values={job.changed_folders} />
          <DetailsList label="Adds" values={[
            ...contributions,
            ...(job.manifest?.python ? ["Code that runs in every agent"] : []),
            ...(job.manifest?.engine ? ["Engine routes"] : []),
            ...(job.manifest?.web ? ["Browser code"] : []),
            ...(job.manifest?.agents ? ["Agent definitions"] : []),
            ...(job.manifest?.hooks ? ["Hooks"] : []),
            ...(job.manifest?.mcp ? ["MCP servers"] : []),
            ...(services.length ? [`${services.length} background process${services.length === 1 ? "" : "es"}`] : []),
          ]} />
          <DetailsList label="Through Kollab" values={throughKollab} />
          <DetailsList label="On Its Own" values={onItsOwn} />
          {uses.includes("agents") ? <p className="text-sm">Agent limits cover Kollab runs and usage the plugin reports; unreported spend is not capped.</p> : null}
          {job.warning ? <p className="text-sm">{job.warning}</p> : null}
          <p className="text-sm">Kollab refuses requests made through Kollab that the plugin did not declare. Plugin code runs as you, as do agents with a shell; they have your files and keys and can do more. Add only plugins you trust.</p>
          <DetailsList label="New grants" values={job.changes?.new_grants} /><DetailsList label="New dependencies" values={job.changes?.new_dependencies} />
          <DetailsList label="Added files" values={job.changes?.added} /><DetailsList label="Changed files" values={job.changes?.modified} /><DetailsList label="Removed files" values={job.changes?.removed} />
        </> : null}
        {error ? <p role="alert" className="text-destructive break-words text-sm">{error}</p> : null}
      </div>
      <DialogFooter><Button type="button" variant="outline" disabled={busy} onClick={() => { void close(); }}>Cancel</Button>
        {job?.status === "staging" && error ? <Button type="button" disabled={busy} onClick={() => { setError(null); setPollVersion(value => value + 1); }}>Retry</Button> : null}
        {!job && action === "install" ? <Button type="button" disabled={busy || !url.trim()} onClick={() => { void start(); }}>Review</Button> : null}
        {ready ? <Button type="button" disabled={busy} onClick={() => { void confirm(); }}>{action === "install" ? "Install" : "Update"}</Button> : null}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
