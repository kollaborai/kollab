/** A `confirm` action fires on the second press of the same key; the first only arms it. */
export function confirmStep(armed: string | null, key: string, confirm: unknown): "arm" | "fire" {
  return confirm && armed !== key ? "arm" : "fire";
}
