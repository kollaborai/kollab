import { useEffect, useRef, useState } from "react";
import { ApiError, type EngineApi } from "@/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  normalizeBoundedUnique,
  normalizePluginContributes,
  normalizePluginUses,
  normalizePluginWebPath,
  PLUGIN_INVENTORY_LIMITS,
  safePluginText,
} from "./loader";

type Job = {
  job_id: string; status: "staging" | "ready" | "cancelling" | "installing" | "complete" | "error" | "cancelled";
  action?: "install" | "update"; plugin_id?: string; commit?: string; files_sha256?: string;
  source?: string; ref?: string; previous_commit?: string; changed_folders?: string[];
  error?: string | null; warning?: string | null;
  manifest?: { name?: string; version?: string; description?: string; uses?: unknown; services?: unknown;
    omitted?: { services?: number; uses?: number; service_secrets?: number };
    python?: unknown; engine?: unknown; web?: unknown; agents?: unknown; contributes?: unknown; hooks?: unknown; mcp?: unknown };
};
const errorText = (error: unknown) => error instanceof ApiError
  ? error.status === 404 ? "Plugin review no longer exists."
    : error.status === 409 ? "Plugin changed during review. Stage it again."
    : error.status === 503 ? "Plugin manager could not complete the request."
    : `Plugin request failed (HTTP ${error.status}).`
  : error instanceof Error && error.message === "Put the branch or tag in one field, not both."
    ? error.message
    : "Plugin request failed. Try again.";

const JOB_STATUSES = new Set<Job["status"]>(["staging", "ready", "cancelling", "installing", "complete", "error", "cancelled"]);
const JOB_ERRORS = new Set([
  "Cannot stage plugin source; check the URL, ref, and plugin manifest",
  "Plugin approval, files, or active runs changed; check plugin status and stage again",
  "Cannot install plugin; check the plugin directory and try again",
  "Cannot install plugin; stage it again",
  "Plugin manifest exceeds the review limits (128 services, 64 secrets per service, 256 uses)",
]);
const JOB_WARNINGS = new Set([
  "Plugin staging files could not be removed",
  "A previously running service could not be restarted; check its status",
  "Plugin installed, but its services could not be started; check their status",
]);
const PLUGIN_ID = /^[a-z0-9][a-z0-9-]{1,39}(?:@dev)?$/;
const SERVICE_ID = /^[a-z0-9][a-z0-9-]{0,39}$/;
const SECRET_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;

function objectRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function boundedObjectEntries(value: Record<string, unknown>, limit: number): Array<[string, unknown]> {
  const entries: Array<[string, unknown]> = [];
  for (const key in value) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
    if (entries.length >= limit) break;
    entries.push([key, value[key]]);
  }
  return entries;
}

function safeCommit(value: unknown, size: 40 | 64): string | undefined {
  return typeof value === "string" && new RegExp(`^[0-9a-f]{${size}}$`, "i").test(value) ? value : undefined;
}

