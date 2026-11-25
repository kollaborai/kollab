import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import type { ChatRow } from "../src/chat-stream";
import { hubNoteOf, HubNoteView, WelcomeContext } from "../src/components/Thread";

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
