/**
 * Keeps a stale sessions-list response from overwriting a local Apply.
 *
 * A list request started before an Apply can finish after it and carry the
 * old effort. Writing that response would put the Session tab's field back.
 */
export function createSessionRefreshGate() {
  let applies = 0;
  return {
    /** Call when a list request starts. The returned check is true while no Apply has landed since. */
    startList(): () => boolean {
      const seen = applies;
      return () => seen === applies;
    },
    /** Call when an Apply succeeds. Every list started before this is stale. */
    recordApply(): void {
      applies += 1;
    },
  };
}
