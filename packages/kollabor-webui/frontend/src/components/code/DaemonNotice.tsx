import { NoticeBar } from "@/components/shell/NoticeBar";
import { OUTDATED_NOTICE } from "@/lib/code-fetch";

/** The plain line shown where a daemon that predates Code, Git and Terminal would otherwise show its raw error. */
export function DaemonNotice() {
  return <NoticeBar>{OUTDATED_NOTICE}</NoticeBar>;
}
