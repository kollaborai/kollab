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

function compactPath(value: string, keepSegments = 2): string {
  const normalized = value.replaceAll("\\", "/").replace(/^['"]|['"]$/g, "");
  const segments = normalized.split("/").filter(Boolean);
  if (segments.length <= keepSegments) return normalized;
  return `…/${segments.slice(-keepSegments).join("/")}`;
}

function basename(value: string): string {
  const normalized = value.replaceAll("\\", "/").replace(/^['"]|['"]$/g, "");
  return normalized.split("/").filter(Boolean).at(-1) ?? normalized;
}

function truncate(value: string, maxLength = 48): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength - 1).trimEnd()}…`;
}

function commandSteps(command: string): string[] {
  const normalized = command.replace(/\s+/g, " ").trim();
  if (!normalized) return [];
  return normalized.split(/&&|\|\||;|(?<!\|)\|(?!\|)/g).filter(Boolean);
}

function withStepCount(label: string, count: number): string {
  return count > 1 ? `${label} · ${count} steps` : label;
}

function pathArgument(command: string): string | undefined {
  const tokens = command.match(/(?:"[^"]*"|'[^']*'|\S+)/g) ?? [];
  const candidates = tokens
    .map((token) => token.replace(/^['"]|['"]$/g, ""))
    .filter(
      (token) =>
        !token.startsWith("-") &&
        !token.startsWith("%") &&
        (token.includes("/") || /\.[a-z0-9]{1,8}$/i.test(token)),
    );
  return candidates.at(-1);
}

function summarizeTerminalCommand(command: string): string {
  const steps = commandSteps(command);
  if (!steps.length) return "Run Command";

  const first = steps[0] ?? command;
  const count = steps.length;
  const normalized = first.trim();

  const searchMatch = normalized.match(/\b(?:rg|grep)\b[\s\S]*?["']([^"']+)["']/);
  if (/^(?:rg|grep)\b/.test(normalized)) {
    const searchTarget =
      searchMatch?.[1] ??
      normalized.replace(/^(?:rg|grep)\b(?:\s+--?\S+)*\s+/, "");
    return withStepCount(
      searchTarget
        ? `Search · ${truncate(searchTarget.replace(/^['"]|['"]$/g, ""))}`
        : "Search",
      count,
    );
  }

  if (/\bfind\b/.test(normalized)) {
    const target = pathArgument(normalized);
    return withStepCount(
      target ? `Find Files · ${compactPath(target, 3)}` : "Find Files",
      count,
    );
  }

  if (/^(?:sed|nl|cat|head|tail|less|more)\b/.test(normalized)) {
    const target = pathArgument(normalized);
    return withStepCount(
      target ? `Read File · ${basename(target)}` : "Read File",
      count,
    );
  }

  const gitMatch = normalized.match(/^git\s+([a-z-]+)/i);
  if (gitMatch?.[1]) {
    const action = gitMatch[1].toLowerCase();
    const labels: Record<string, string> = {
      add: "Git Add",
      branch: "Git Branches",
      checkout: "Git Checkout",
      commit: "Git Commit",
      diff: "Git Diff",
      log: "Git History",
      pull: "Git Pull",
      push: "Git Push",
      show: "Git Show",
      status: "Git Status",
    };
    const label = labels[action] ?? `Git ${action}`;
    const target = action === "diff" || action === "show" ? pathArgument(normalized) : undefined;
    return withStepCount(
      target ? `${label} · ${basename(target)}` : label,
      count,
    );
  }

  if (/^(?:python(?:3)?|pytest)\b.*\bpytest\b/.test(normalized)) {
    const target = pathArgument(normalized);
    return withStepCount(
      target ? `Run Tests · ${compactPath(target)}` : "Run Tests",
      count,
    );
  }

  if (/^(?:npm|pnpm|yarn)\s+/.test(normalized)) {
    const packageManager = normalized.split(/\s+/, 1)[0] ?? "package manager";
    const script = normalized.match(/\b(?:run|exec)\s+([^\s]+)/)?.[1];
    return withStepCount(
      script ? `Run ${packageManager} · ${script}` : `Run ${packageManager}`,
      count,
    );
  }

  const executable = normalized.match(/^(?:sudo\s+)?([^\s]+)/)?.[1];
  const labels: Record<string, string> = {
    cd: "Change Directory",
    echo: "Print Output",
    node: "Run Node",
    pwd: "Show Directory",
    python: "Run Python",
    python3: "Run Python",
  };
  return withStepCount(
    (executable && labels[executable]) ||
      (executable ? `Run ${executable}` : "Run Command"),
    count,
  );
}

export function humanizeToolName(toolName: string): string {
  const knownLabels: Record<string, string> = {
    hub_msg: "Hub Message",
    hub_work: "Hub Work",
    file_read: "Read File",
    file_write: "Write File",
    terminal: "Run Command",
  };
  const normalized = toolName.trim() || "tool";
  if (knownLabels[normalized]) return knownLabels[normalized];

  const leaf = normalized.split(/[:./]/).at(-1) ?? normalized;
  return leaf
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

/** Keep tool rows scannable while leaving the full request in the detail panel. */
export function summarizeToolCall(toolName: string, argsText?: string): string {
  const args = parseArgs(argsText);
  const normalizedName = toolName.trim() || "tool";

  if (normalizedName === "terminal" && typeof args.command === "string") {
    return summarizeTerminalCommand(args.command);
  }

  if (
    (normalizedName === "file_read" || normalizedName === "file_write") &&
    typeof args.file === "string"
  ) {
    return `${humanizeToolName(normalizedName)} · ${basename(args.file)}`;
  }

  return humanizeToolName(normalizedName);
}
