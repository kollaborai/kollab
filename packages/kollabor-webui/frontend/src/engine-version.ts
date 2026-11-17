type BuildIdentityStatus = "same" | "mismatch" | "unknown";

const VERSION_PATTERN = /^v?(\d+\.\d+\.\d+(?:(?:-(?:alpha|beta|rc)\.?[0-9a-z.-]+)|(?:a|b|rc)\d+|\.(?:post|dev)\.?\d+)?)$/i;
const BUILD_SHA_PATTERN = /^(?:[0-9a-f]{8}|[0-9a-f]{40}|[0-9a-f]{64})$/i;

function parseVersion(value: string): { release: string; build: string | null } | null {
  const [rawRelease, ...localParts] = value.trim().split("+");
  const release = rawRelease.toLowerCase();
  if (!VERSION_PATTERN.test(release) || localParts.length > 1) return null;
  const localBuild = localParts[0]?.toLowerCase() ?? "";
  return {
    release: release.replace(/^v/, ""),
    build: BUILD_SHA_PATTERN.test(localBuild) ? localBuild : null,
  };
}

function validBuildSha(value: string): string | null {
  const sha = value.trim().toLowerCase();
  return BUILD_SHA_PATTERN.test(sha) ? sha : null;
}

function sameBuild(engine: string, bundle: string): boolean {
  return engine.length <= bundle.length ? bundle.startsWith(engine) : engine.startsWith(bundle);
}

/** Compare engine and bundle release/build identities without guessing when evidence is missing. */
export function compareBuildIdentity(
  engineVersion: string,
  bundleVersion: string,
  bundleBuildSha: string,
): BuildIdentityStatus {
  const engine = parseVersion(engineVersion);
  const bundle = parseVersion(bundleVersion);
  if (!engine || !bundle) return "unknown";
  if (engine.release !== bundle.release) return "mismatch";

  const engineBuild = engine.build;
  const bundleBuild = validBuildSha(bundleBuildSha) ?? bundle.build;
  if (!engineBuild || !bundleBuild) return "unknown";
  return sameBuild(engineBuild, bundleBuild) ? "same" : "mismatch";
}

/** Compare valid release portions for callers that do not have build provenance. */
export function sameVersionBase(engine: string, bundle: string): boolean {
  const parsedEngine = parseVersion(engine);
  const parsedBundle = parseVersion(bundle);
  return Boolean(parsedEngine && parsedBundle && parsedEngine.release === parsedBundle.release);
}
