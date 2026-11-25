import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import type { EngineApi, HubSchedule, HubScheduleFields } from "@/api";
import { errorText } from "@/api";
import { Button } from "@/components/ui/button";

const UNITS: [string, number][] = [
  ["d", 86400],
  ["h", 3600],
  ["m", 60],
  ["s", 1],
];

/** "30s", "5m", "1h", "2h30m", "1d" -> seconds; null for anything else or zero. */
export function parseEvery(text: string): number | null {
  const s = text.trim().toLowerCase().replace(/\s+/g, "");
  if (!/^(\d+[dhms])+$/.test(s)) return null;
  let total = 0;
  for (const [, n, unit] of s.matchAll(/(\d+)([dhms])/g)) {
    total += Number(n) * UNITS.find(([u]) => u === unit)![1];
  }
  return total > 0 ? total : null;
}

/** Seconds -> the shortest text parseEvery reads back ("2h30m"). */
export function formatEvery(seconds: number): string {
  let rest = Math.max(1, Math.round(seconds));
  let out = "";
  for (const [unit, size] of UNITS) {
    const n = Math.floor(rest / size);
    if (n) out += `${n}${unit}`;
    rest -= n * size;
  }
  return out;
}

/** The next grid point the coordinator's timer will fire (schedules.py rule). */
export function nextRun(row: HubSchedule): number {
  const fired = row.last_run?.fired_at;
  const after = typeof fired === "number" ? fired : row.anchor - row.interval_seconds;
  const k = Math.floor((after - row.anchor) / row.interval_seconds) + 1;
  return row.anchor + Math.max(0, k) * row.interval_seconds;
}

