import assert from "node:assert/strict";
import test from "node:test";
import { networkSummaryRows, summaryRows } from "../src/components/panels/panel-model.ts";

const address = "kollabor.ai/c/0123456789abcdef";

test("summary tuples and object maps retain their label/value contract", () => {
  assert.deepEqual(summaryRows([["Network", "Kollab"], ["Count", 0], ["Ready", false], ["Missing", null]]), [
    { label: "Network", value: "Kollab" },
    { label: "Count", value: "0" },
    { label: "Ready", value: "false" },
    { label: "Missing", value: "" },
  ]);
  assert.deepEqual(summaryRows({ Network: "Kollab", Count: 0, Ready: false, Missing: null }), [
    { label: "Network", value: "Kollab" },
    { label: "Count", value: "0" },
    { label: "Ready", value: "false" },
    { label: "Missing", value: "" },
  ]);
});

test("only explicit boolean copy metadata on a real string is preserved", () => {
  assert.deepEqual(summaryRows([{ label: "Knocks Address", value: address, copyable: true }]), [
    { label: "Knocks Address", value: address, copyable: true },
  ]);
  for (const copyable of [false, "true", "false", 1, 0, null, undefined, {}]) {
    assert.deepEqual(summaryRows([{ label: "Knocks Address", value: address, copyable }]), [
      { label: "Knocks Address", value: address },
    ]);
  }
});

test("blank or non-string summary values cannot acquire copy controls", () => {
  for (const value of ["", "  \n\t", null, undefined, 0, false, [], {}]) {
    const [row] = summaryRows([{ label: "Address", value, copyable: true }]);
    assert.equal(row.copyable, undefined);
  }
  assert.deepEqual(summaryRows([{ label: "Value", value: "0", copyable: true }]), [
    { label: "Value", value: "0", copyable: true },
  ]);
});

test("tuple extras and object-map contents never become copy metadata", () => {
  assert.deepEqual(summaryRows([["Address", address, true]]), [{ label: "Address", value: address }]);
  assert.deepEqual(summaryRows({ Address: { value: address, copyable: true } }), [
    { label: "Address", value: "[object Object]" },
  ]);
});

test("missing or malformed summaries contain no invented facts", () => {
  for (const summary of [null, undefined, "network", 1, false]) assert.deepEqual(summaryRows(summary), []);
  assert.deepEqual(summaryRows([null, "ignored", 1, { label: "Connection", value: "Offline" }]), [
    { label: "Connection", value: "Offline" },
  ]);
});

test("a missing owning-daemon address is unavailable and never inferred from names", () => {
  assert.deepEqual(networkSummaryRows({ Network: "example.org", "This Device": "my-device" }), [
    { label: "Network", value: "example.org" },
    { label: "This Device", value: "my-device" },
    { label: "Knocks Address", value: "Unavailable" },
  ]);
  assert.deepEqual(networkSummaryRows(undefined), [{ label: "Knocks Address", value: "Unavailable" }]);
});

test("an owning-daemon address retains the explicit copy opt-in unchanged", () => {
  assert.deepEqual(networkSummaryRows([{ label: "Knocks Address", value: address, copyable: true }]), [
    { label: "Knocks Address", value: address, copyable: true },
  ]);
});

test("unavailable and non-copyable address rows are neither duplicated nor made copyable", () => {
  assert.deepEqual(networkSummaryRows([{ label: "Knocks Address", value: "Unavailable" }]), [
    { label: "Knocks Address", value: "Unavailable" },
  ]);
  assert.deepEqual(networkSummaryRows([["Knocks Address", address]]), [{ label: "Knocks Address", value: address }]);
  assert.deepEqual(networkSummaryRows({ "knocks address": address }), [{ label: "knocks address", value: address }]);
  for (const value of ["", "  ", "Unavailable"]) {
    assert.deepEqual(networkSummaryRows([{ label: "Knocks Address", value, copyable: true }]), [
      { label: "Knocks Address", value: "Unavailable" },
    ]);
  }
});
