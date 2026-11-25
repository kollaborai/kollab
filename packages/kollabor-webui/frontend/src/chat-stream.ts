// Chat stream contract: the web UI's copy of
// packages/kollabor-events/src/kollabor_events/chat_stream.py.
// Spec: docs/specs/webui-chat-single-stream.md (#1451). Both sides test
// against tests/fixtures/chat-stream/.

export const ROW_KINDS = ["input", "reply", "tool", "note", "prompt", "marker"] as const;
export type RowKind = (typeof ROW_KINDS)[number];
export type Role = "user" | "assistant" | "system";
export type InputState = "queued" | "started" | "joined" | "ended";
export type Outcome = "completed" | "failed" | "cancelled";
export type TurnState = "running" | "ended";

export type ChatPart =
  | { type: "text"; text: string }
  | { type: "reasoning"; text: string }
  | {
      type: "tool-call";
      tool_call_id: string;
      tool_name: string;
      args: unknown;
      result?: unknown;
      is_error?: boolean;
    }
  | { type: "image"; url: string }
  | { type: "prompt"; prompt_id: string; data: unknown }
  // A hub message, built from the daemon's structured hub fields (never parsed from text).
  // In a note row when it starts no turn; in an input row (id = hub message id) when it does.
  | {
      type: "hub-note";
      from: string;
      to: string; // "" for a broadcast
      text: string;
      observed: boolean;
      kind?: string;
      thread_id?: string;
      reply_to?: string;
      is_direct?: boolean;
      presence?: "online" | "offline";
    };

export type ChatRow = {
  id: string;
  seq: number;
  kind: RowKind;
  role: Role;
  turn_id: string | null;
  status: string;
  parts: ChatPart[];
  created_at: string;
};

export type InputInfo = { id: string; state: InputState; turn_id: string | null; outcome?: Outcome };
export type TurnInfo = { turn_id: string; state: TurnState; input_ids: string[] };

type StreamEvent<T extends string, D> = { seq: number; conversation_uid: string; type: T; data: D };

export type ChatStreamEvent =
  | StreamEvent<"snapshot", { rows: ChatRow[]; next_seq: number; older: string | null; turns: TurnInfo[]; inputs: InputInfo[] }>
  | StreamEvent<"row.upsert", ChatRow>
  | StreamEvent<"row.append", { id: string; part: number; text: string }>
  | StreamEvent<"row.delete", { id: string }>
  | StreamEvent<"replace", { rows: ChatRow[] }>
  | StreamEvent<"input.state", InputInfo>
  | StreamEvent<"turn.state", TurnInfo>;

/** The body of `POST /sessions/{id}/inputs`. */
export type InputRequest = {
  id: string;
  text: string;
  attachments?: readonly { name: string; content_type: string; data_url: string }[];
};

/** What the chat store gives the rest of the chat: lane 3 builds it, lane 4 builds on it. */
export interface ChatStore {
  readonly rows: readonly ChatRow[];
  readonly inputs: Readonly<Record<string, InputInfo>>;
  readonly running: boolean;
  send(input: InputRequest): Promise<void>;
  cancel(inputId: string): Promise<void>;
  stop(): Promise<void>;
  loadOlder(): Promise<void>;
}

const EVENT_TYPES = new Set(["snapshot", "row.upsert", "row.append", "row.delete", "replace", "input.state", "turn.state"]);
const INPUT_STATES = new Set(["queued", "started", "joined", "ended"]);
const OUTCOMES = new Set(["completed", "failed", "cancelled"]);
const KINDS = new Set<string>(ROW_KINDS);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isId = (value: unknown): value is string => typeof value === "string" && value.length > 0;

const isRow = (value: unknown): boolean =>
  isRecord(value) && isId(value.id) && typeof value.seq === "number" && KINDS.has(value.kind as string) &&
  typeof value.role === "string" && Array.isArray(value.parts);

const isInputInfo = (value: unknown): boolean => {
  if (!isRecord(value) || !isId(value.id) || !INPUT_STATES.has(value.state as string)) return false;
  if ((value.state === "started" || value.state === "joined") && !isId(value.turn_id)) return false;
  return value.state !== "ended" || OUTCOMES.has(value.outcome as string);
};

/** Shape check for one event, the same rules as `event_problems` on the Python side. */
export function isChatStreamEvent(value: unknown): value is ChatStreamEvent {
  if (!isRecord(value) || !Number.isInteger(value.seq) || (value.seq as number) < 0) return false;
  if (!isId(value.conversation_uid) || !EVENT_TYPES.has(value.type as string) || !isRecord(value.data)) return false;
  const data = value.data;
  switch (value.type) {
    case "snapshot":
      return Array.isArray(data.rows) && data.rows.every(isRow) && Number.isInteger(data.next_seq) &&
        Array.isArray(data.turns) && Array.isArray(data.inputs) && data.inputs.every(isInputInfo);
    case "replace":
      return Array.isArray(data.rows) && data.rows.every(isRow);
    case "row.upsert":
      return isRow(data);
    case "row.append":
      return isId(data.id) && Number.isInteger(data.part) && typeof data.text === "string";
    case "row.delete":
      return isId(data.id);
    case "input.state":
      return isInputInfo(data);
    default:
      return isId(data.turn_id) && (data.state === "running" || data.state === "ended") && Array.isArray(data.input_ids);
  }
}

/**
 * A new input id: the client's prefix and 24 random hex digits. Uses
 * `getRandomValues`, which works on plain-http LAN pages, where
 * `crypto.randomUUID` does not exist.
 */
export function newInputId(clientPrefix: string): string {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return `${clientPrefix}-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}
