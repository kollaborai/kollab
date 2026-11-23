import { createRoot } from "react-dom/client";
import { EngineApi, type WebMessageQueueSnapshot } from "@/api";
import { WebQueuePanelMount } from "@/components/web-queue/WebQueuePanelMount";
import "@/styles.css";

document.documentElement.classList.add("dark");

type RequestRecord = { url: string; method: string; authorization: string | null; body: string | null };
type MountProbe = {
  requests: () => RequestRecord[];
  sent: () => boolean;
  removed: () => boolean;
  failNextRead: () => void;
};
type ProbeWindow = Window & { __queuePanelMountProbe?: MountProbe };
declare global {
  interface Window {
    __queuePanelMountProbe?: MountProbe;
  }
}

let sent = false;
let removed = false;
let failRead = false;
let revision = 0;
const requests: RequestRecord[] = [];
const firstBatch: WebMessageQueueSnapshot["batches"][number] = {
  batch_id: "batch-first",
  sequence: 1,
  status: "queued",
  request_ids: ["request-first"],
  claim_id: null,
  items: [{
    request_id: "request-first",
    status: "queued",
    attempt_id: null,
    text_preview: "First queued message",
    attachment_labels: [],
  }],
};
const secondBatch: WebMessageQueueSnapshot["batches"][number] = {
  batch_id: "batch-second",
  sequence: 2,
  status: "queued",
  request_ids: ["request-second"],
  claim_id: null,
  items: [{
    request_id: "request-second",
    status: "queued",
    attempt_id: null,
    text_preview: "Second queued message",
    attachment_labels: ["plan.png"],
  }],
};

const api = new EngineApi("http://queue-engine.test");
api.setToken("queue-panel-test-token");
window.fetch = async (input: string | URL | Request, init?: RequestInit) => {
  const url = String(input);
  if (url === "/api/config") return Response.json({});
  const method = init?.method ?? "GET";
  requests.push({
    url,
    method,
    authorization: new Headers(init?.headers).get("Authorization"),
    body: typeof init?.body === "string" ? init.body : null,
  });
  if (url.endsWith("/ids")) {
    return Response.json({
      scope_id: "queue-scope",
      incarnation: "queue-incarnation",
      operation_ids: ["remove-operation"],
    });
  }
  if (method === "DELETE") {
    removed = true;
    return Response.json({ status: "removed", request_ids: ["request-second"] });
  }
  if (url.includes("/send-now?")) {
    sent = true;
    return Response.json({ status: "admitted", accepted: true, request_ids: ["request-first"] });
  }
  if (method === "GET") {
    if (failRead) {
      failRead = false;
      return Response.json({ detail: "temporary read failure" }, { status: 503 });
    }
    const batches = [
      sent
        ? { ...firstBatch, status: "dispatching" as const, claim_id: "claim-first",
            items: [{ ...firstBatch.items[0]!, status: "dispatching" as const, attempt_id: "attempt-first" }] }
        : firstBatch,
      ...(!removed ? [secondBatch] : []),
    ];
    return Response.json({
      scope_id: "queue-scope",
      incarnation: "queue-incarnation",
      revision: ++revision,
      batches,
      dispatch_available: true,
    } satisfies WebMessageQueueSnapshot);
  }
  return Response.json({ detail: "Unexpected request" }, { status: 404 });
};

(window as ProbeWindow).__queuePanelMountProbe = {
  requests: () => [...requests],
  sent: () => sent,
  removed: () => removed,
  failNextRead: () => { failRead = true; },
};

createRoot(document.getElementById("root")!).render(
  <main className="bg-background min-h-screen p-4 text-foreground">
    <div className="mx-auto w-full max-w-2xl">
      <WebQueuePanelMount
        api={api}
        sessionId="session/one"
        conversationUid="conversation-a"
        busy
      />
    </div>
  </main>,
);
