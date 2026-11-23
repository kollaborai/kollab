import { createRoot } from "react-dom/client";
import { ToolFallback } from "../src/components/assistant-ui/tool-fallback";
import "../src/styles.css";

const ids = [
  "0123456789abcdef0123456789abcdef",
  "abcdef0123456789abcdef0123456789",
  "1234567890abcdef1234567890abcdef",
  "fedcba9876543210fedcba9876543210",
];
const calls = [
  {
    name: "mcp__kollab-hub__hub_msg",
    args: { to: "Sapphire", message: "The review is ready.", thread_id: ids[0] },
  },
  {
    name: "mcp__kollab-hub__hub_reply",
    args: { to: "Sapphire", message: "Here is the answer.", thread_id: ids[1], reply_to: ids[2] },
  },
  {
    name: "mcp__kollab-hub__hub_broadcast",
    args: { message: "Build is green.", thread_id: ids[3] },
  },
  {
    name: "mcp__calendar__lookup",
    args: {
      query: "events",
      thread_id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      reply_to: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    },
  },
];

createRoot(document.getElementById("root")!).render(
  <main className="min-h-screen bg-background p-6 text-foreground sm:p-10">
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-3">
      {calls.map((call) => (
        <ToolFallback
          key={call.name}
          toolName="harness_tool"
          argsText={JSON.stringify(call.args)}
          result={{
            success: true,
            output: "Delivered",
            metadata: {
              acp: {
                title: call.name,
                kind: "other",
                status: "completed",
              },
            },
          }}
          status={{ type: "complete", reason: "stop" }}
        />
      ))}
    </div>
  </main>,
);
