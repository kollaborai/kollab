import { useId, useState } from "react";
import { Info } from "lucide-react";
import type { LiveAgent } from "@/api";
import { NoticeAction, NoticeBar } from "@/components/shell/NoticeBar";
import { cn } from "@/lib/utils";

function VersionDetails({ detail }: { detail: string }) {
  const [expanded, setExpanded] = useState(false);
  const contentId = useId();

  return (
    <>
      {/* Collapsed, the disclosure is one icon inline with the actions; it never takes its own row. */}
      <details className="min-w-0" onToggle={(event) => setExpanded(event.currentTarget.open)}>
        <summary aria-label="Version details" aria-expanded={expanded} aria-controls={contentId} title={detail} className="group/version relative min-h-7 list-none cursor-pointer px-1 text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 [&::-webkit-details-marker]:hidden">
          <Info aria-hidden="true" className="size-3" />
        </summary>
      </details>
      {/* Expanded, the detail text is the strip's last row: full width, below the Dismiss. */}
      <span id={contentId} className={cn("order-last w-full break-words text-muted-foreground", !expanded && "hidden")}>
        {expanded ? detail : null}
      </span>
    </>
  );
}

/** What a chat says when its agent runs other code than the engine. */
export type AgentVersionNoticeState = {
  /**
   * `too-old`: the agent publishes no version, so it started before Restart Agent existed and cannot
   * restart itself. `button`: Restart Agent is on. `command`: it is off, so the line names /restart.
   * `unknown`: the daemon is outdated, but the page has no agent version to choose a more specific action.
   */
  kind: "too-old" | "button" | "command" | "unknown";
  /** Version details for the disclosure; never included in the notice line. */
  detail: string;
  /** The agent's name, for the stop-and-start line. */
  name: string;
};

/**
 * The notice for one chat, or null while its agent runs the engine's version. An agent that publishes
 * no version started before agents did, so it counts as older even while the engine version loads.
 * A versioned agent waits for the engine version; with no agent row there is no identity to guide.
 */
export function agentVersionNotice({
  engineVersion,
  agent,
  restartAgents,
}: {
  engineVersion: string;
  agent: LiveAgent | undefined;
  restartAgents: boolean;
}): AgentVersionNoticeState | null {
  if (!agent) return null;
  const running = agent.version ?? "";
  if (!engineVersion) {
    if (running) return null;
    return { kind: "too-old", detail: "Agent (version unknown) · engine version loading", name: agent.name };
  }
  if (running === engineVersion) return null;
  const detail = `Agent ${running || "(version unknown)"} · engine ${engineVersion}`;
  // No version means a build from before /restart restarted in place. There /restart is an alias of /new,
  // which wipes the conversation, so that agent must use the engine's restart action.
  if (!running) return { kind: "too-old", detail, name: agent.name };
  return { kind: restartAgents ? "button" : "command", detail, name: agent.name };
}

/**
 * One quiet line above the chat: this agent runs an older version of Kollab. With Restart Agent on,
 * the button restarts it where it stands (same chat, same history); off, the line names /restart.
 * An agent that cannot restart itself is restarted by the engine through the same in-page action.
 */
export type AgentVersionNoticeProps = {
  notice: AgentVersionNoticeState;
  /** A restart was asked for and the agent has not come back yet. */
  busy: boolean;
  /** The agent lets its turn in progress end first. */
  waiting?: boolean;
  /** Why the last ask failed. */
  failure?: string;
  /** The engine refused the ask because the agent cannot restart itself (see `isAgentTooOld`). */
  tooOld?: boolean;
  onRestart: () => void;
  onDismiss?: () => void;
};

export function AgentVersionNotice({
  notice,
  busy,
  waiting = false,
  failure = "",
  tooOld = false,
  onRestart,
  onDismiss,
}: AgentVersionNoticeProps) {
  if (failure && tooOld) {
    return (
      <NoticeBar
        urgent
        detail={notice.detail}
        details={<VersionDetails detail={notice.detail} />}
        actions={<NoticeAction onClick={onRestart}>Try Again</NoticeAction>}
        onDismiss={onDismiss}
      >
        This agent could not be restarted by this engine.
      </NoticeBar>
    );
  }
  if (failure) {
    return (
      <NoticeBar
        urgent
        detail={notice.detail}
        details={<VersionDetails detail={notice.detail} />}
        actions={<NoticeAction onClick={onRestart}>Try Again</NoticeAction>}
        onDismiss={onDismiss}
      >
        {`Couldn't restart this agent. ${failure}`}
      </NoticeBar>
    );
  }
  if (busy) {
    return (
      <NoticeBar detail={notice.detail} details={<VersionDetails detail={notice.detail} />}>
        {waiting ? "This agent restarts when its turn ends." : "Restarting this agent…"}
      </NoticeBar>
    );
  }
  if (notice.kind === "too-old" || notice.kind === "unknown") {
    return (
      <NoticeBar
        detail={notice.detail}
        details={<VersionDetails detail={notice.detail} />}
        actions={<NoticeAction onClick={onRestart}>Restart Agent</NoticeAction>}
        onDismiss={onDismiss}
      >
        This agent runs an older version of Kollab.
      </NoticeBar>
    );
  }
  if (notice.kind === "command") {
    return (
      <NoticeBar detail={notice.detail} details={<VersionDetails detail={notice.detail} />} onDismiss={onDismiss}>
        Run /restart to update the agent version running.
      </NoticeBar>
    );
  }
  return (
    <NoticeBar
      detail={notice.detail}
      details={<VersionDetails detail={notice.detail} />}
      actions={<NoticeAction onClick={onRestart}>Restart Agent</NoticeAction>}
      onDismiss={onDismiss}
    >
      This agent runs an older version of Kollab.
    </NoticeBar>
  );
}
