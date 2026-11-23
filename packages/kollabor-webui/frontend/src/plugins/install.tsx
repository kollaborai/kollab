import { useEffect, useRef, useState } from "react";
import { TriangleAlert } from "lucide-react";
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

const contributionLabels: Record<string, [string, string]> = {
  views: ["view", "views"],
  pages: ["page", "pages"],
  menu: ["right-menu row", "right-menu rows"],
  modals: ["modal", "modals"],
  slash: ["slash command", "slash commands"],
  renderers: ["chat renderer", "chat renderers"],
};

function contributionSummary(kind: string, count: number): string {
  const labels = contributionLabels[kind];
  return `${count} ${labels ? labels[count === 1 ? 0 : 1] : kind.replaceAll("_", " ")}`;
}

export function describePluginAdds(manifest: NonNullable<Job["manifest"]>): string[] {
  const contributions = manifest.contributes && typeof manifest.contributes === "object" && !Array.isArray(manifest.contributes)
    ? Object.entries(manifest.contributes).flatMap(([kind, entries]) => Array.isArray(entries) && entries.length ? [contributionSummary(kind, entries.length)] : []) : [];
  const services = manifest.services && typeof manifest.services === "object" && !Array.isArray(manifest.services)
    ? Object.keys(manifest.services) : [];
  return [
    ...contributions,
    ...(manifest.python ? ["code that runs in every agent"] : []),
    ...(manifest.engine ? ["engine routes"] : []),
    ...(manifest.web ? ["browser code"] : []),
    ...(manifest.agents ? ["agent definitions"] : []),
    ...(manifest.hooks ? ["hooks"] : []),
    ...(manifest.mcp ? ["MCP servers"] : []),
    ...(services.length ? [`${services.length} background process${services.length === 1 ? "" : "es"}`] : []),
  ];
}

/** Describe declared capabilities without implying that plugin code is sandboxed. Shared with the Settings page. */
export function describeUses(value: unknown): { throughKollab: string[]; onItsOwn: string[]; other: string[] } {
  const uses = Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  const throughKollab = [
    ...(uses.includes("agents") ? ["Can start agent runs through Kollab."] : []),
    ...(uses.includes("agents:unattended") ? ["Can run agent tools without asking; those agents can do anything you can."] : []),
    ...uses.filter(item => item.startsWith("secret:")).map(item => `Can request the secret ${item.slice(7)} through Kollab.`),
  ];
  const onItsOwn = uses.flatMap(item => {
    const [kind, ...rest] = item.split(":");
    const detail = rest.join(":");
    if (!detail) return [];
    if (kind === "run") return [`Runs: ${detail}`];
    if (kind === "net") return [`Talks to: ${detail}.`];
    if (kind === "files") return [`Reads or writes: ${detail}.`];
    return [];
  });
  const other = uses.filter(item => item !== "agents" && item !== "agents:unattended" &&
    !item.startsWith("secret:") && !/^(run|net|files):.+/.test(item));
  return { throughKollab, onItsOwn, other };
}

export const PLUGIN_TRUST_NOTICE = "Kollab refuses requests made through Kollab that the plugin did not declare. Plugin code runs as you, as do agents with a shell; they have your files and keys and can do more. Add only plugins you trust.";

function DetailsList({ label, values }: { label: string; values: unknown }) {
  if (!values || (Array.isArray(values) && !values.length) || (typeof values === "object" && !Object.keys(values).length)) return null;
  const lines = Array.isArray(values) ? values : typeof values === "object" ? Object.entries(values).map(([key, value]) => `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`) : [values];
  return <div className="grid min-w-0 gap-1 sm:grid-cols-[8rem_minmax(0,1fr)] sm:gap-3"><h3 className="text-muted-foreground text-sm">{label}</h3><ul className="min-w-0 space-y-1 text-sm">{lines.map((item, index) => <li className="break-words" key={index}>{typeof item === "string" ? item : JSON.stringify(item)}</li>)}</ul></div>;
}

/** Which source and Review controls the dialog shows. A failed review keeps both open so the URL can be edited and reviewed again. */
export function reviewFormState(action: "install" | "update", job: Pick<Job, "status"> | null): { source: boolean; review: boolean } {
  const open = action === "install" && (!job || job.status === "error");
  return { source: open, review: open };
}

