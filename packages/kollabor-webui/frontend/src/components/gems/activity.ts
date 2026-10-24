import { useAuiState } from "@assistant-ui/react";
import type { Activity } from "./gem-face";

const TOOL_ACTIVITY: Record<string, Activity> = {
  file_read: "reading",
  terminal_output: "reading",
  terminal_status: "reading",
  file_grep: "searching",
  web_search: "searching",
  web_fetch: "searching",
  tool_search: "searching",
  hub_msg: "messaging",
  hub_broadcast: "messaging",
  hub_ask_ctx: "messaging",
};

/** A harness (ACP) call names its kind; its title is display text. */
const ACP_KIND: Record<string, Activity> = {
  read: "reading",
  search: "searching",
  fetch: "searching",
  think: "thinking",
};

/**
 * The gem action for a tool call. Live calls arrive as display names
 * ("terminal: ls", "web-search: q"); history carries the bare tool name.
 * Anything not looking, talking or tasking is hands-on work at the laptop.
 */
export function toolActivity(name: string, kind?: string): Activity {
  if (kind && ACP_KIND[kind]) return ACP_KIND[kind];
  const key = (name.match(/^([\w-]+):(?:\s|$)/)?.[1] ?? name).replaceAll("-", "_");
  return TOOL_ACTIVITY[key] ?? (key.startsWith("task_") ? "tasking" : "typing");
}

/**
 * What a sidebar gem whose chat is not open acts out: the tool its presence
 * says it runs. Null between tools, so the gem falls back to presence state.
 */
export function presenceActivity(tool?: { name: string; type: string } | null): Activity | null {
  return tool?.name ? toolActivity(tool.name, tool.type) : null;
}

/** The ACP kind on a harness card's result (`metadata.acp.kind`), if any. */
function harnessKind(result: unknown): string | undefined {
  const acp = (result as { metadata?: { acp?: { kind?: unknown } } } | undefined)?.metadata?.acp;
  return typeof acp?.kind === "string" ? acp.kind : undefined;
}

/**
 * A turn this page did not start (a hub message woke the agent, or it was
 * typed in the terminal) never runs the thread, so its gem reads the session's
 * event stream instead: running tools, then thinking or speaking.
 */
export type LiveActivity = { tools: ReadonlyArray<readonly [string, Activity]>; phase: Activity | null };
export const NO_LIVE_ACTIVITY: LiveActivity = { tools: [], phase: null };

export function nextLiveActivity(live: LiveActivity, event: { type?: string; [key: string]: unknown }): LiveActivity {
  // The engine marks a cancelled turn's late tool events (its tool ignored
  // the stop signal), so none restarts the gem; the next turn arrives unmarked.
  if (event.cancelled) return live;
  const id = String(event.tool_id ?? "");
  switch (event.type) {
    case "tool_start": {
      const kind = typeof event.tool_type === "string" ? event.tool_type : undefined;
      const act = toolActivity(String(event.tool_name ?? ""), kind);
      return { ...live, tools: [...live.tools.filter(([tool]) => tool !== id), [id, act]] };
    }
    case "tool_result":
      // The model reads the result next.
      return { tools: live.tools.filter(([tool]) => tool !== id), phase: "thinking" };
    case "thinking":
      return { ...live, phase: "thinking" };
    case "token":
      return { ...live, phase: "speaking" };
    case "turn_complete":
    case "turn_cancelled": // a stopped turn may never report its running tool's result
    case "error":
      return NO_LIVE_ACTIVITY;
    default:
      return live;
  }
}

export function liveActivity(live: LiveActivity): Activity | null {
  return live.tools.at(-1)?.[1] ?? live.phase;
}

/**
 * What the open session's gem acts out, read from its live thread while this
 * page runs the turn, else from `live` (the event stream). Null while nothing
 * runs, so the sidebar falls back to the hub's presence state.
 */
export function useThreadActivity(failed: boolean, live: Activity | null = null): Activity | null {
  const thread = useAuiState((s) => {
    const last = s.thread.messages.at(-1);
    if (!s.thread.isRunning) {
      const errored = last?.role === "assistant" && last.status?.type === "incomplete" && last.status.reason === "error";
      return failed || errored ? "error" : null;
    }
    if (last?.role !== "assistant") return "thinking";
    const part = last.parts.at(-1);
    // A harness card carries an interim result while it runs.
    if (part?.type === "tool-call" && (part.result === undefined || part.isPreliminary)) {
      return toolActivity(part.toolName, harnessKind(part.result));
    }
    if (part?.type === "text" && part.text) return "speaking";
    return "thinking";
  });
  return thread ?? live;
}
