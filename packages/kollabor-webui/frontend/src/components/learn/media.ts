export type PhoneLearnMediaMap = Readonly<Record<string, string>>;

export const PHONE_LEARN_PATH_PREFIX = "/relay/v1/phone/";

/** Keep Learn's local assets inside the signed PhoneLink app bundle. */
export function resolveLearnMediaSource(
  source: string,
  pathname: string,
  phoneMedia: PhoneLearnMediaMap,
): string | null {
  if (!pathname.startsWith(PHONE_LEARN_PATH_PREFIX)) return source;
  return phoneMedia[source] ?? null;
}
