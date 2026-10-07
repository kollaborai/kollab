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

/**
 * The gem action for a tool call. Live calls arrive as display names
 * ("terminal: ls", "web-search: q"); history carries the bare tool name.
 * Anything not looking, talking or tasking is hands-on work at the laptop.
 */
export function toolActivity(name: string): Activity {
  const key = (name.match(/^([\w-]+):(?:\s|$)/)?.[1] ?? name).replaceAll("-", "_");
  return TOOL_ACTIVITY[key] ?? (key.startsWith("task_") ? "tasking" : "typing");
}

/**
 * What the open session's gem acts out, read from its live thread. Null while
 * nothing runs, so the sidebar falls back to the hub's presence state.
 */
export function useThreadActivity(failed: boolean): Activity | null {
  return useAuiState((s) => {
    const last = s.thread.messages.at(-1);
    if (!s.thread.isRunning) {
      const errored = last?.role === "assistant" && last.status?.type === "incomplete" && last.status.reason === "error";
      return failed || errored ? "error" : null;
    }
    if (last?.role !== "assistant") return "thinking";
    const part = last.parts.at(-1);
    if (part?.type === "tool-call" && part.result === undefined) return toolActivity(part.toolName);
    if (part?.type === "text" && part.text) return "speaking";
    return "thinking";
  });
}
