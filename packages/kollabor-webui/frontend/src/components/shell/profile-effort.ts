/**
 * Which provider a profile's requests reach, as far as the Profiles editor's effort
 * picker needs it. Keep in step with kollabor_ai's detect_provider_from_profile:
 * the key decides first, then the endpoint. The endpoint counts by its host; the
 * server still matches it as text (#848).
 */

/** True when the endpoint's host is `domain` or a subdomain of it. No scheme reads as https. */
function hostIs(baseUrl: string, domain: string): boolean {
  if (!baseUrl) return false;
  try {
    const host = new URL(baseUrl.includes("://") ? baseUrl : `https://${baseUrl}`).hostname.toLowerCase();
    return host === domain || host.endsWith(`.${domain}`);
  } catch {
    return false;
  }
}

/** True when an "auto" profile with this key and endpoint resolves to Anthropic. */
export function autoResolvesToAnthropic(apiKey: string, baseUrl: string): boolean {
  if (apiKey.startsWith("sk-ant-")) return true;
  if (hostIs(baseUrl, "azure.com") || apiKey.startsWith("sk-")) return false;
  if (hostIs(baseUrl, "generativelanguage.googleapis.com")) return false;
  if (hostIs(baseUrl, "openrouter.ai")) return false;
  return hostIs(baseUrl, "anthropic.com");
}

/**
 * True when requests go to Anthropic, so the editor leaves out `none` and `minimal`.
 * A named provider decides. "auto" follows a typed key or endpoint. With neither
 * typed, a saved profile uses the provider the server resolved (its key is never
 * sent back), and a new profile follows the endpoint alone.
 */
export function sendsAnthropic(
  draft: { provider?: string; api_key?: string; base_url?: string },
  savedEffortProvider: string,
): boolean {
  const provider = (draft.provider || "").toLowerCase();
  if (provider !== "auto") return provider === "anthropic";
  const apiKey = draft.api_key || "";
  const baseUrl = draft.base_url || "";
  if (apiKey) return autoResolvesToAnthropic(apiKey, baseUrl);
  if (savedEffortProvider) return savedEffortProvider === "anthropic";
  return autoResolvesToAnthropic("", baseUrl);
}
