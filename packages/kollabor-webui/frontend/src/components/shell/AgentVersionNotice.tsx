import type { LiveAgent } from "@/api";
import { NoticeAction, NoticeBar } from "@/components/shell/NoticeBar";

/** What a chat says when its agent runs other code than the engine. */
export type AgentVersionNoticeState = {
  /** `button`: Restart Agent is on. `command`: it is off, so the line names /restart. */
  kind: "button" | "command";
  /** Both versions, for the hover: they never go in the line. */
  detail: string;
};

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
  return {
    kind: restartAgents ? "button" : "command",
    detail: `Agent ${running || "(version unknown)"} · engine ${engineVersion}`,
  };
}

/**
 * One quiet line above the chat: this agent runs an older version of Kollab. With Restart Agent on,
 * the button restarts it where it stands (same chat, same history); off, the line names /restart.
 */
export function AgentVersionNotice({
  notice,
  busy,
  waiting = false,
  failure = "",
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
  onRestart: () => void;
  onDismiss?: () => void;
}) {
  if (failure) {
    return (
      <NoticeBar
        urgent
        detail={notice.detail}
        actions={<NoticeAction onClick={onRestart}>Try Again</NoticeAction>}
        onDismiss={onDismiss}
      >
        {`Couldn't restart this agent. ${failure}`}
      </NoticeBar>
    );
  }
  if (busy) {
    return (
      <NoticeBar detail={notice.detail}>
        {waiting ? "This agent restarts when its turn ends." : "Restarting this agent…"}
      </NoticeBar>
    );
  }
  if (notice.kind === "command") {
    return (
      <NoticeBar detail={notice.detail} onDismiss={onDismiss}>
        Run /restart to update the agent version running.
      </NoticeBar>
    );
  }
  return (
    <NoticeBar
      detail={notice.detail}
      actions={<NoticeAction onClick={onRestart}>Restart Agent</NoticeAction>}
      onDismiss={onDismiss}
    >
      This agent runs an older version of Kollab.
    </NoticeBar>
  );
}
