export type ContextCompactionStatusName =
  | "paused"
  | "compacting"
  | "ready_to_retry"
  | "applied"
  | "failed";

export interface ContextCompactionStatus {
  status: ContextCompactionStatusName;
  message: string;
  action?: string;
}

const STATUS_NAMES = new Set<string>([
  "paused",
  "compacting",
  "ready_to_retry",
  "applied",
  "failed",
]);

/** Parse a semantic context-compaction event; undefined means unrelated. */
export function parseContextCompactionStatus(
  event: Record<string, unknown>,
): ContextCompactionStatus | null | undefined {
  if (event.type !== "context_compaction_status") return undefined;

  const status = typeof event.status === "string" ? event.status : "failed";
  if (status === "idle") return null;

  return {
    status: STATUS_NAMES.has(status)
      ? (status as ContextCompactionStatusName)
      : "failed",
    message:
      typeof event.message === "string"
        ? event.message
        : "Conversation context is being compacted.",
    action: typeof event.action === "string" ? event.action : undefined,
  };
}

export function contextCompactionStatusTitle(
  status: ContextCompactionStatusName,
): string {
  switch (status) {
    case "paused":
      return "Automatic context compaction paused";
    case "compacting":
      return "Reducing conversation context";
    case "ready_to_retry":
      return "Context reduction ready";
    case "applied":
      return "Context reduction applied";
    case "failed":
      return "Context reduction needs attention";
  }
}

export function isUrgentContextCompactionStatus(
  status: ContextCompactionStatusName,
): boolean {
  return status === "paused" || status === "failed";
}
