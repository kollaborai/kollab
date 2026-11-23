import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  QueuePanel,
  type WebQueueBatchSnapshot,
  type WebQueueItemSnapshot,
  type WebQueueSnapshot,
} from "@/components/web-queue/QueuePanel";

const item = (
  request_id: string,
  text_preview: string,
  status: WebQueueItemSnapshot["status"] = "queued",
  attachment_labels: string[] = [],
): WebQueueItemSnapshot => ({
  request_id,
  status,
  attempt_id: status === "queued" ? null : `attempt-${request_id}`,
  text_preview,
  attachment_labels,
});

const batch = (
  batch_id: string,
  sequence: number,
  request_ids: string[],
  items: WebQueueItemSnapshot[],
  status: WebQueueBatchSnapshot["status"] = "queued",
): WebQueueBatchSnapshot => ({
  batch_id,
  sequence,
  status,
  request_ids,
  claim_id: status === "queued" ? null : `claim-${batch_id}`,
  items,
});

const snapshot = (batches: WebQueueBatchSnapshot[]): WebQueueSnapshot => ({
  scope_id: "scope-test",
  incarnation: "incarnation-test",
  revision: 4,
  batches,
});

const render = (value: WebQueueSnapshot | null, options?: { busy?: boolean; dispatchAvailable?: boolean; stale?: boolean; refreshError?: boolean }) =>
  renderToStaticMarkup(
    <QueuePanel
      snapshot={value}
      dispatchAvailable={options?.dispatchAvailable ?? true}
      stale={options?.stale ?? false}
      refreshError={options?.refreshError ?? false}
      busy={options?.busy ?? true}
      onRefresh={vi.fn()}
      onRemoveItem={vi.fn()}
      onSendNow={vi.fn()}
    />,
  );

