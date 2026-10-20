export type ProfileTarget = {
  provider?: string;
  base_url?: string;
  command?: string[];
};

/** Display the effective destination without exposing URL credentials or query data. */
export function profileTargetLabel(profile: ProfileTarget): string | null {
  const provider = profile.provider?.trim();
  if (provider?.toLowerCase() === "acp") {
    const command = profile.command?.find((part) => part.trim());
    if (!command) return "ACP command missing";
    const executable = command.replaceAll("\\", "/").split("/").at(-1);
    return executable ? `ACP adapter: ${executable}` : "ACP adapter configured";
  }

  const baseUrl = profile.base_url?.trim();
  if (baseUrl) {
    try {
      const url = new URL(baseUrl);
      const path = url.pathname.replace(/\/+$/, "");
      return `Endpoint: ${url.host}${path}`;
    } catch {
      const safeUrl = baseUrl
        .replace(/(https?:\/\/)[^/@\s]+@/i, "$1")
        .split(/[?#]/, 1)[0]
        .replace(/\/+$/, "");
      return safeUrl ? `Endpoint: ${safeUrl}` : null;
    }
  }

  return provider ? `${provider} default endpoint` : null;
}

export function profileLoadWarningLines(
  invalidProfiles?: Record<string, string>,
): string[] {
  return Object.entries(invalidProfiles ?? {}).map(
    ([name, reason]) => `${name}: ${reason}`,
  );
}
