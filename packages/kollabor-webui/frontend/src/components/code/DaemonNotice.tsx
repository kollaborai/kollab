import {
  AgentVersionNotice,
  type AgentVersionNoticeProps,
  type AgentVersionNoticeState,
} from "@/components/shell/AgentVersionNotice";
import { NoticeBar } from "@/components/shell/NoticeBar";
import { OUTDATED_NOTICE } from "@/lib/code-fetch";

export type DaemonNoticeProps = Omit<AgentVersionNoticeProps, "notice" | "onDismiss"> & {
  notice: AgentVersionNoticeState | null;
};

/** Reuses the chat's working recovery action where a daemon-outdated error appears in a panel. */
export function DaemonNotice({ notice, ...props }: DaemonNoticeProps) {
  if (!notice) return <NoticeBar urgent>{OUTDATED_NOTICE}</NoticeBar>;
  return (
    <AgentVersionNotice
      {...props}
      notice={notice}
    />
  );
}
