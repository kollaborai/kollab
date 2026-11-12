export type PhoneLearnMediaMap = Readonly<Record<string, string>>;

export const PHONE_LEARN_PATH_PREFIX = "/relay/v1/phone/";

/** Read the reduced-motion preference; false where the environment has no matchMedia. */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Keep Learn's local assets inside the signed PhoneLink app bundle. */
export function resolveLearnMediaSource(
  source: string,
  pathname: string,
  phoneMedia: PhoneLearnMediaMap,
): string | null {
  if (!pathname.startsWith(PHONE_LEARN_PATH_PREFIX)) return source;
  return phoneMedia[source] ?? null;
}