function normalizeReviewManifest(value: unknown, pluginId: string | undefined): Job["manifest"] | undefined {
  const raw = objectRecord(value);
  if (!raw) return undefined;
  const services = objectRecord(raw.services);
  const safeServices: Record<string, { secrets: string[]; secrets_omitted: number }> = {};
  let omittedServiceSecrets = 0;
  if (services) {
    for (const [name, definition] of boundedObjectEntries(services, PLUGIN_INVENTORY_LIMITS.services)) {
      const safeName = safePluginText(name, 40);
      if (!safeName || !SERVICE_ID.test(safeName)) continue;
      const service = objectRecord(definition);
      const secretValues = Array.isArray(service?.secrets) ? service.secrets : [];
      const secretsOmitted = Math.max(0, secretValues.length - PLUGIN_INVENTORY_LIMITS.serviceSecrets);
      omittedServiceSecrets += secretsOmitted;
      const secrets = normalizeBoundedUnique(service?.secrets, PLUGIN_INVENTORY_LIMITS.serviceSecrets, (item) =>
        typeof item === "string" && SECRET_NAME.test(item) ? item : null,
      );
      safeServices[safeName] = { secrets, secrets_omitted: secretsOmitted };
    }
  }
  const rawUses = Array.isArray(raw.uses) ? raw.uses : [];
  const omittedServices = services
    ? Math.max(0, Object.keys(services).length - PLUGIN_INVENTORY_LIMITS.services)
    : 0;
  const safeContributions = normalizePluginContributes(raw.contributes);
  const name = safePluginText(raw.name, 256) ?? pluginId ?? "Plugin";
  const version = safePluginText(raw.version, 256) ?? undefined;
  const description = safePluginText(raw.description) ?? undefined;
  return {
    name,
    version,
    description,
    uses: normalizePluginUses(raw.uses),
    services: safeServices,
    omitted: {
      services: omittedServices,
      uses: Math.max(0, rawUses.length - PLUGIN_INVENTORY_LIMITS.uses),
      service_secrets: omittedServiceSecrets,
    },
    contributes: safeContributions,
    python: Boolean(raw.python),
    engine: Boolean(raw.engine),
    web: normalizePluginWebPath(objectRecord(raw.web)?.entry) !== null,
    agents: Boolean(raw.agents),
    hooks: Boolean(raw.hooks),
    mcp: Boolean(raw.mcp),
  };
}

/** One safe shape for job responses before review text reaches the dialog. */
export function normalizeReviewJob(value: unknown): Job | null {
  const raw = objectRecord(value);
  const jobId = safePluginText(raw?.job_id, 128);
  if (!raw || !jobId) return null;
  const status = JOB_STATUSES.has(raw.status as Job["status"]) ? raw.status as Job["status"] : "error";
  const pluginId = typeof raw.plugin_id === "string" && PLUGIN_ID.test(raw.plugin_id) ? raw.plugin_id : undefined;
  const errorText = typeof raw.error === "string" && JOB_ERRORS.has(raw.error)
    ? raw.error
    : status === "error" ? "Plugin review failed. Check the source and manifest, then try again." : null;
  const warningText = typeof raw.warning === "string" && JOB_WARNINGS.has(raw.warning)
    ? raw.warning
    : raw.warning ? "Plugin operation completed with a warning." : null;
  const changedFolders = Array.isArray(raw.changed_folders)
    ? raw.changed_folders.slice(0, 256).map((folder) => safePluginText(folder, 512)).filter((folder): folder is string => folder !== null)
    : undefined;
  return {
    job_id: jobId,
    status,
    action: raw.action === "install" || raw.action === "update" ? raw.action : undefined,
    plugin_id: pluginId,
    commit: safeCommit(raw.commit, 40) ?? safeCommit(raw.commit, 64),
    files_sha256: safeCommit(raw.files_sha256, 64),
    source: safePluginText(raw.source) ?? undefined,
    ref: safePluginText(raw.ref, 256) ?? undefined,
    previous_commit: safeCommit(raw.previous_commit, 40) ?? safeCommit(raw.previous_commit, 64),
    changed_folders: changedFolders,
    error: errorText,
    warning: warningText,
    manifest: normalizeReviewManifest(raw.manifest, pluginId),
  };
}

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
  const safeContributions = normalizePluginContributes(manifest.contributes);
  const contributions = Object.entries(safeContributions)
    .flatMap(([kind, entries]) => entries.length ? [contributionSummary(kind, entries.length)] : []);
  const services = boundedObjectEntries(
    objectRecord(manifest.services) ?? {},
    PLUGIN_INVENTORY_LIMITS.services,
  );
  const omitted = manifest.omitted ?? {};
  const omissions = [
    omitted.services ? `${omitted.services} additional service${omitted.services === 1 ? "" : "s"} omitted from this review` : null,
    omitted.uses ? `${omitted.uses} additional use declaration${omitted.uses === 1 ? "" : "s"} omitted from this review` : null,
    omitted.service_secrets ? `${omitted.service_secrets} additional service secret${omitted.service_secrets === 1 ? "" : "s"} omitted from this review` : null,
  ].filter((line): line is string => line !== null);
  return [
    ...contributions,
    ...(manifest.python ? ["code that runs in every agent"] : []),
    ...(manifest.engine ? ["engine routes"] : []),
    ...(manifest.web ? ["browser code"] : []),
    ...(manifest.agents ? ["agent definitions"] : []),
    ...(manifest.hooks ? ["hooks"] : ["no hooks"]),
    ...(manifest.mcp ? ["MCP servers"] : ["no MCP servers"]),
    ...(services.length ? [`${services.length} background process${services.length === 1 ? "" : "es"}`] : []),
    ...omissions,
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
    if (kind === "run") {
      const programs = detail.split(",").map((program) => program.trim().toLowerCase());
      return [
        `Runs: ${detail}`,
        ...(programs.some((program) => program === "codex" || program === "claude")
          ? ["Its coding agents use a shell without asking."]
          : []),
      ];
    }
    if (kind === "net") return [`Talks to: ${detail}.`];
    if (kind === "files") return [`Reads or writes: ${detail}.`];
    return [];
  });
  const other = uses.filter(item => item !== "agents" && item !== "agents:unattended" &&
    !item.startsWith("secret:") && !/^(run|net|files):.+/.test(item));
  return { throughKollab, onItsOwn, other };
}

