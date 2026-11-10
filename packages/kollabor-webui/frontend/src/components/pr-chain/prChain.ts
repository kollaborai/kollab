import type { PrChainEvent, PrChainItem } from "@/api";

/** The repository the pr-chain workflow opens PRs and issues in. */
export const PR_CHAIN_REPO = "kollaborai/kollab";

/** Statuses that need a person. They sort to the top and read in the warning color. */
export const NEEDS_HUMAN = new Set(["AWAIT-HUMAN", "UI-FAILED", "BUILD-FAILED", "ERROR"]);
const DONE = new Set(["MERGED", "CLOSED"]);

export const itemUrl = (item: PrChainItem) =>
  `https://github.com/${PR_CHAIN_REPO}/${item.kind === "pr" ? "pull" : "issues"}/${item.number}`;

export type StatusTone = "attention" | "active" | "done" | "idle";

export function statusTone(item: PrChainItem): StatusTone {
  const status = item.status ?? "";
  if (NEEDS_HUMAN.has(status)) return "attention";
  if (DONE.has(status)) return "done";
  return item.claimed ? "active" : "idle";
}

/** Attention first, then running, then waiting, then finished; newest first inside a group. */
export function sortItems(items: PrChainItem[]): PrChainItem[] {
  const rank: Record<StatusTone, number> = { attention: 0, active: 1, idle: 2, done: 3 };
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
  const parts = [event.stage, event.status ?? event.verdict].filter(Boolean);
  return [target, parts.join(": ")].filter(Boolean).join(" ");
}
