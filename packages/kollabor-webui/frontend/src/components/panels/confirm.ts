/** A `confirm` action fires on the second press of the same key; the first only arms it. */
export function confirmStep(armed: string | null, key: string, confirm: unknown): "arm" | "fire" {
  return confirm && armed !== key ? "arm" : "fire";
}

/** The halt panels' server refuses halt, resume and revoke acts that do not say a person confirmed them. */
export function confirmedPayload(
  haltPanel: boolean,
  confirm: unknown,
  payload: Record<string, unknown> = {},
): Record<string, unknown> {
  return haltPanel && confirm ? { ...payload, confirmed: true } : payload;
}
