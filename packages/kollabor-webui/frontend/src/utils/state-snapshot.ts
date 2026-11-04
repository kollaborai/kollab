/** Keep the current state reference when a poll returns the same JSON snapshot. */
export function reuseIfEqual<T>(current: T, next: T): T {
  return JSON.stringify(current) === JSON.stringify(next) ? current : next;
}
