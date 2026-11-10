import type { PrChainEvent, PrChainItem } from "@/api";

/** The repository the pr-chain workflow opens PRs and issues in. */
export const PR_CHAIN_REPO = "kollaborai/kollab";

/** Statuses where the workflow stopped on a failure. They sort to the top and read in the destructive color. */
export const FAILED = new Set(["UI-FAILED", "BUILD-FAILED", "ERROR"]);
/** Statuses that wait for a person. They sort just after failures and read in the highlight color. */
export const NEEDS_HUMAN = new Set(["AWAIT-HUMAN"]);
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
  "issue-end": "Issue ended",
  review: "Review",
};

/** Workflow tokens in plain words. Unknown tokens come out in sentence case. */
export function statusLabel(token: string | undefined): string {
  if (!token) return "Unknown";
  const known = Object.hasOwn(LABELS, token) ? LABELS[token] : undefined;
  if (known) return known;
  const words = token.replace(/[-_]+/g, " ").trim().toLowerCase();
  return words ? words[0].toUpperCase() + words.slice(1) : "Unknown";
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
export function shortTime(stamp: string | undefined): string {
  const match = /T(\d{2}:\d{2})/.exec(stamp ?? "");
  return match ? match[1] : (stamp ?? "");
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

/** Hands out one check per request; only the newest request's check stays true. */
export function latestOnly(): () => () => boolean {
  let latest = 0;
  return () => {
    const mine = ++latest;
    return () => mine === latest;
  };
}

/** Counts requests that have started and not yet ended, so a timer can skip a tick while one is still running. */
export function inFlight(): { begin: () => () => void; busy: () => boolean } {
  let open = 0;
  return {
    begin: () => {
      open += 1;
      let ended = false;
      return () => {
        if (ended) return;
        ended = true;
        open -= 1;
      };
    },
    busy: () => open > 0,
  };
}
