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
      is_error?: boolean | null;
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
      kind?: string | null;
      thread_id?: string | null;
      reply_to?: string | null;
      is_direct?: boolean | null;
      presence?: "online" | "offline" | null;
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

export type InputInfo = { id: string; state: InputState; turn_id: string | null; outcome?: Outcome | null };
export type TurnInfo = { turn_id: string; state: TurnState; input_ids: string[]; origin?: { kind: "goal"; goal_id: string; attempt_id: string } };

type StreamEvent<T extends string, D> = { seq: number; conversation_uid: string; type: T; data: D };

export type ChatStreamEvent =
  // `older` is the cursor for the page before `rows`, or null when there is none.
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
  /** Client-private capability for polling and cancelling this goal command. Never part of a stream row. */
  goal_reply_id?: string;
};

/** What the chat store gives the rest of the chat: lane 3 builds it, lane 4 builds on it. */
export interface ChatStore {
  readonly rows: readonly ChatRow[];
  readonly inputs: Readonly<Record<string, InputInfo>>;
  readonly running: boolean;
  /** History paging: loading while loadOlder runs, failed after it fails, complete when nothing is older. */
  readonly olderStatus: "idle" | "loading" | "failed" | "complete";
  send(input: InputRequest): Promise<void>;
  /** Asks the daemon to start a queued input now. Local rows and inputs change only on the stream's input.state events. */
  sendNow(inputId: string): Promise<void>;
  cancel(inputId: string): Promise<void>;
  stop(): Promise<void>;
  loadOlder(): Promise<void>;
}

const EVENT_TYPES = new Set(["snapshot", "row.upsert", "row.append", "row.delete", "replace", "input.state", "turn.state"]);
const ROLES = new Set(["user", "assistant", "system"]);
const INPUT_STATES = new Set(["queued", "started", "joined", "ended"]);
const OUTCOMES = new Set(["completed", "failed", "cancelled"]);
const KINDS = new Set<string>(ROW_KINDS);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isId = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const isCount = (value: unknown): boolean => Number.isInteger(value) && (value as number) >= 0;
const isStr = (value: unknown): value is string => typeof value === "string";
const isBool = (value: unknown): value is boolean => typeof value === "boolean";
const has = (record: Record<string, unknown>, key: string): boolean => Object.prototype.hasOwnProperty.call(record, key);
// An optional field: absent, null, or passing its check.
const optional = (record: Record<string, unknown>, key: string, check: (value: unknown) => boolean): boolean =>
  record[key] === undefined || record[key] === null || check(record[key]);

// Each ChatPart variant, the same rules as `_part_problems` on the Python side.
const isPart = (value: unknown): boolean => {
  if (!isRecord(value)) return false;
  switch (value.type) {
    case "text":
    case "reasoning":
      return isStr(value.text);
    case "tool-call":
      return isId(value.tool_call_id) && isStr(value.tool_name) && has(value, "args") && optional(value, "is_error", isBool);
    case "image":
      return isId(value.url);
    case "prompt":
      return isId(value.prompt_id) && has(value, "data");
    case "hub-note":
      return isId(value.from) && isStr(value.to) && isStr(value.text) && isBool(value.observed) &&
        optional(value, "kind", isStr) && optional(value, "thread_id", isStr) && optional(value, "reply_to", isStr) &&
        optional(value, "is_direct", isBool) && optional(value, "presence", (p) => p === "online" || p === "offline");
    default:
      return false;
  }
};

// Every ChatRow field, the same rules as `_row_problems` on the Python side.
const isRow = (value: unknown): boolean =>
  isRecord(value) && isId(value.id) && isCount(value.seq) && KINDS.has(value.kind as string) &&
  ROLES.has(value.role as string) && (value.turn_id === null || isId(value.turn_id)) &&
  typeof value.status === "string" && Array.isArray(value.parts) && value.parts.every(isPart) &&
  typeof value.created_at === "string";

const isTurnInfo = (value: unknown): boolean =>
  isRecord(value) && isId(value.turn_id) && (value.state === "running" || value.state === "ended") &&
  Array.isArray(value.input_ids) && value.input_ids.every(isId) &&
  (!has(value, "origin") || (isRecord(value.origin) && value.origin.kind === "goal" &&
    isId(value.origin.goal_id) && isId(value.origin.attempt_id)));

const isInputInfo = (value: unknown): boolean => {
  if (!isRecord(value) || !isId(value.id) || !INPUT_STATES.has(value.state as string)) return false;
  if (!has(value, "turn_id") || (value.turn_id !== null && !isId(value.turn_id))) return false;
  if ((value.state === "started" || value.state === "joined") && value.turn_id === null) return false;
  if (value.state === "ended") return OUTCOMES.has(value.outcome as string);
  return value.outcome === undefined || value.outcome === null; // only an ended input has an outcome
};

/** Shape check for one event, the same rules as `event_problems` on the Python side. */
export function isChatStreamEvent(value: unknown): value is ChatStreamEvent {
  if (!isRecord(value) || !isCount(value.seq)) return false;
  if (!isId(value.conversation_uid) || !EVENT_TYPES.has(value.type as string) || !isRecord(value.data)) return false;
  const data = value.data;
  switch (value.type) {
    case "snapshot":
      return Array.isArray(data.rows) && data.rows.every(isRow) && isCount(data.next_seq) &&
        (data.older === null || isId(data.older)) &&
        Array.isArray(data.turns) && data.turns.every(isTurnInfo) && Array.isArray(data.inputs) && data.inputs.every(isInputInfo);
    case "replace":
      return Array.isArray(data.rows) && data.rows.every(isRow);
    case "row.upsert":
      return isRow(data);
    case "row.append":
      return isId(data.id) && isCount(data.part) && typeof data.text === "string";
    case "row.delete":
      return isId(data.id);
    case "input.state":
      return isInputInfo(data);
    default:
      return isTurnInfo(data);
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
