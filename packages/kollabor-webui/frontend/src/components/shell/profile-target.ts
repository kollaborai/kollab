export type ProfileTarget = {
  provider?: string;
  base_url?: string;
  acp_adapter?: string;
};

/** Display the effective destination without exposing URL credentials or query data. */
export function profileTargetLabel(profile: ProfileTarget): string | null {
  const provider = profile.provider?.trim();
  if (provider?.toLowerCase() === "acp") {
    const adapter = profile.acp_adapter?.trim();
    return adapter ? `ACP adapter: ${adapter}` : "ACP command missing";
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


export function shouldSubmitBaseUrlUpdate(
  originalUrl: string,
  editedUrl: string,
  containsHiddenData: boolean,
  replaceHiddenData: boolean,
): boolean {
  return (
    !containsHiddenData || editedUrl !== originalUrl || replaceHiddenData
  );
}
