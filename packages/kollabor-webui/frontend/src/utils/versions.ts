import type { EngineConfig } from "@/api";

export type VersionRow = { label: string; value: string };

/**
 * Rows for the settings Versions block. `bundle` is the frontend's own build
 * version (__APP_VERSION__); the rest come from GET /api/config. A version the
 * server could not read shows "unknown" rather than a blank.
 */
export function versionRows(config: EngineConfig | null, bundle: string): VersionRow[] {
  const show = (version?: string) => {
    const value = (version || "").trim();
    return value ? `v${value}` : "unknown";
  };
  return [
    { label: "Kollab", value: show(config?.kollab_version) },
    { label: "Engine", value: show(config?.engine_version) },
    { label: "Web UI Server", value: show(config?.server_version) },
    { label: "Frontend Bundle", value: show(bundle) },
  ];
}
