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

const KNOWN_BUILD_SHA = /^[0-9a-f]{8,64}$/i;

/**
 * Treat the runtime local dev suffix as the same build only when its commit
 * matches the SHA embedded in this frontend bundle. Unknown provenance never
 * suppresses a mismatch warning.
 */
export function runtimeVersionMatchesBuild(
  bundleVersion: string,
  engineVersion: string,
  buildSha: string,
): boolean {
  const bundle = bundleVersion.trim();
  const engine = engineVersion.trim();
  if (bundle && engine === bundle) return true;

  const sha = buildSha.trim();
  if (!bundle || !KNOWN_BUILD_SHA.test(sha)) return false;
  return engine === `${bundle}+dev.g${sha.slice(0, 8).toLowerCase()}`;
}
