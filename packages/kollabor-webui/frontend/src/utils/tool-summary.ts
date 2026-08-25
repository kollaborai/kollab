function parseArgs(argsText?: string): Record<string, unknown> {
  if (!argsText) return {};
  try {
    const value = JSON.parse(argsText) as unknown;
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function compactCommand(command: string): string {
  const normalized = command.replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  const steps = normalized.split(/&&|\|\||;|(?<!\|)\|(?!\|)/g);
  const first = steps[0]?.trim() || normalized;
  const count = steps.filter(Boolean).length;
  return count > 1 ? `${first} · ${count} steps` : first;
}

/** Keep tool rows scannable while leaving the full request in the detail panel. */
export function summarizeToolCall(toolName: string, argsText?: string): string {
  const args = parseArgs(argsText);
  const normalizedName = toolName.trim() || "tool";

  if (normalizedName === "terminal" && typeof args.command === "string") {
    const command = compactCommand(args.command);
    return command ? `terminal · ${command}` : "terminal";
  }

  if (
    (normalizedName === "file_read" || normalizedName === "file_write") &&
    typeof args.file === "string"
  ) {
    return `${normalizedName} · ${args.file}`;
  }

  return normalizedName;
}
