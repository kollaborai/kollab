import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import { HubNoteView, WelcomeContext } from "../src/components/Thread";

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
