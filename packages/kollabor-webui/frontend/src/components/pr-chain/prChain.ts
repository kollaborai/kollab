import type { PrChainEvent, PrChainItem, PrChainSnapshot } from "@/api";

/** The repository the pr-chain workflow opens PRs and issues in. */
export const PR_CHAIN_REPO = "kollaborai/kollab";

/** Statuses where the workflow stopped on a failure. They sort to the top and read in the destructive color. */
export const FAILED = new Set(["UI-FAILED", "BUILD-FAILED", "ERROR", "GATE-FAILED", "NO-VERDICT", "UI-NO-VERDICT", "CI-RED"]);
/** Statuses that wait for a person. They sort just after failures and read in the highlight color. */
export const NEEDS_HUMAN = new Set(["AWAIT-HUMAN", "NEEDS-HUMAN", "LAND-STOPPED"]);
const DONE = new Set(["MERGED", "CLOSED"]);

export const itemUrl = (item: PrChainItem) =>
  `https://github.com/${PR_CHAIN_REPO}/${item.kind === "pr" ? "pull" : "issues"}/${item.number}`;

export type StatusTone = "failed" | "attention" | "active" | "done" | "idle";

const LABELS: Record<string, string> = {
  "AWAIT-HUMAN": "Waiting for you",
  "human-gate": "Waiting for you",
  "BUILD-FAILED": "Build failed",
  "UI-FAILED": "UI failed",
  ERROR: "Error",
  REVIEWING: "Reviewing",
  MERGED: "Merged",
  CLOSED: "Closed",
  "ui-review": "UI review",
  "issue-end": "Ended",
  review: "Review",
};

/** Workflow tokens in plain words. Unknown tokens come out in sentence case. */
export function statusLabel(token: string | undefined): string {
  if (!token) return "Unknown";
  const known = Object.hasOwn(LABELS, token) ? LABELS[token] : undefined;
  if (known) return known;
  const words = token.replace(/[-_]+/g, " ").trim().toLowerCase();
  if (!words) return "Unknown";
  const text = words.replace(/\b(pr|ci|ui|id)\b/g, (acronym) => acronym.toUpperCase());
  return text[0].toUpperCase() + text.slice(1);
}

/**
 * The verdict and reason line under a row. The runner keeps the reason and verdict of an earlier
 * stop when a run starts again, so a live run shows neither and a ready one shows only its verdict.
 */
export function itemDetail(item: PrChainItem): string {
  if (item.claimed || item.status?.toLowerCase() === "running") return "";
  const reason = item.status === "READY" ? undefined : item.why;
  return [item.last_verdict ? statusLabel(item.last_verdict) : "", reason].filter(Boolean).join(" · ");
}

export function statusTone(item: PrChainItem): StatusTone {
  const status = item.status ?? "";
  if (FAILED.has(status)) return "failed";
  if (NEEDS_HUMAN.has(status)) return "attention";
  if (DONE.has(status)) return "done";
  return item.claimed ? "active" : "idle";
}

/** Attention first, then running, then waiting, then finished; newest first inside a group. */
export function sortItems(items: PrChainItem[]): PrChainItem[] {
  const rank: Record<StatusTone, number> = { failed: 0, attention: 1, active: 2, idle: 3, done: 4 };
  return [...items].sort(
    (a, b) =>
      rank[statusTone(a)] - rank[statusTone(b)] ||
      (b.updated ?? "").localeCompare(a.updated ?? ""),
  );
}

/** "2026-11-09T17:27:03" to "17:27". Anything else is shown as given. */
export function shortTime(stamp: string | undefined, now: Date = new Date()): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})/.exec(stamp ?? "");
  if (!match) return stamp ?? "";
  const [, year, month, day, time] = match;
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  return `${year}-${month}-${day}` === today ? time : `${month}-${day} ${time}`;
}

/** One plain line for a ledger event, e.g. "PR 960 review: PASS". */
export function describeEvent(event: PrChainEvent): string {
  const target = event.issue != null ? `Issue ${event.issue}` : event.pr != null ? `PR ${event.pr}` : "";
  const parts = [event.stage, event.status ?? event.verdict]
    .filter(Boolean)
    .map((part) => statusLabel(part))
    .filter((label, index, all) => all.indexOf(label) === index);
  return [target, parts.join(": ")].filter(Boolean).join(" ");
}

/** Hands out one check per request. A request's answer counts unless a later request's answer was already shown,
 * so a slow poll cannot overwrite newer data, and polls that each take longer than the refresh gap still show. */
export function inOrder(): () => () => boolean {
  let issued = 0;
  let shown = 0;
  return () => {
    const mine = ++issued;
    return () => {
      if (mine < shown) return false;
      shown = mine;
      return true;
    };
  };
}

/** Counts requests that have started and not yet ended, so a timer can skip a tick while one is still running.
 * A request older than `limitMs` is aborted and forgotten, so one that never settles cannot stop
 * refreshing for good or pile up; `abortAll` cancels the rest when the dialog closes. */
export function inFlight(
  limitMs = 30_000,
  now: () => number = Date.now,
): { begin: () => { signal: AbortSignal; end: () => void }; busy: () => boolean; pending: () => number; abortAll: () => void } {
  const open = new Map<symbol, { startedAt: number; controller: AbortController }>();
  const forget = () => {
    for (const [id, request] of open) {
      if (now() - request.startedAt < limitMs) continue;
      request.controller.abort();
      open.delete(id);
    }
  };
  return {
    begin: () => {
      forget();
      const id = Symbol("request");
      const controller = new AbortController();
      open.set(id, { startedAt: now(), controller });
      return {
        signal: controller.signal,
        end: () => {
          open.delete(id);
        },
      };
    },
    busy: () => {
      forget();
      return open.size > 0;
    },
    pending: () => open.size,
    abortAll: () => {
      for (const request of open.values()) request.controller.abort();
      open.clear();
    },
  };
}

/** The line under the list when the engine left folders out; null when everything was listed. */
export function listNote(snapshot: PrChainSnapshot): string | null {
  return snapshot.truncated ? "Some items are not listed." : null;
}
