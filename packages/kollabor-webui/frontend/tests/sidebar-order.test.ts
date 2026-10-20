// Run: node --test tests/sidebar-order.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { groupByFolder, moveWithinFolder, orderKey, workspaceName } from "../src/components/shell/sidebar-order.ts";

const row = (folder: string, name: string) => ({ key: orderKey(folder, name), folder });

test("rows group by folder and follow the saved order; unsaved rows keep arrival order", () => {
  const rows = [row("/a", "lapis"), row("/b", "ruby"), row("/a", "peridot"), row("/b", "onyx")];
  const saved = [orderKey("/b", "onyx"), orderKey("/a", "peridot")];
  const groups = groupByFolder(rows, saved);
  assert.deepEqual(groups.map((g) => g.folder), ["/b", "/a"]);
  assert.deepEqual(groups[0].rows.map((r) => r.key), [orderKey("/b", "onyx"), orderKey("/b", "ruby")]);
  assert.deepEqual(groups[1].rows.map((r) => r.key), [orderKey("/a", "peridot"), orderKey("/a", "lapis")]);
});

test("moving swaps within the folder, keeps hidden spots, and stops at the edges", () => {
  const groups = groupByFolder([row("/a", "x"), row("/a", "y")], []);
  const hidden = orderKey("/z", "gone");
  const next = moveWithinFolder(groups, orderKey("/a", "y"), -1, [hidden]);
  assert.deepEqual(next, [orderKey("/a", "y"), orderKey("/a", "x"), hidden]);
  assert.equal(moveWithinFolder(groups, orderKey("/a", "x"), -1, []), null);
});


test("remote order keys are scoped by network and device without changing local keys", () => {
  assert.equal(orderKey("/w/one", "Lapis"), '["pos","/w/one","lapis"]');
  const networkOne = orderKey("/w/one", "lapis@laptop", ["remote", "network-one", "laptop"]);
  assert.notEqual(networkOne, orderKey("/w/one", "lapis@laptop", ["remote", "network-two", "laptop"]));
  assert.notEqual(networkOne, orderKey("/w/one", "lapis@laptop", ["remote", "network-one", "desktop"]));
  assert.notEqual(networkOne, orderKey("/w/one", "lapis@laptop"));
});

test("workspace labels handle POSIX paths, Windows paths, and empty folders", () => {
  assert.equal(workspaceName("/Users/example/project"), "project");
  assert.equal(workspaceName("C:\\Users\\example\\project"), "project");
  assert.equal(workspaceName("/"), "No Folder");
});
