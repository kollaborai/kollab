function formatJsonValue(value: unknown, depth: number): string {
  const indent = " ".repeat(depth * 2);

  if (typeof value === "string") {
    if (!/[\r\n]/.test(value)) return JSON.stringify(value);
    const childIndent = " ".repeat((depth + 1) * 2);
    const lines = value.replace(/\r\n?/g, "\n").split("\n");
    return `|\n${lines.map((line) => `${childIndent}${line}`).join("\n")}`;
  }
  if (value === null) return "null";
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    if (!value.length) return "[]";
    const childIndent = " ".repeat((depth + 1) * 2);
    return `[\n${value
  .map((item) => `${childIndent}${formatJsonValue(item, depth + 1)}`)
  .join(",\n")}\n${indent}]`;
  }
  if (typeof value === "object") {
    return formatObject(value as Record<string, unknown>, depth);
  }
  return String(value);
}

function formatObject(value: Record<string, unknown>, depth: number): string {
  const indent = " ".repeat(depth * 2);
  const entries = Object.entries(value);
  if (!entries.length) return "{}";
  const childIndent = " ".repeat((depth + 1) * 2);
  return `{\n${entries
    .map(
      ([key, item]) =>
        `${childIndent}${JSON.stringify(key)}: ${formatDisplayValue(item, depth + 1)}`,
    )
    .join(",\n")}\n${indent}}`;
}

function formatDisplayValue(value: unknown, depth: number): string {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return formatObject(value as Record<string, unknown>, depth);
  }
  return formatJsonValue(value, depth);
}

function parseJson(value: string): unknown | undefined {
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return undefined;
  }
}

function formatResultEnvelope(value: string): string | undefined {
  const match = value.match(/(^|\n)Result:\s*/i);
  if (!match || match.index === undefined) return undefined;

  const resultStart = match.index + match[0].length;
  const parsed = parseJson(value.slice(resultStart).trim());
  if (parsed === undefined) return undefined;

  const prefix = value.slice(0, match.index).trimEnd();
  return `${prefix ? `${prefix}\n\n` : ""}${formatDisplayValue(parsed, 0)}`;
}

function decodeEscapedText(value: string): string {
  if (/[\r\n]/.test(value)) return value;
  if (!/\\(?:r\\n|n|r|t)/.test(value)) return value;
  return value
    .replace(/\\r\\n/g, "\n")
    .replace(/\\n/g, "\n")
    .replace(/\\r/g, "\r")
    .replace(/\\t/g, "\t");
}

/** Format JSON-shaped tool content without rendering Markdown. */
export function formatContent(value: unknown): string {
  if (value === undefined) return "";
  if (value === null) return "null";
  if (typeof value !== "string") return formatDisplayValue(value, 0);

  const trimmed = value.trim();
  if (!trimmed) return value;

  const parsed = parseJson(trimmed);
  if (parsed !== undefined) return formatDisplayValue(parsed, 0);

  return formatResultEnvelope(value) ?? decodeEscapedText(value);
}
