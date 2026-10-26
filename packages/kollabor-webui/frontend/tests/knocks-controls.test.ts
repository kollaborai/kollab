import assert from "node:assert/strict";
import test from "node:test";
import { pickerControlNeedsApply, settleControlSubmission } from "../src/components/panels/panel-model.ts";

test("only Knocks text controls defer submission; dropdowns and sibling pickers stay immediate", () => {
  assert.equal(pickerControlNeedsApply("connect-knocks", { type: "text_input" }), true);
  for (const type of ["dropdown", "checkbox", "label", "slider", "spinbox"] as const) {
    assert.equal(pickerControlNeedsApply("connect-knocks", { type }), false);
  }
  for (const panel of ["connect", "connect-join", "config", "llm", "model", "voice", "setup"]) {
    assert.equal(pickerControlNeedsApply(panel, { type: "text_input" }), false);
  }
});

test("applying one route clears that field while preserving another unsaved route and its error", () => {
  const current = {
    draft: { expect_route: "example.test/c/1234567890abcdef", block_route: "unfinished" },
    errors: { expect_route: "Old failure", block_route: "domain/c/16 hex" },
  };
  const result = settleControlSubmission(current, { path: "expect_route", value: current.draft.expect_route }, { ok: true });
  assert.deepEqual(result, {
    draft: { block_route: "unfinished" },
    errors: { block_route: "domain/c/16 hex" },
  });
  assert.deepEqual(current.draft, { expect_route: "example.test/c/1234567890abcdef", block_route: "unfinished" });
  assert.deepEqual(current.errors, { expect_route: "Old failure", block_route: "domain/c/16 hex" });
});

test("validation rejection keeps partial text, the other draft and useful field errors", () => {
  const current = {
    draft: { expect_route: "ex", block_route: "another.test/c/abcdef1234567890" },
    errors: { block_route: "Existing error" },
  };
  const result = settleControlSubmission(current, { path: "expect_route", value: "ex" }, {
    ok: false,
    message: "That is not a contact route.",
    errors: { expect_route: "domain/c/16 hex" },
  });
  assert.equal(result.draft, current.draft);
  assert.deepEqual(result.errors, { block_route: "Existing error", expect_route: "domain/c/16 hex" });
});

test("daemon and transport failures without field errors attach their message and retain all draft text", () => {
  const current = {
    draft: { expect_route: "keep this", block_route: "example.test/c/1234567890abcdef" },
    errors: { expect_route: "Existing error" },
  };
  for (const message of ["Knocks are unavailable right now.", "The request could not reach the engine.", undefined]) {
    const result = settleControlSubmission(current, { path: "block_route", value: current.draft.block_route }, { ok: false, message });
    assert.equal(result.draft, current.draft);
    assert.equal(result.errors.expect_route, "Existing error");
    assert.equal(result.errors.block_route, message ?? "That did not work.");
  }
});

test("late success cannot erase newer text in the submitted field", () => {
  const current = { draft: { expect_route: "newer text", block_route: "keep this too" }, errors: {} };
  const result = settleControlSubmission(current, { path: "expect_route", value: "older submitted text" }, { ok: true });
  assert.equal(result.draft, current.draft);
  assert.deepEqual(result.draft, { expect_route: "newer text", block_route: "keep this too" });
});

test("separate successful Applies settle both routes without resurrecting a cleared field", () => {
  const current = {
    draft: { expect_route: "example.test/c/1234567890abcdef", block_route: "other.test/c/abcdef1234567890" },
    errors: {},
  };
  const first = settleControlSubmission(current, { path: "expect_route", value: current.draft.expect_route }, { ok: true });
  const second = settleControlSubmission(first, { path: "block_route", value: current.draft.block_route }, { ok: true });
  assert.deepEqual(second, { draft: {}, errors: {} });
});