export const PLUGIN_TRUST_NOTICE = "Kollab refuses requests made through Kollab that the plugin did not declare. Plugin code runs as you, as do agents with a shell; they have your files and keys and can do more. Add only plugins you trust.";

export function installDismissalMessage(status: Job["status"], error: string | null): string | null {
  return status === "installing" && error !== "Plugin review no longer exists."
    ? "Plugin installation is in progress. Wait for it to finish."
    : null;
}

function DetailsList({ label, values }: { label: string; values: unknown }) {
  if (!values || (Array.isArray(values) && !values.length) || (typeof values === "object" && !Object.keys(values).length)) return null;
  const lines = Array.isArray(values) ? values : typeof values === "object" ? Object.entries(values).map(([key, value]) => `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`) : [values];
  return <div className="grid min-w-0 gap-1 sm:grid-cols-[8rem_minmax(0,1fr)] sm:gap-3"><h3 className="text-muted-foreground text-sm">{label}</h3><ul className="min-w-0 space-y-1 text-sm">{lines.map((item, index) => <li className="break-words" key={index}>{typeof item === "string" ? item : JSON.stringify(item)}</li>)}</ul></div>;
}

/** Which source and Review controls the dialog shows. A failed review keeps both open so the URL can be edited and reviewed again. */
export function reviewFormState(action: "install" | "update", job: Pick<Job, "status"> | null, failed = false): { source: boolean; review: boolean } {
  const retryable = job?.status === "error" || job?.status === "cancelled" || (action === "update" && job === null && failed);
  const open = action === "install" && (!job || retryable);
  return { source: open, review: action === "install" ? open : retryable };
}

/** The progress line is for a review that is really still working, never one that already failed. */
export function showFetching(job: Pick<Job, "status"> | null, failed: boolean): boolean {
  return job?.status === "staging" && !failed;
}

export function shouldPollReview(job: Pick<Job, "status"> | null): boolean {
  return job?.status === "staging" || job?.status === "installing" || job?.status === "cancelling";
}

export function shouldRetryPolling(job: Pick<Job, "status"> | null, failed: boolean): boolean {
  return shouldPollReview(job) && failed;
}

