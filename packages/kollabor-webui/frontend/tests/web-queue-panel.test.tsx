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

const render = (value: WebQueueSnapshot | null, options?: { busy?: boolean; dispatchAvailable?: boolean }) =>
  renderToStaticMarkup(
    <QueuePanel
      snapshot={value}
      dispatchAvailable={options?.dispatchAvailable ?? true}
      busy={options?.busy ?? true}
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
    expect(html).toContain("aria-label=\"Send 1 queued message now\"");
    expect(html).toMatch(/<button[^>]*disabled=""/);
    expect(html).toContain("Remove");
  });

  it("holds dispatching and in-doubt items and explains ambiguous delivery", () => {
    for (const status of ["dispatching", "in_doubt"] as const) {
      const html = render(snapshot([
        batch("b", 1, ["r"], [item("r", "Held text", status)], status),
      ]));
      expect(html).toContain(status === "dispatching" ? "Sending" : "Delivery status unknown");
      if (status === "in_doubt") expect(html).toContain("held to avoid sending twice");
      expect(html).toContain("aria-label=\"Send 1 queued message now\"");
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
    expect(html).toContain("aria-label=\"Send 2 queued messages now\"");
    expect(html).toContain("aria-label=\"Remove queued message 1\"");
    expect((html.match(/<button[^>]*disabled=""/g) ?? []).length).toBe(2);
  });
});
