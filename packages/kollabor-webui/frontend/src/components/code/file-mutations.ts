import { codeFetch } from "@/lib/code-fetch";

/** Perform one tree mutation and surface the daemon's refusal. */
export async function mutateFileTree(action: "create" | "rename" | "delete", body: Record<string, unknown>): Promise<void> {
  const response = await codeFetch(`/api/fs/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.ok) return;
  const raw: unknown = await response.json().catch(() => null);
  const error = raw && typeof raw === "object" && "error" in raw ? raw.error : null;
  const message = error && typeof error === "object" && "message" in error ? error.message : null;
  throw new Error(typeof message === "string" && message ? message : `Could not ${action} the path.`);
}
