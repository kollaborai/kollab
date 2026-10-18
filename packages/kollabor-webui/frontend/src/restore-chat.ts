/**
 * The chat the page opens by itself on load: this engine's newest (an agent started
 * elsewhere opens on a click). There is none once the person has opened a chat or is
 * opening one: the restore never switches away from a pick.
 */
export function chatToRestore<T extends { external?: boolean }>(
  sessions: readonly T[],
  opened: boolean,
): T | undefined {
  if (opened) return undefined;
  return [...sessions].reverse().find((session) => !session.external);
}
