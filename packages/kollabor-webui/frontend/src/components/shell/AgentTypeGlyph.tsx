import type { AgentPoolEntry } from "@/api";

type AgentTypeSource = Pick<AgentPoolEntry, "agent_type" | "agent">;

/** One restrained text glyph for each bundled agent type. */
export const AGENT_TYPE_GLYPHS: Readonly<Record<string, string>> = Object.freeze({
  coder: "▸",
  community: "◉",
  "creative-writer": "✎",
  "data-analyst": "∑",
  default: "●",
  kollabor: "⬟",
  koordinator: "✦",
  coordinator: "✦",
  maintainer: "⛭",
  native: "⌘",
  "pr-shepherd": "⚑",
  "release-planner": "⇧",
  research: "◈",
  system: "⌬",
  "tech-dude": "⌁",
  "technical-writer": "¶",
  triager: "⊙",
  local: "L",
  mentiko: "M",
});

const UNKNOWN_AGENT_TYPE_GLYPH = "◇";

function nonBlank(value?: string): string | undefined {
  const trimmed = value?.trim();
  return trimmed || undefined;
}

/** Resolve the visible type from roster data, with a session bundle as fallback. */
export function resolveSidebarAgentType(
  entry?: AgentTypeSource,
  fallback?: string,
): string | undefined {
  if (!entry) return nonBlank(fallback);
  return nonBlank(entry.agent_type) ?? nonBlank(entry.agent) ?? "default";
}

export function AgentTypeGlyph({ type }: { type?: string }) {
  const label = nonBlank(type);
  if (!label) return null;

  const glyph = AGENT_TYPE_GLYPHS[label.toLowerCase()] ?? UNKNOWN_AGENT_TYPE_GLYPH;
  return (
    <span
      aria-label={label}
      className="text-muted-foreground ml-1 inline-block shrink-0 align-baseline text-[0.75em] leading-none"
      data-agent-type-glyph
      role="img"
      title={label}
    >
      {glyph}
    </span>
  );
}
