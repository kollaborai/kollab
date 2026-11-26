// @vitest-environment jsdom
import { act, createElement, type PropsWithChildren } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ChatRow } from "../src/chat-stream";

const state = vi.hoisted(() => ({
  messageId: "",
  rows: [] as ChatRow[],
}));

vi.mock("@assistant-ui/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@assistant-ui/react")>();
  return {
    ...actual,
    useAuiState: (selector: (value: unknown) => unknown) =>
      selector({ message: { id: state.messageId } }),
    MessagePrimitive: {
      ...actual.MessagePrimitive,
      Root: ({ children, ...props }: PropsWithChildren<Record<string, unknown>>) =>
        createElement("div", props, children),
      Parts: () => {
        const row = state.rows.find((candidate) => candidate.id === state.messageId);
        return createElement(
          "span",
          null,
          row?.parts
            .filter((part): part is Extract<ChatRow["parts"][number], { type: "text" }> => part.type === "text")
            .map((part) => part.text)
            .join(""),
        );
      },
    },
  };
});

vi.mock("@/chat-store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/chat-store")>();
  return { ...actual, useChatStore: () => ({ rows: state.rows }) };
});

vi.mock("@/components/agents/Avatar", () => ({
  Avatar: ({ gem }: { gem: string }) => createElement("span", { "data-test-gem": gem }),
}));

import { HubNoteMessage, hubNoteOf, HubNoteView, WelcomeContext } from "../src/components/Thread";

// The chat store's row for a hub note: the note is the row's hub-note part, not
// message metadata (decision s, #1451).
const noteRow = (parts: ChatRow["parts"]): ChatRow => ({
  id: "note-1",
  seq: 4,
  kind: "note",
  role: "system",
  turn_id: null,
  status: "complete",
  parts,
  created_at: "2026-11-25T10:00:00Z",
});

test("hub notes are read from the note row's hub-note part", () => {
  const row = noteRow([{ type: "hub-note", from: "ruby", to: "", text: "hello everyone", observed: false }]);
  expect(hubNoteOf(row)).toEqual({ type: "hub-note", from: "ruby", to: "", text: "hello everyone", observed: false });
});

test("a row without a hub-note part carries no hub note", () => {
  expect(hubNoteOf(noteRow([{ type: "text", text: "plain" }]))).toBeUndefined();
});

// A broadcast (empty recipient) must never name the observing window's own
// agent: the note header is "From → All", matching the stored "-> *" (#671).
test("a broadcast note addresses All, never the window's own agent", () => {
  const html = renderToStaticMarkup(
    <WelcomeContext.Provider value={{ name: "lapis" }}>
      <HubNoteView from="ruby" to="">
        hello everyone
      </HubNoteView>
    </WelcomeContext.Provider>,
  );
  expect(html).toContain("Ruby → All");
  expect(html).not.toContain("Lapis");
});

// A note delivered straight to this window ("Message from …", no explicit
// recipient) keeps addressing the window's own agent.
test("a direct note with no explicit recipient is for the window's own agent", () => {
  const html = renderToStaticMarkup(
    <WelcomeContext.Provider value={{ name: "lapis" }}>
      <HubNoteView from="koordinator" to="" isDirect>
        take over the lane
      </HubNoteView>
    </WelcomeContext.Provider>,
  );
  expect(html).toContain("Koordinator → Lapis");
});

// An addressed note names the addressed agent, device and all.
test("an addressed note renders the addressed agent", () => {
  const html = renderToStaticMarkup(
    <WelcomeContext.Provider value={{ name: "lapis" }}>
      <HubNoteView from="ruby" to="lapis@devbox">
        build is green
      </HubNoteView>
    </WelcomeContext.Provider>,
  );
  expect(html).toContain("Ruby → Lapis on devbox");
  expect(html).not.toContain("Ruby → All");
});

const hubRow: ChatRow = {
  id: "hub-7",
  seq: 12,
  kind: "note",
  role: "system",
  turn_id: null,
  status: "complete",
  parts: [{
    type: "hub-note",
    from: "ruby",
    to: "lapis",
    text: "Build completed; the report is ready.",
    observed: false,
    kind: "message",
    thread_id: "thread-7",
    reply_to: null,
    is_direct: true,
    presence: null,
  }],
  created_at: "2026-11-25T10:00:00Z",
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function renderHubRow(row: ChatRow) {
  state.rows = [row];
  state.messageId = row.id;
  await act(async () => root.render(createElement(HubNoteMessage)));
}

test("structured hub-note text renders from a note row", async () => {
  await renderHubRow(hubRow);

  const hubMessage = container.querySelector('[data-slot="kollab_hub-message"]');
  expect(hubMessage?.textContent).toContain("Build completed; the report is ready.");
  expect(hubMessage?.getAttribute("data-role")).toBe("system");
});

test("structured hub-note text renders from an input row", async () => {
  await renderHubRow({ ...hubRow, id: "hub-input-7", kind: "input", role: "user", turn_id: "turn-7" });

  const hubMessage = container.querySelector('[data-slot="kollab_hub-message"]');
  expect(hubMessage?.textContent).toContain("Build completed; the report is ready.");
  expect(hubMessage?.getAttribute("data-role")).toBe("user");
});

test("ordinary system markers use the regular system renderer", async () => {
  await renderHubRow({
    ...hubRow,
    id: "marker-7",
    kind: "marker",
    parts: [{ type: "text", text: "Earlier messages compacted" }],
  });

  expect(container.querySelector('[data-slot="kollab_hub-message"]')).toBeNull();
  expect(container.querySelector('[data-slot="aui_system-message-root"]')?.textContent)
    .toContain("Earlier messages compacted");
});
