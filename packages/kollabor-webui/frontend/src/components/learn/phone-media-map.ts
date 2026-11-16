import phoneLearnMedia from "virtual:kollab-learn-media";
import { resolveLearnMediaSource } from "./media";

export function getLearnMediaSource(source: string): string | null {
  const pathname = typeof window === "undefined" ? "/" : window.location.pathname;
  return resolveLearnMediaSource(source, pathname, phoneLearnMedia);
}
