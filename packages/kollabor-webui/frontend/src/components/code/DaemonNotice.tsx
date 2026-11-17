import {
  AgentVersionNotice,
  type AgentVersionNoticeProps,
  type AgentVersionNoticeState,
} from "@/components/shell/AgentVersionNotice";

export type DaemonNoticeProps = Omit<AgentVersionNoticeProps, "notice" | "onDismiss"> & {
  notice: AgentVersionNoticeState | null;
  fallbackName: string;
};

/** Reuses the chat's working recovery action where a daemon-outdated error appears in a panel. */
export function DaemonNotice({ notice, fallbackName, ...props }: DaemonNoticeProps) {
  return (
    <AgentVersionNotice
      {...props}
      notice={notice ?? { kind: "unknown", detail: "Version information unavailable", name: fallbackName }}
    />
  );
}
