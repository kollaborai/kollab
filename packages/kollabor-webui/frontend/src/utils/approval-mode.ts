export const APPROVAL_MODE_OPTIONS = [
  { value: "confirm_all", label: "Confirm All" },
  { value: "default", label: "Default" },
  { value: "auto_approve_edits", label: "Auto-Approve Edits" },
  { value: "trust_all", label: "Trust All" },
] as const;

type ApprovalMode = (typeof APPROVAL_MODE_OPTIONS)[number]["value"];

const MODE_BY_ORDINAL: Record<string, ApprovalMode> = {
  "1": "default",
  "2": "confirm_all",
  "3": "auto_approve_edits",
  "4": "trust_all",
};

const APPROVAL_MODE_LABELS: Record<ApprovalMode, string> =
  Object.fromEntries(
    APPROVAL_MODE_OPTIONS.map(({ value, label }) => [value, label]),
  ) as Record<ApprovalMode, string>;

function titleizeMode(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

export function normalizeApprovalMode(value: unknown): string {
  if (value === null || value === undefined) return "confirm_all";
  const raw = String(value);
  return MODE_BY_ORDINAL[raw] ?? raw.toLowerCase();
}

export function formatApprovalMode(value: unknown): string {
  const normalized = normalizeApprovalMode(value);
  return (
    APPROVAL_MODE_LABELS[normalized as ApprovalMode] ??
    titleizeMode(normalized)
  );
}
