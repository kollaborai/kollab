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
  const shortSha = sha === "unknown" ? sha : sha.slice(0, 8);

  return [
    { label: "Kollab", value: showVersion(config?.kollab_version) },
    { label: "Engine", value: showVersion(config?.engine_version) },
    { label: "Web UI Server", value: showVersion(config?.server_version) },
    { label: "Frontend Bundle", value: showVersion(bundle) },
    {
      label: "Build",
      value: `${branch} @ ${shortSha}`,
      title: `${branch} @ ${sha}`,
    },
  ];
}
