import type { EngineConfig } from "@/api";

export type BuildIdentity = { branch: string; sha: string };
export type VersionRow = { label: string; value: string; title?: string };

/**
 * Rows for Settings > Versions. Package versions come from GET /api/config;
 * build identity is embedded in the Vite bundle so the signed PhoneLink app
 * does not need access to the token-bearing config response.
 */
export function versionRows(
  config: EngineConfig | null,
  bundle: string,
  build: BuildIdentity,
): VersionRow[] {
  const showVersion = (version?: string) => {
    const value = (version || "").trim();
    return value ? `v${value}` : "unknown";
  };
  const showIdentity = (value: string) => {
    const normalized = value.trim();
    return normalized && normalized !== "unknown" ? normalized : "unknown";
  };

  const branch = showIdentity(build.branch);
  const sha = showIdentity(build.sha);
  const hasBranch = branch !== "unknown";
  const hasSha = sha !== "unknown";
  const shortSha = hasSha ? sha.slice(0, 8) : "unknown";
  const buildValue = hasSha
    ? hasBranch
      ? `${branch} @ ${shortSha}`
      : shortSha
    : hasBranch
      ? `${branch} (commit unavailable)`
      : "Unavailable";
  const buildTitle = hasSha
    ? hasBranch
      ? `${branch} @ ${sha}`
      : sha
    : hasBranch
      ? `${branch} (commit unavailable)`
      : "Build identity unavailable";

  return [
    { label: "Kollab", value: showVersion(config?.kollab_version) },
    { label: "Engine", value: showVersion(config?.engine_version) },
    { label: "Web UI Server", value: showVersion(config?.server_version) },
    { label: "Frontend Bundle", value: showVersion(bundle) },
    {
      label: "Build",
      value: buildValue,
      title: buildTitle,
    },
  ];
}
