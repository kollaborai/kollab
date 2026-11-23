import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "@/styles.css";
import {
  QueuePanel,
  type WebQueueSnapshot,
} from "@/components/web-queue/QueuePanel";

document.documentElement.classList.add("dark");

type TestApi = {
  setSnapshot: (snapshot: WebQueueSnapshot) => void;
  setBusy: (busy: boolean) => void;
  setDispatchAvailable: (available: boolean) => void;
  failNext: () => void;
  settleAction: () => void;
  calls: () => Array<{ kind: "remove" | "send"; id: string }>;
};

type TestWindow = Window & { __queuePanelTest?: TestApi };

const initial: WebQueueSnapshot = {
  scope_id: "fixture-scope",
  incarnation: "fixture-incarnation",
  revision: 1,
  batches: [{
    batch_id: "fixture-batch",
    sequence: 1,
    status: "queued",
    request_ids: ["fixture-request"],
    claim_id: null,
    items: [{
      request_id: "fixture-request",
      status: "queued",
      attempt_id: null,
      text_preview: "Review the queued request preview.",
      attachment_labels: [],
    }],
  }],
};

function Fixture() {
  const [snapshot, setSnapshot] = useState(initial);
  const [busy, setBusy] = useState(true);
  const [dispatchAvailable, setDispatchAvailable] = useState(true);
  const calls = useRef<Array<{ kind: "remove" | "send"; id: string }>>([]);
  const fail = useRef(false);
  const resolvePending = useRef<(() => void) | null>(null);

  const action = (kind: "remove" | "send", id: string) => {
    calls.current.push({ kind, id });
    if (fail.current) {
      fail.current = false;
      return Promise.reject(new Error("/private/path/SECRET_ATTACHMENT_REF"));
    }
    return new Promise<void>((resolve) => {
      resolvePending.current = resolve;
    });
  };

  useEffect(() => {
    const testApi: TestApi = {
      setSnapshot,
      setBusy,
      setDispatchAvailable,
      failNext: () => { fail.current = true; },
      settleAction: () => {
        resolvePending.current?.();
        resolvePending.current = null;
      },
      calls: () => [...calls.current],
    };
    (window as TestWindow).__queuePanelTest = testApi;
    return () => { delete (window as TestWindow).__queuePanelTest; };
  }, []);

  return (
    <main className="bg-background min-h-screen p-4 text-foreground">
      <div className="mx-auto w-full max-w-2xl">
        <QueuePanel
          snapshot={snapshot}
          dispatchAvailable={dispatchAvailable}
          busy={busy}
          onRemoveItem={(requestId) => action("remove", requestId)}
          onSendNow={(batchId) => action("send", batchId)}
        />
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