/** Ask the engine to stage a review of the source as it is now typed. */
export async function startReview(api: EngineApi, { action, pluginId, url, gitRef }: {
  action: "install" | "update"; pluginId?: string; url: string; gitRef: string;
}): Promise<Job> {
  if (action === "install" && gitRef.trim() && url.includes("#")) throw new Error("Put the branch or tag in one field, not both.");
  const endpoint = action === "install" ? "/plugins/install" : `/plugins/${encodeURIComponent(pluginId ?? "")}/update`;
  const source = `${url.trim()}${gitRef.trim() ? `#${gitRef.trim()}` : ""}`;
  const raw = await api.json<unknown>(endpoint, { method: "POST", body: JSON.stringify(action === "install" ? { source } : {}) });
  const result = normalizeReviewJob(raw);
  if (!result) throw new Error("The plugin manager did not return a review job.");
  return result;
}

export async function fetchReview(api: EngineApi, jobId: string, signal?: AbortSignal): Promise<Job> {
  const raw = await api.json<unknown>(`/plugins/jobs/${encodeURIComponent(jobId)}`, { signal });
  const result = normalizeReviewJob(raw);
  if (!result) throw new Error("The plugin manager returned invalid review data.");
  return result;
}

export async function discardReview(api: EngineApi, jobId: string): Promise<void> {
  try {
    await api.json(`/plugins/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
  } catch (caught) {
    if (!(caught instanceof ApiError) || (caught.status !== 404 && caught.status !== 409)) throw caught;
  }
}

async function discardReviewForDismissal(api: EngineApi, jobId: string): Promise<void> {
  try {
    await api.json(`/plugins/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
  } catch (caught) {
    if (!(caught instanceof ApiError) || caught.status !== 404) throw caught;
  }
}

/** Preview stages untrusted source; only the explicit second action confirms its exact bytes. */
export function PluginInstallDialog({ api, action, pluginId, onClose, onApplied, onRestoreFocus }: {
  api: EngineApi; action: "install" | "update"; pluginId?: string; onClose: () => void; onApplied: () => Promise<void>;
  onRestoreFocus: (event: Event) => void;
}) {
  const [url, setUrl] = useState("");
  const [gitRef, setGitRef] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollVersion, setPollVersion] = useState(0);
  const progressRef = useRef<HTMLParagraphElement>(null);
  const previousStatus = useRef<Job["status"] | null>(null);
  const started = useRef(false);
  const start = async () => {
    setBusy(true); setError(null);
    try {
      if (job && (job.status === "staging" || job.status === "ready"))
        await discardReview(api, job.job_id);
      setJob(null);
      const result = await startReview(api, { action, pluginId, url, gitRef });
      setJob(result);
    } catch (caught) { setError(errorText(caught)); } finally { setBusy(false); }
  };
  useEffect(() => {
    if (action === "update" && !started.current) { started.current = true; void start(); }
  }, [action]);
  useEffect(() => {
    const status = job?.status ?? null;
    if ((status === "staging" || status === "cancelling" || status === "installing") && status !== previousStatus.current)
      requestAnimationFrame(() => progressRef.current?.focus());
    previousStatus.current = status;
  }, [job?.status]);
  useEffect(() => {
    if (!job || (job.status !== "staging" && job.status !== "installing" && job.status !== "cancelling")) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const next = await fetchReview(api, job.job_id, controller.signal);
        if (cancelled) return;
        setJob(next);
        if (next.status === "complete") {
          setBusy(true);
          try {
            await onApplied();
            if (!next.warning) onClose();
          } catch {
            setError("Plugin installed, but the plugin list could not be refreshed.");
          } finally {
            setBusy(false);
          }
          return;
        }
        if (shouldPollReview(next)) timer = setTimeout(() => { void poll(); }, 750);
      } catch (caught) { if (!cancelled) setError(errorText(caught)); }
    };
    void poll();
    return () => { cancelled = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [api, job?.job_id, job?.status, pollVersion]);
  const close = () => {
    if (busy) return;
    if (job?.status === "installing") {
      const message = installDismissalMessage(job.status, error);
      if (message) { setError(message); return; }
      onClose();
      return;
    }
    if (job && (job.status === "staging" || job.status === "ready" || job.status === "cancelling")) {
      setBusy(true); setError(null);
      const cleanup = job.status === "ready"
        ? discardReview(api, job.job_id)
        : discardReviewForDismissal(api, job.job_id);
      void cleanup.then(onClose).catch((caught) => {
        if (caught instanceof ApiError && caught.status === 409) { onClose(); return; }
        setError(errorText(caught));
      }).finally(() => setBusy(false));
      return;
    }
    onClose();
  };
  const confirm = async () => {
    if (!job || job.status !== "ready" || !job.commit || !job.files_sha256) return;
    setBusy(true); setError(null);
    let installed = false;
    try {
      const raw = await api.json<unknown>(`/plugins/jobs/${encodeURIComponent(job.job_id)}/confirm`, {
        method: "POST", body: JSON.stringify({ commit: job.commit, files_sha256: job.files_sha256 }),
      });
      const result = normalizeReviewJob(raw);
      if (!result) throw new Error("The plugin manager returned invalid review data.");
      setJob(result);
      if (result.status !== "complete") throw new Error(result.error || "The plugin manager did not complete the operation.");
      installed = true;
      await onApplied();
      if (!result.warning) onClose();
    } catch (caught) {
      if (installed) {
        setError("Plugin installed, but the plugin list could not be refreshed.");
        return;
      }
      const fallback = errorText(caught);
      try {
        const current = await fetchReview(api, job.job_id);
        setJob(current);
        if (current.status === "complete") {
          setError(null);
          await onApplied();
          if (!current.warning) onClose();
        } else {
          setError(current.status === "installing" ? null : current.error || fallback);
        }
      } catch {
        setError(fallback);
      }
    } finally { setBusy(false); }
  };
  const ready = job?.status === "ready" && Boolean(job.commit && job.files_sha256);
  const uses = Array.isArray(job?.manifest?.uses) ? job.manifest.uses : [];
  const { throughKollab: declaredThroughKollab, onItsOwn, other } = describeUses(uses);
  const services = job?.manifest?.services && typeof job.manifest.services === "object" && !Array.isArray(job.manifest.services)
    ? boundedObjectEntries(objectRecord(job.manifest.services) ?? {}, PLUGIN_INVENTORY_LIMITS.services) : [];
  const throughKollab = [
    ...declaredThroughKollab,
    ...services.flatMap(([name, value]) => {
      const service = objectRecord(value);
      const secrets = normalizeBoundedUnique(service?.secrets, PLUGIN_INVENTORY_LIMITS.serviceSecrets, (item) =>
        typeof item === "string" && SECRET_NAME.test(item) ? item : null,
      );
      return secrets.length ? [`Background process ${name} receives: ${secrets.join(", ")}.`] : [];
    }),
  ];
  const form = reviewFormState(action, job, error !== null);
  const missingInstallingJob = job?.status === "installing" && error === "Plugin review no longer exists.";
  return <Dialog open onOpenChange={open => { if (!open) close(); }}>
    <DialogContent onCloseAutoFocus={onRestoreFocus} className="flex max-h-[100dvh] min-h-0 flex-col overflow-hidden sm:max-w-[640px] max-sm:h-[100dvh] max-sm:max-w-none max-sm:rounded-none">
      <DialogHeader><DialogTitle>{action === "install" ? "Add Plugin" : "Update Plugin"}</DialogTitle>
        <DialogDescription>Review the exact source and permissions before anything is installed or run.</DialogDescription></DialogHeader>
      <div role="region" tabIndex={0} aria-label="Plugin review details" className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pr-1">
        {form.source ? <div className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
          <label className="text-muted-foreground flex min-w-0 flex-col gap-2 text-sm">Git URL<Input className="pointer-coarse:min-h-11" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://github.com/owner/plugin.git" autoComplete="url" /></label>
          <label className="text-muted-foreground flex min-w-0 flex-col gap-2 text-sm">Branch or Tag<Input className="pointer-coarse:min-h-11" value={gitRef} onChange={event => setGitRef(event.target.value)} placeholder="Default branch" /></label>
        </div> : null}
        {showFetching(job, error !== null) ? <p ref={progressRef} tabIndex={-1} role="status" className="text-muted-foreground text-sm">Fetching and checking the plugin…</p> : null}
        {job?.status === "installing" && !error ? <p ref={progressRef} tabIndex={-1} role="status" className="text-muted-foreground text-sm">Installing plugin…</p> : null}
        {job?.status === "cancelling" && !error ? <p ref={progressRef} tabIndex={-1} role="status" className="text-muted-foreground text-sm">Cancelling plugin review…</p> : null}
        {job?.status === "complete" ? <p role="status" className="text-muted-foreground text-sm">Plugin {action === "install" ? "added" : "updated"}.</p> : null}
        {job?.warning ? <p role="alert" className="break-words text-sm">{job.warning}</p> : null}
        {job?.status === "error" ? <p role="alert" className="text-destructive break-words text-sm">{job.error || "Plugin review failed."}</p> : null}
        {job?.status === "cancelled" ? <p role="status" className="text-muted-foreground text-sm">Plugin review was cancelled. You can start another review.</p> : null}
        {ready && job ? <>
          <p className="break-words text-sm font-semibold">{job.manifest?.name ?? job.plugin_id ?? "Plugin"}</p>
          {job.manifest?.description ? <p className="text-muted-foreground break-words text-sm">{job.manifest.description}</p> : null}
          {job.source ? <p className="break-all text-sm">From: {job.source}{job.ref ? ` (${job.ref})` : " (default branch)"}</p> : null}
          {job.previous_commit ? <p className="break-all text-xs">Previous Commit: {job.previous_commit}</p> : null}
          <p className="break-all text-xs">Commit: {job.commit}</p><p className="break-all text-xs">Files SHA-256: {job.files_sha256}</p>
          <DetailsList label="Changed Folders" values={job.changed_folders} />
          <DetailsList label="Adds" values={job.manifest ? describePluginAdds(job.manifest) : []} />
          <DetailsList label="Through Kollab" values={throughKollab} />
          <DetailsList label="On Its Own" values={onItsOwn} />
          <DetailsList label="Other Declarations" values={other} />
          <p className="bg-muted rounded-lg p-3 text-sm">{PLUGIN_TRUST_NOTICE}</p>
        </> : null}
        {error ? <p role="alert" className="text-destructive break-words text-sm">{error}</p> : null}
        {shouldRetryPolling(job, error !== null) ? <Button type="button" variant="outline" className="pointer-coarse:min-h-11 self-start" disabled={busy} onClick={() => { setError(null); setPollVersion((version) => version + 1); requestAnimationFrame(() => progressRef.current?.focus()); }}>Check Again</Button> : null}
      </div>
      <DialogFooter><Button type="button" variant="outline" className="pointer-coarse:min-h-11" disabled={busy || (job?.status === "installing" && !missingInstallingJob)} onClick={close}>{job?.status === "installing" && !missingInstallingJob ? "Installing…" : job?.status === "complete" || missingInstallingJob ? "Close" : "Cancel"}</Button>
                {form.review ? <Button type="button" className="pointer-coarse:min-h-11" disabled={busy || (action === "install" && !url.trim())} onClick={() => { void start(); }}>{action === "update" ? "Review Again" : "Review"}</Button> : null}
         {ready ? <Button type="button" className="pointer-coarse:min-h-11" disabled={busy} onClick={() => { void confirm(); }}>{action === "install" ? "Add Plugin" : "Update"}</Button> : null}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