function clock(epoch: number) {
  return new Date(epoch * 1000).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function agentsLabel(agents: string[]) {
  return agents.length === 1 ? agents[0] : `${agents.length} agents`;
}

function StatusText({ active }: { active: boolean }) {
  return active ? (
    <span className="text-emerald-700 dark:text-emerald-400">Active</span>
  ) : (
    <span className="text-muted-foreground">Paused</span>
  );
}

type Draft = {
  prompt: string;
  every: string;
  agents: string;
  whenBusy: HubSchedule["when_busy"];
  active: boolean;
  skill: string;
};

function draftOf(row?: HubSchedule): Draft {
  return {
    prompt: row?.prompt ?? "",
    every: row ? formatEvery(row.interval_seconds) : "1h",
    agents: row?.agents.join(", ") ?? "",
    whenBusy: row?.when_busy ?? "queue",
    active: row?.active ?? true,
    skill: row?.skill ?? "",
  };
}

function Hint({ children }: { children: React.ReactNode }) {
  return <p className="text-muted-foreground mt-1 text-xs">{children}</p>;
}

const FIELD =
  "bg-muted/50 w-full rounded-md px-3 py-2 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50";

function ScheduleForm({
  row,
  saving,
  onCancel,
  onSave,
}: {
  row?: HubSchedule;
  saving: boolean;
  onCancel: () => void;
  onSave: (fields: HubScheduleFields) => void;
}) {
  const [d, setD] = useState<Draft>(() => draftOf(row));
  const [problem, setProblem] = useState("");
  const set = (patch: Partial<Draft>) => setD((prev) => ({ ...prev, ...patch }));

  const submit = () => {
    const interval = parseEvery(d.every);
    const agents = d.agents.split(",").map((a) => a.trim()).filter(Boolean);
    if (!d.prompt.trim()) return setProblem("Write the prompt to send.");
    if (interval === null) return setProblem("Every needs a time like 30s, 5m, 1h or 2h30m.");
    if (!agents.length) return setProblem("Runs in needs at least one agent name.");
    setProblem("");
    onSave({
      prompt: d.prompt.trim(),
      interval_seconds: interval,
      agents,
      when_busy: d.whenBusy,
      active: d.active,
      skill: d.skill.trim(),
    });
  };

  return (
    <div className="min-w-0 text-sm">
      <h3 className="font-medium">{row ? "Edit schedule" : "Add schedule"}</h3>
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        <label className="block sm:col-span-2">
          <span className="font-medium">Prompt</span>
          <textarea
            aria-label="Prompt"
            rows={3}
            className={`${FIELD} mt-1 resize-y`}
            value={d.prompt}
            onChange={(e) => set({ prompt: e.target.value })}
          />
          <Hint>Sent as a message to each agent when the schedule fires.</Hint>
        </label>
        <label className="block">
          <span className="font-medium">Every</span>
          <input
            aria-label="Every"
            className={`${FIELD} mt-1`}
            value={d.every}
            onChange={(e) => set({ every: e.target.value })}
          />
          <Hint>30s, 5m, 1h or 2h30m. Repeats until you pause or delete it.</Hint>
        </label>
        <label className="block">
          <span className="font-medium">Skill</span>
          <input
            aria-label="Skill"
            className={`${FIELD} mt-1`}
            placeholder="No skill"
            value={d.skill}
            onChange={(e) => set({ skill: e.target.value })}
          />
          <Hint>Optional. The agent that runs it must have this skill.</Hint>
        </label>
        <label className="block sm:col-span-2">
          <span className="font-medium">Runs in</span>
          <input
            aria-label="Runs in"
            className={`${FIELD} mt-1`}
            placeholder="lapis, infra@devbox"
            value={d.agents}
            onChange={(e) => set({ agents: e.target.value })}
          />
          <Hint>
            Agent names, separated by commas. Add @device for an agent on another
            device. An agent that is not running is started.
          </Hint>
        </label>
        <div className="sm:col-span-2">
          <span className="block font-medium">If the agent is busy</span>
          <div className="bg-muted/50 mt-1 grid w-full grid-cols-2 gap-1 rounded-md p-1 sm:inline-grid sm:w-auto">
            {(
              [
                ["queue", "Add to its queue"],
                ["new_instance", "Open a new instance"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={d.whenBusy === value}
                onClick={() => set({ whenBusy: value })}
                className={`rounded px-3 py-1.5 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 ${
                  d.whenBusy === value ? "bg-background font-medium" : "text-muted-foreground"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <Hint>
            Add to its queue: the prompt runs right after its current turn, in the
            same chat. A new instance runs it now, beside the busy one.
          </Hint>
        </div>
        <div className="sm:col-span-2">
          <button
            type="button"
            aria-pressed={d.active}
            onClick={() => set({ active: !d.active })}
            className="rounded font-medium outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <StatusText active={d.active} />
          </button>
          <Hint>
            {d.active ? "Press Active to pause." : "Press Paused to make it active."}{" "}
            Paused keeps the schedule without running it.
          </Hint>
        </div>
      </div>
      {problem ? (
        <p role="alert" className="text-destructive mt-3">
          {problem}
        </p>
      ) : null}
      <div className="mt-4 flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
        <Button type="button" size="sm" onClick={submit} disabled={saving}>
          Save schedule
        </Button>
      </div>
    </div>
  );
}

function ScheduleDetail({
  row,
  saving,
  onPause,
  onEdit,
  onDelete,
}: {
  row: HubSchedule;
  saving: boolean;
  onPause: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  useEffect(() => setConfirming(false), [row.id]);
  const last = row.last_run;
  return (
    <div className="min-w-0 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-base font-medium wrap-anywhere">{row.agents.join(", ")}</h3>
          <p className="text-muted-foreground text-xs">
            <StatusText active={row.active} />
            {row.created_by ? ` · Scheduled by ${row.created_by}` : ""}
          </p>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={onPause} disabled={saving}>
            {row.active ? "Pause" : "Resume"}
          </Button>
          <Button type="button" variant="secondary" size="sm" onClick={onEdit} disabled={saving}>
            Edit
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="text-destructive"
            disabled={saving}
            onClick={() => (confirming ? onDelete() : setConfirming(true))}
          >
            {confirming ? "Confirm delete" : "Delete"}
          </Button>
        </div>
      </div>
      <p className="bg-muted/50 mt-3 rounded-md px-3 py-2 whitespace-pre-wrap wrap-anywhere">
        {row.prompt}
      </p>
      <dl className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-4">
        {(
          [
            ["Every", formatEvery(row.interval_seconds)],
            ["Next", row.active ? clock(nextRun(row)) : "Paused"],
            ["Last run", last?.fired_at ? clock(last.fired_at) : "Not yet"],
            ["Skill", row.skill || "None"],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="bg-muted/50 min-w-0 rounded-md px-3 py-2">
            <dt className="text-muted-foreground text-xs">{label}</dt>
            <dd className="mt-0.5 wrap-anywhere">{value}</dd>
          </div>
        ))}
      </dl>
      {last?.result === "error" ? (
        <p className="text-destructive mt-3 wrap-anywhere">
          The last run failed{last.agent ? ` for ${last.agent}` : ""}: {last.error || "no reason given"}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The editable schedules list (v16): ~/.kollab/schedules.json, saved through
 * the hub coordinator so the timer and every device follow the change.
 */
export function SchedulesSection({ api }: { api: EngineApi }) {
  const [rows, setRows] = useState<HubSchedule[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit" | "add">("view");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);

  const load = useCallback(async () => {
    const mine = ++epoch.current;
    setBusy(true);
    try {
      const data = await api.listSchedules();
      if (mine !== epoch.current) return;
      // An engine without the route's list shape reads as no schedules, not a crash.
      const schedules = Array.isArray(data?.schedules) ? data.schedules : [];
      setRows(schedules);
      setSelected((cur) => (schedules.some((r) => r.id === cur) ? cur : schedules[0]?.id ?? null));
    } catch (err) {
      if (mine === epoch.current) setError(errorText(err));
    } finally {
      if (mine === epoch.current) setBusy(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (work: () => Promise<unknown>, after?: () => void) => {
    setBusy(true);
    setError("");
    try {
      await work();
      after?.();
      await load();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  const current = rows?.find((r) => r.id === selected);
  const paused = rows?.filter((r) => !r.active).length ?? 0;

  return (
    <section aria-label="Schedules" className="text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-medium">
          Schedules{" "}
          <span className="text-muted-foreground text-sm font-normal">
            {rows ? `${rows.length}${paused ? ` · ${paused} paused` : ""}` : ""}
          </span>
        </h2>
        <div className="grid grid-cols-2 gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={() => void load()} disabled={busy}>
            <RefreshCw className={busy ? "size-4 animate-spin" : "size-4"} aria-hidden="true" />
            Refresh
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => {
              setError("");
              setMode("add");
            }}
            disabled={busy}
          >
            Add schedule
          </Button>
        </div>
      </div>
      {error ? (
        <p role="alert" className="text-destructive mt-2 wrap-anywhere">
          {error}
        </p>
      ) : null}
      <div className="mt-3 grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="bg-muted/30 min-w-0 self-start rounded-lg p-1">
          {rows && !rows.length ? (
            <p className="text-muted-foreground p-3">
              No schedules yet. Add schedule sends a prompt to an agent on a timer.
            </p>
          ) : null}
          {rows?.map((r) => {
            const every = `every ${formatEvery(r.interval_seconds)}`;
            const status = r.active ? "Active" : "Paused";
            return (
              <button
                key={r.id}
                type="button"
                aria-label={`${agentsLabel(r.agents)} ${every} ${r.prompt} ${status}`}
                aria-current={r.id === selected && mode !== "add" ? "true" : undefined}
                onClick={() => {
                  setSelected(r.id);
                  setMode("view");
                }}
                className={`block w-full rounded-md px-3 py-2 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 ${
                  r.id === selected && mode !== "add" ? "bg-muted" : "hover:bg-muted/60"
                }`}
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="min-w-0 truncate font-medium">{agentsLabel(r.agents)}</span>
                  <span className="shrink-0">{every}</span>
                </span>
                <span className="mt-0.5 flex items-baseline justify-between gap-2">
                  <span className="text-muted-foreground min-w-0 truncate">{r.prompt}</span>
                  <span className="shrink-0 text-xs">
                    <StatusText active={r.active} />
                  </span>
                </span>
                {r.last_run?.result === "error" ? (
                  <span className="text-destructive mt-0.5 block truncate text-xs">
                    {r.last_run.error || "Last run failed"}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
        <div className="min-w-0">
          {mode === "add" ? (
            <ScheduleForm
              key="add"
              saving={busy}
              onCancel={() => setMode("view")}
              onSave={(fields) => void run(() => api.createSchedule(fields), () => setMode("view"))}
            />
          ) : current && mode === "edit" ? (
            <ScheduleForm
              key={current.id}
              row={current}
              saving={busy}
              onCancel={() => setMode("view")}
              onSave={(fields) =>
                void run(() => api.updateSchedule(current.id, fields), () => setMode("view"))
              }
            />
          ) : current ? (
            <ScheduleDetail
              row={current}
              saving={busy}
              onPause={() => void run(() => api.updateSchedule(current.id, { active: !current.active }))}
              onEdit={() => {
                setError("");
                setMode("edit");
              }}
              onDelete={() => void run(() => api.deleteSchedule(current.id))}
            />
          ) : null}
        </div>
      </div>
    </section>
  );
}
