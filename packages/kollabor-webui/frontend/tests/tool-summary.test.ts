// Run: node --test tests/tool-summary.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { askedForOk, summarizeToolCall } from "../src/utils/tool-summary.ts";

test("MCP filesystem tools show the file they touch", () => {
  assert.equal(
    summarizeToolCall("read_file", JSON.stringify({ path: "/repo/docs/notes.md" })),
    "Read File · notes.md",
  );
});

test("native file tools keep showing their file", () => {
  assert.equal(
    summarizeToolCall("file_read", JSON.stringify({ file: "src/app.py" })),
    "Read File · app.py",
  );
});

test("tools without a file argument keep their plain name", () => {
  assert.equal(summarizeToolCall("web_search", JSON.stringify({ query: "ruby" })), "Web Search");
});

test("shell commands say what ran", () => {
  const run = (command: string) =>
    summarizeToolCall("terminal", JSON.stringify({ command }));
  assert.equal(run("ls -la"), "List Files");
  assert.equal(run("ls -la src/components"), "List Files · components");
  assert.equal(run("make build"), "Run · make build");
  // A spoken yes runs npm: its row is where the user sees the exact command.
  assert.equal(run("npm test"), "Run · npm test");
  assert.equal(run("npm install lodash"), "Run · npm install lodash");
  assert.equal(run("git status"), "Git Status");
});

test("outgoing hub reply tool row labels Replied to <agent>", () => {
  assert.equal(
    summarizeToolCall("hub_reply", JSON.stringify({ to: "lapis", message: "done" })),
    "Replied to lapis",
  );
  assert.equal(
    summarizeToolCall("hub_reply", JSON.stringify({ to: "nephrite@devbox" })),
    "Replied to nephrite@devbox",
  );
  assert.equal(
    summarizeToolCall("hub_reply", JSON.stringify({ target: "sapphire" })),
    "Replied to sapphire",
  );
  assert.equal(
    summarizeToolCall("hub_reply", "{}"),
    "Replied to agent",
  );
  assert.equal(
    summarizeToolCall("hub_msg", JSON.stringify({ to: "zircon", reply_to: "msg-1" })),
    "Replied to zircon",
  );
});

test("a call the agent asked the user to OK is not a failure", () => {
  assert.equal(askedForOk("Not run yet: this needs the user's OK. In your reply, say why."), true);
  assert.equal(askedForOk("Exit code 1: no such file"), false);
  assert.equal(askedForOk(undefined), false);
});
