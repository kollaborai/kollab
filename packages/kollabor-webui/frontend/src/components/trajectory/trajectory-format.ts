export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(2)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${(seconds % 60).toFixed(1).padStart(4, "0")}s`;
}

export function formatTimestamp(timestamp: string | null): string {
  if (!timestamp) return "No timestamp";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "medium",
  });
}

export function formatTokens(value: number | undefined): string {
  return value === undefined || !Number.isFinite(value)
    ? "—"
    : Math.round(value).toLocaleString();
}

export function formatKind(kind: string): string {
  return kind.replace(/-/g, " ");
}
