import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import type { EngineApi, HubCronJob, HubCronJobsView } from "@/api";
import { errorText } from "@/api";
import { Button } from "@/components/ui/button";
import { SchedulesSection } from "./SchedulesPanel";

/** The badge next to an agent name: enough of the job id to tell rows apart. */
export function shortId(id: string) {
  return id.length > 8 ? id.slice(0, 8) : id;
}

/** "in 30m", "in 1m 10s", "in 2h 14m" — the same style /hub cron list uses. */
export function inSeconds(seconds: number) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `in ${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return rem ? `in ${m}m ${rem}s` : `in ${m}m`;
  const h = Math.floor(m / 60);
  const mrem = m % 60;
  if (h < 48) return mrem ? `in ${h}h ${mrem}m` : `in ${h}h`;
  const d = Math.round(h / 24);
  return `in ${d}d`;
}

export function summaryLine(view: HubCronJobsView) {
  const agents = view.agents_responded.length;
  const jobs = `${view.count} ${view.count === 1 ? "job" : "jobs"}`;
  const live = `${agents} live ${agents === 1 ? "agent" : "agents"}`;
  const unavailable = view.agents_unavailable.length
    ? ` · ${view.agents_unavailable.length} unavailable`
    : "";
  return `${jobs} across ${live}${unavailable}`;
}

/** Per-agent cron query failures surface as amber alerts in every state. */
function ErrorAlerts({ errors }: { errors: [string, string][] }) {
  if (!errors.length) return null;
  return (
    <div className="mb-3 space-y-1">
      {errors.map(([agent, msg]) => (
        <p
          key={agent}
          role="alert"
          className="text-amber-700 dark:text-amber-400 text-sm wrap-anywhere"
        >
          <AlertTriangle className="mr-1 inline size-4 align-[-3px]" />
          {agent}: {msg}
        </p>
      ))}
    </div>
  );
}

/**
 * Long free text (target, message, error) shows two lines; a click shows the
 * rest and a second click folds it again. The title gives desktop a hover peek.
 */
function ClampText({ text, className = "" }: { text: string; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <button
      type="button"
      className={`w-full cursor-pointer rounded-sm text-left wrap-anywhere outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 ${open ? "" : "line-clamp-2"} ${className}`}
      title={text}
      aria-expanded={open}
      onClick={() => setOpen((v) => !v)}
    >
      {text}
    </button>
  );
}

/** Read-only schedule table (desktop) and cards (narrow screens). */
export function CronJobsBody({ view }: { view: HubCronJobsView }) {
  const errorAgents = Object.entries(view.agents_errors || {});
  if (!view.jobs.length) {
    const partial = view.agents_unavailable.length > 0 || errorAgents.length > 0;
    return (
      <div className="text-muted-foreground rounded-lg border p-4 text-sm">
        <ErrorAlerts errors={errorAgents} />
        <p className="text-foreground font-medium">
          {partial ? "No jobs reported by responding agents" : "No scheduled jobs"}
        </p>
        <p className="mt-1">
          {partial
            ? "Some agents could not be checked, so their scheduled jobs are unknown."
            : "No live agent on this machine has a hub cron job. Schedule one from an agent's terminal: /hub cron add"}
        </p>
        {view.agents_unavailable.length > 0 ? (
          <p className="mt-2 wrap-anywhere">
            Not answering: {view.agents_unavailable.join(", ")}
          </p>
        ) : null}
      </div>
    );
  }
  return (
    <>
      <ErrorAlerts errors={errorAgents} />
      <div className="hidden overflow-x-auto md:block">
        <table className="text-left text-sm">
          <thead>
            <tr className="text-muted-foreground border-b">
              <th className="py-2 pr-3 font-medium">Agent</th>
              <th className="py-2 pr-3 font-medium">Type</th>
              <th className="py-2 pr-3 font-medium">Schedule</th>
              <th className="py-2 pr-3 font-medium">Target</th>
              <th className="py-2 pr-3 font-medium">Message</th>
              <th className="py-2 pr-3 font-medium">Next Fire</th>
              <th className="py-2 font-medium">Last Error</th>
            </tr>
          </thead>
          <tbody>
            {view.jobs.map((job: HubCronJob) => (
              <tr key={`${job.agent}:${job.id}`} className="border-b last:border-b-0">
                <td className="py-2 pr-3 align-top whitespace-nowrap">
                  {job.agent}{" "}
                  <span className="text-muted-foreground text-xs">
                    {shortId(job.id)}
                  </span>
                </td>
                <td className="py-2 pr-3 align-top">{job.mode}</td>
                <td className="py-2 pr-3 align-top whitespace-nowrap">
                  {job.interval}
                </td>
                <td className="max-w-56 min-w-32 py-2 pr-3 align-top">
                  <ClampText text={job.target} />
                </td>
                <td className="max-w-80 min-w-48 py-2 pr-3 align-top">
                  <ClampText text={job.message} />
                </td>
                <td className="py-2 pr-3 align-top whitespace-nowrap">
                  {inSeconds(job.next_in_seconds)}
                </td>
                <td className="max-w-64 min-w-32 py-2 align-top">
                  {job.last_error ? (
                    <ClampText text={job.last_error} className="text-destructive" />
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="space-y-3 md:hidden">
        {view.jobs.map((job: HubCronJob) => (
          <div key={`${job.agent}:${job.id}`} className="rounded-lg border p-3 text-sm">
            <div className="flex items-baseline justify-between gap-2">
              <span className="min-w-0 font-medium wrap-anywhere">
                {job.agent}{" "}
                <span className="text-muted-foreground text-xs">
                  {shortId(job.id)}
                </span>
              </span>
              <span className="text-muted-foreground text-xs">{job.mode}</span>
            </div>
            <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">Schedule</dt>
              <dd>{job.interval}</dd>
              <dt className="text-muted-foreground">Target</dt>
              <dd className="min-w-0">
                <ClampText text={job.target} />
              </dd>
              <dt className="text-muted-foreground">Message</dt>
              <dd className="min-w-0">
                <ClampText text={job.message} />
              </dd>
              <dt className="text-muted-foreground">Next Fire</dt>
              <dd>{inSeconds(job.next_in_seconds)}</dd>
              {job.last_error ? (
                <>
                  <dt className="text-destructive">Last Error</dt>
                  <dd className="min-w-0">
                    <ClampText text={job.last_error} className="text-destructive" />
                  </dd>
                </>
              ) : null}
            </dl>
          </div>
        ))}
      </div>
    </>
  );
}

/**
 * The Schedules settings tab: editable schedules on top, then every live agent's in-memory hub cron jobs,
 * read-only. Deletion stays in the owning agent's terminal; this view
 * reflects it on refresh.
 */
export function CronJobsPanel({ api }: { api: EngineApi }) {
  const [view, setView] = useState<HubCronJobsView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);

  const load = useCallback(
    async (refresh = false) => {
      const mine = ++epoch.current;
      setBusy(true);
      try {
        const next = await api.listHubCronJobs(refresh);
        if (mine === epoch.current) {
          setView(next);
          setError("");
        }
      } catch (err) {
        if (mine === epoch.current) setError(errorText(err));
      } finally {
        if (mine === epoch.current) setBusy(false);
      }
    },
    [api],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // "Loading" only while a request is in flight; a settled failure with no
  // data says so instead of repeating the loading label.
  const summary = view
    ? summaryLine(view)
    : error && !busy
      ? "Scheduled jobs unavailable"
      : "Loading scheduled jobs…";

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 pt-3 pb-4 sm:px-6">
      <SchedulesSection api={api} />
      <div className="mt-8 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-medium">Agent Cron Jobs</h2>
          <p className="text-muted-foreground text-sm">{summary}</p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void load(true)}
          disabled={busy}
          aria-label="Refresh cron jobs"
        >
          <RefreshCw
            className={busy ? "size-4 animate-spin" : "size-4"}
            aria-hidden="true"
          />
          Refresh
        </Button>
      </div>
      {error ? (
        <p role="alert" className="text-destructive pt-2 text-sm">
          {error}
        </p>
      ) : null}
      <div className="mt-2">
        {view ? <CronJobsBody view={view} /> : null}
        <p className="text-muted-foreground mt-3 text-xs">
          Read-only. To delete a job, run /hub cron delete in the terminal of
          the agent named in its row; the row disappears here on refresh.
        </p>
      </div>
    </div>
  );
}