/** Preview stages untrusted source; only the explicit second action confirms its exact bytes. */
export function PluginInstallDialog({ api, action, pluginId, onClose, onApplied }: {
  api: EngineApi; action: "install" | "update"; pluginId?: string; onClose: () => void; onApplied: () => Promise<void>;
}) {
  const [url, setUrl] = useState("");
  const [gitRef, setGitRef] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollVersion, setPollVersion] = useState(0);
  const started = useRef(false);
  const start = async () => {
    setBusy(true); setError(null); setJob(null);
    try {
      if (action === "install" && gitRef.trim() && url.includes("#")) throw new Error("Put the branch or tag in one field, not both.");
      const endpoint = action === "install" ? "/plugins/install" : `/plugins/${encodeURIComponent(pluginId ?? "")}/update`;
      const source = `${url.trim()}${gitRef.trim() ? `#${gitRef.trim()}` : ""}`;
      const result = await api.json<Job>(endpoint, { method: "POST", body: JSON.stringify(action === "install" ? { source } : {}) });
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
  // No server call on close: the engine has no route to discard a staged review, so it expires on the server.
  const close = () => {
    if (busy || job?.status === "installing") return;
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
  const uses = Array.isArray(job?.manifest?.uses) ? job.manifest.uses : [];
  const { throughKollab: declaredThroughKollab, onItsOwn, other } = describeUses(uses);
  const services = job?.manifest?.services && typeof job.manifest.services === "object" && !Array.isArray(job.manifest.services)
    ? Object.entries(job.manifest.services) : [];
  const throughKollab = [
    ...declaredThroughKollab,
    ...services.flatMap(([name, value]) => {
      if (!value || typeof value !== "object" || !("secrets" in value) || !Array.isArray(value.secrets)) return [];
      const secrets = value.secrets.filter((item: unknown): item is string => typeof item === "string");
      return secrets.length ? [`Background process ${name} receives: ${secrets.join(", ")}.`] : [];
    }),
  ];
  const form = reviewFormState(action, job);
  return <Dialog open onOpenChange={open => { if (!open) close(); }}>
    <DialogContent className="flex max-h-[100dvh] min-h-0 flex-col overflow-hidden sm:max-w-[640px] max-sm:h-[100dvh] max-sm:max-w-none max-sm:rounded-none">
      <DialogHeader><DialogTitle>{action === "install" ? "Add Plugin" : "Update Plugin"}</DialogTitle>
        <DialogDescription>Review the exact source and permissions before anything is installed or run.</DialogDescription></DialogHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pr-1">
        {form.source ? <div className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
          <label className="text-muted-foreground flex min-w-0 flex-col gap-2 text-sm">Git URL<Input value={url} onChange={event => setUrl(event.target.value)} placeholder="https://github.com/owner/plugin.git" autoComplete="url" /></label>
          <label className="text-muted-foreground flex min-w-0 flex-col gap-2 text-sm">Branch or Tag<Input value={gitRef} onChange={event => setGitRef(event.target.value)} placeholder="Default branch" /></label>
        </div> : null}
        {job?.status === "staging" ? <p role="status" className="text-muted-foreground text-sm">Fetching and checking the plugin…</p> : null}
        {job?.status === "error" ? <p role="alert" className="text-destructive text-sm">{job.error || "Plugin review failed."}</p> : null}
        {ready && job ? <>
          <p className="text-sm font-semibold">{job.manifest?.name ?? job.plugin_id ?? "Plugin"}</p>
          {job.manifest?.description ? <p className="text-muted-foreground text-sm">{job.manifest.description}</p> : null}
          {job.source ? <p className="break-all text-sm">From: {job.source}{job.ref ? ` (${job.ref})` : ""}</p> : null}
          {job.previous_commit ? <p className="break-all text-xs">Previous Commit: {job.previous_commit}</p> : null}
          <p className="break-all text-xs">Commit: {job.commit}</p><p className="break-all text-xs">Files SHA-256: {job.files_sha256}</p>
          <DetailsList label="Changed Folders" values={job.changed_folders} />
          <DetailsList label="Adds" values={job.manifest ? describePluginAdds(job.manifest) : []} />
          <DetailsList label="Through Kollab" values={throughKollab} />
          <DetailsList label="On Its Own" values={onItsOwn} />
          <DetailsList label="Other Declarations" values={other} />
          {uses.includes("agents") ? <DetailsList label="Limits" values={["When available, agent limits cover Kollab runs and usage the plugin reports; unreported spend is not capped."]} /> : null}
          {job.warning ? <p className="text-sm">{job.warning}</p> : null}
          <div className="bg-muted flex gap-3 rounded-lg p-3 text-sm"><TriangleAlert className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" /><p>{PLUGIN_TRUST_NOTICE}</p></div>
          <DetailsList label="New Grants" values={job.changes?.new_grants} /><DetailsList label="New Dependencies" values={job.changes?.new_dependencies} />
          <DetailsList label="Added Files" values={job.changes?.added} /><DetailsList label="Changed Files" values={job.changes?.modified} /><DetailsList label="Removed Files" values={job.changes?.removed} />
        </> : null}
        {error ? <p role="alert" className="text-destructive break-words text-sm">{error}</p> : null}
      </div>
      <DialogFooter><Button type="button" variant="outline" disabled={busy} onClick={close}>Cancel</Button>
        {job?.status === "staging" && error ? <Button type="button" disabled={busy} onClick={() => { setError(null); setPollVersion(value => value + 1); }}>Retry</Button> : null}
        {form.review ? <Button type="button" disabled={busy || !url.trim()} onClick={() => { void start(); }}>Review</Button> : null}
        {ready ? <Button type="button" disabled={busy} onClick={() => { void confirm(); }}>{action === "install" ? "Install" : "Update"}</Button> : null}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
