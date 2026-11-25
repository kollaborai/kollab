// Queue state for the composer and the waiting rows (#1451, lane 4). Pure helpers
// over the chat store's rows and inputs; thread.tsx moves to them in lane 4e.
import type { ChatRow, InputInfo } from "./chat-stream";

export type ComposerAction = "send" | "idle" | "queue" | "stop";

/**
 * What the composer's primary button does. `hasContent` means non-blank text or
 * at least one attachment, the same test sendThroughStore makes in thread.tsx.
 * While a turn runs, content queues and an empty composer offers Stop. When no
 * turn runs, content sends and an empty composer leaves send disabled.
 */
export function composerAction({ running, hasContent }: { running: boolean; hasContent: boolean }): ComposerAction {
  if (running) return hasContent ? "queue" : "stop";
  return hasContent ? "send" : "idle";
}

/**
 * True for an input row the daemon has queued and not yet started or joined.
 * A row's id is its input's id for the life of the row (spec, Ids), so the
 * lookup needs no second key.
 */
export function isWaitingRow(row: ChatRow, inputs: Readonly<Record<string, InputInfo>>): boolean {
  return row.kind === "input" && inputs[row.id]?.state === "queued";
}

/**
 * True when Send Now can move this input into the running turn. This checks the
 * input only. The session must also accept Send Now (useSendWaitingNow in
 * thread.tsx), which the component checks.
 */
export function canSendNow(info: InputInfo | undefined): boolean {
  return info?.state === "queued";
}

/** One queued input as the queue panel lists it: its id, and the text its row shows (empty when no row has arrived). */
export type QueuedInput = { id: string; text: string };

/**
 * The queued inputs, in queue order. The rows are in the order the daemon admitted the inputs, so the list
 * follows them. A queued input with no row in the window comes after the rows, in the order the store holds it.
 */
export function queuedInputs(rows: readonly ChatRow[], inputs: Readonly<Record<string, InputInfo>>): QueuedInput[] {
  const listed: QueuedInput[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    if (!isWaitingRow(row, inputs)) continue;
    listed.push({ id: row.id, text: rowText(row) });
    seen.add(row.id);
  }
  for (const input of Object.values(inputs)) {
    if (input.state === "queued" && !seen.has(input.id)) listed.push({ id: input.id, text: "" });
  }
  return listed;
}

/** The text a row shows: its text parts and a hub message's text. Attachments and the other parts carry none. */
function rowText(row: ChatRow): string {
  return row.parts.map((part) => (part.type === "text" || part.type === "hub-note" ? part.text : "")).join("");
}