describe("QueuePanel", () => {
  it("renders nothing when there are no queued request IDs", () => {
    expect(render(snapshot([]))).toBe("");
    expect(render(null)).toBe("");
  });

  it("shows text previews and attachment basenames in daemon FIFO order", () => {
    const html = render(snapshot([
      batch("later", 9, ["r3"], [item("r3", "Third batch message")]),
      batch("first", 2, ["r1", "r2"], [
        item("r1", "First in this batch", "queued", ["/scratch/private/retry-trace.txt"]),
        item("r2", "Second in this batch"),
      ]),
    ]));
    const firstBatch = html.indexOf("First in this batch");
    const secondBatchItem = html.indexOf("Second in this batch");
    const laterBatch = html.indexOf("Third batch message");
    expect(firstBatch).toBeGreaterThan(-1);
    expect(firstBatch).toBeLessThan(secondBatchItem);
    expect(secondBatchItem).toBeLessThan(laterBatch);
    expect(html).toContain("retry-trace.txt");
    expect(html).not.toContain("/scratch/private");
    expect(html).toContain("aria-label=\"Send 2 queued messages now\"");
    expect(html).toContain("aria-label=\"Remove queued message 1\"");
    expect(html).toContain("aria-label=\"Remove queued message 2\"");
    expect(html).toMatch(/aria-live="polite"/);
  });

  it("never renders raw payload, attachment refs, claim IDs, attempt IDs, or request IDs", () => {
    const unsafe = {
      ...item("request-id-private", "Visible preview"),
      payload_b64: "RAW_ATTACHMENT_BYTES_SECRET",
      attachment_refs: ["opaque-staged-ref-secret"],
    } as unknown as WebQueueItemSnapshot;
    const html = render(snapshot([
      batch("batch-id-private", 1, ["request-id-private"], [unsafe]),
    ]));
    for (const value of [
      "RAW_ATTACHMENT_BYTES_SECRET",
      "opaque-staged-ref-secret",
      "batch-id-private",
      "claim-batch-id-private",
      "attempt-request-id-private",
      "request-id-private",
    ]) {
      expect(html).not.toContain(value);
    }
    expect(html).toContain("Visible preview");
  });

  it("bounds long text previews without splitting Unicode code points", () => {
    const text = `${"a".repeat(239)}🌱${"b".repeat(20)}`;
    const html = render(snapshot([batch("b", 1, ["r"], [item("r", text)])]));
    expect(html).toContain(`${"a".repeat(239)}🌱…`);
    expect(html).not.toContain("b".repeat(20));
  });

  it("keeps removal available for queued items while idle and hides Send Now", () => {
    const html = render(snapshot([batch("b", 1, ["r"], [item("r", "Queued text")])]), { busy: false });
    expect(html).toContain("Queued for the next turn.");
    expect(html).not.toContain("Send Now");
    expect(html).toContain("Remove");
    expect(html).not.toMatch(/<button[^>]* disabled=""/);
  });

  it("disables Send Now when dispatch is unavailable and tells the truth", () => {
    const html = render(snapshot([batch("b", 1, ["r"], [item("r", "Queued text")])]), {
      busy: true,
      dispatchAvailable: false,
    });
    expect(html).toContain("Queue delivery is unavailable right now.");
    expect(html).toContain("aria-label=\"Send Now unavailable: queue delivery is unavailable\"");
    expect(html).toMatch(/<button[^>]*disabled=""/);
    expect(html).toContain("Remove");
  });

  it("labels stale rows as last known and disables queue mutation until refresh", () => {
    const html = render(snapshot([batch("b", 1, ["r"], [item("r", "Last known message")])]), {
      stale: true,
      refreshError: true,
    });
    expect(html).toContain("Queue status is out of date. Showing last known messages");
    expect(html).toContain("Last known message");
    expect(html).toContain('data-stale="true"');
    expect(html).toContain("Retry");
    expect(html).toContain('aria-label="Send Now unavailable: queue status is out of date"');
    expect((html.match(/<button[^>]*disabled=""/g) ?? []).length).toBe(2);

    const empty = render(null, { stale: true, refreshError: true });
    expect(empty).toContain("Could not load queued messages.");
    expect(empty).toContain("Retry");
  });

  it("holds dispatching and in-doubt items and explains ambiguous delivery", () => {
    for (const status of ["dispatching", "in_doubt"] as const) {
      const html = render(snapshot([
        batch("b", 1, ["r"], [item("r", "Held text", status)], status),
      ]));
      expect(html).toContain(status === "dispatching" ? "Sending" : "Delivery status unknown");
      if (status === "in_doubt") expect(html).toContain("held to avoid sending twice");
      expect(html).toContain(status === "dispatching"
        ? "aria-label=\"Send Now unavailable: earlier message is already being sent\""
        : "aria-label=\"Send Now unavailable: earlier message delivery status is unknown\"");
      expect(html).toContain("aria-label=\"Remove queued message 1\"");
      expect((html.match(/<button[^>]*disabled=""/g) ?? []).length).toBe(2);
    }
  });

  it("fails closed when a batch lists a request without an item snapshot", () => {
    const html = render(snapshot([
      batch("b", 1, ["r", "missing"], [item("r", "Visible preview")]),
    ]));
    expect(html).toContain("Some queued message details are unavailable.");
    expect(html).toContain("1 message is unavailable.");
    expect(html).toContain("aria-label=\"Send Now unavailable: refresh the queue before sending\"");
    expect(html).toContain("aria-label=\"Remove queued message 1\"");
    expect(html).toContain("aria-label=\"Remove queued message 2\"");
    expect((html.match(/<button[^>]*disabled=""/g) ?? []).length).toBe(3);
  });

  it("offers one Send Now control for only the earliest queued batch", () => {
    const html = render(snapshot([
      batch("later", 2, ["r2"], [item("r2", "Later batch")]),
      batch("first", 1, ["r1"], [item("r1", "First batch")]),
    ]));
    expect(html.match(/aria-label="Send 1 queued message now"/g) ?? []).toHaveLength(1);
    expect(html.indexOf("Send 1 queued message now")).toBeLessThan(html.indexOf("First batch"));
    expect(html.indexOf("First batch")).toBeLessThan(html.indexOf("Later batch"));
  });

  it("renders batches as one compact flat message list", () => {
    const html = render(snapshot([
      batch("first", 1, ["r1", "r2"], [item("r1", "First message"), item("r2", "Second message")]),
      batch("later", 2, ["r3"], [item("r3", "Later message")]),
    ]));
    expect(html).not.toContain('data-slot="web-queue-batch"');
    expect(html.match(/data-slot="web-queue-item"/g) ?? []).toHaveLength(3);
    expect(html).not.toMatch(/<span[^>]*>\d+ messages<\/span>/);
    expect(html).toContain('aria-label="Send 2 queued messages now"');
    expect(html.indexOf("First message")).toBeLessThan(html.indexOf("Second message"));
    expect(html.indexOf("Second message")).toBeLessThan(html.indexOf("Later message"));
  });

  it("does not bypass an ambiguous first batch to send a later batch", () => {
    const html = render(snapshot([
      batch("first", 1, ["r1"], [item("r1", "Uncertain first batch", "in_doubt")], "in_doubt"),
      batch("later", 2, ["r2"], [item("r2", "Held later batch")]),
    ]));
    expect(html).toContain('aria-label="Send Now unavailable: earlier message delivery status is unknown"');
    expect(html).not.toContain('aria-label="Send 1 queued message now"');
    expect(html).toMatch(/<button[^>]*aria-label="Send Now unavailable: earlier message delivery status is unknown"[^>]*disabled=""/);
    expect(html).toContain("held to avoid sending twice");
    expect(html.match(/data-slot="web-queue-status-dot"/g) ?? []).toHaveLength(2);
    expect(html).toContain('data-status="in_doubt"');
  });

  it("explains an earlier dispatching batch in the disabled Send Now name", () => {
    const html = render(snapshot([
      batch("first", 1, ["r1"], [item("r1", "Sending first batch", "dispatching")], "dispatching"),
      batch("later", 2, ["r2"], [item("r2", "Later queued batch")]),
    ]));
    expect(html).toContain('aria-label="Send Now unavailable: earlier message is already being sent"');
    expect(html).not.toContain('aria-label="Send 1 queued message now"');
  });
});
