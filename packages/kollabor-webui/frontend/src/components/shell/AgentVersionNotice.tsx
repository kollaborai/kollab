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
   */
  kind: "too-old" | "button" | "command";
  /** Both versions, for the hover: they never go in the line. */
  detail: string;
  /** The agent's name, for the command in a too-old line. */
  name: string;
};

/** The line for an agent that cannot restart itself: the stop-and-start command, in plain words (#968). */
function tooOldLine(name: string): string {
  return `This agent started before Restart Agent existed. Run kollab --hub stop ${name} in a terminal, then select its Offline row in the sidebar to start it again.`;
}

/**
 * The notice for one chat, or null while its agent runs the engine's version. An agent that publishes
 * no version started before agents did, so it counts as older. With no agent row (still starting,
 * off the hub, or on another computer) or no engine version there is nothing to compare.
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
  if (!agent || !engineVersion) return null;
  const running = agent.version ?? "";
  if (running === engineVersion) return null;
  const detail = `Agent ${running || "(version unknown)"} · engine ${engineVersion}`;
  // No version means a build from before /restart restarted in place. There /restart is an alias of /new,
  // which wipes the conversation, so that agent gets the stop-and-start line and no button or /restart.
  if (!running) return { kind: "too-old", detail, name: agent.name };
  return { kind: restartAgents ? "button" : "command", detail, name: agent.name };
}

/**
 * One quiet line above the chat: this agent runs an older version of Kollab. With Restart Agent on,
 * the button restarts it where it stands (same chat, same history); off, the line names /restart. An
 * agent that cannot restart itself gets its stop-and-start command instead, with no button.
 */
export function AgentVersionNotice({
  notice,
  busy,
  waiting = false,
  failure = "",
  tooOld = false,
  onRestart,
  onDismiss,
}: {
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
}) {
  if (failure && tooOld) {
    return (
      <NoticeBar urgent detail={notice.detail} details={<VersionDetails detail={notice.detail} />} onDismiss={onDismiss}>
        {tooOldLine(notice.name)}
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
  if (notice.kind === "too-old") {
    return (
      <NoticeBar detail={notice.detail} details={<VersionDetails detail={notice.detail} />} onDismiss={onDismiss}>
        {tooOldLine(notice.name)}
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
