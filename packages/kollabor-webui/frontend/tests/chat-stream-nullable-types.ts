// Accepted explicit nulls must also be representable in the public types.
const nullableTool: import("../src/chat-stream").ChatPart = { type: "tool-call", tool_call_id: "c", tool_name: "x", args: null, is_error: null };
const nullableHub: import("../src/chat-stream").ChatPart = { type: "hub-note", from: "a", to: "b", text: "x", observed: true, kind: null, thread_id: null, reply_to: null, is_direct: null, presence: null };
const nullableInput: import("../src/chat-stream").InputInfo = { id: "a", state: "queued", turn_id: null, outcome: null };
void [nullableTool, nullableHub, nullableInput];
