// Run: node --test tests/sidebar-order.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import {
  buildLocalOrderRows,
  groupByFolder,
  localConversationOrderKey,
  localOfflineOrderKey,
  localSessionOrderKey,
  moveWithinFolder,
  orderKey,
  workspaceName,
} from "../src/components/shell/sidebar-order.ts";
import { createSidebarOrderController, type SidebarOrderSnapshot } from "../src/components/shell/sidebar-order-controller.ts";

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


function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const orderClient = (
  saveSidebarOrder: (order: string[], revision: number) => Promise<SidebarOrderSnapshot>,
  getSidebarOrder: () => Promise<SidebarOrderSnapshot>,
) => ({
  saveSidebarOrder,
  getSidebarOrder,
  isConflict: (reason: unknown) => typeof reason === "object" && reason !== null && "status" in reason && reason.status === 409,
  isRecoveryRequired: (reason: unknown) => typeof reason === "object" && reason !== null && "status" in reason && reason.status === 422,
  errorText: (reason: unknown) => reason instanceof Error ? reason.message : String(reason),
});

test("failed writes restore the last confirmed order and ignore stale polls", async () => {
  const confirmed = { order: ["server-a", "server-b"], revision: 4, warning: null };
  const pending = deferred<SidebarOrderSnapshot>();
  const started = deferred<void>();
  const controller = createSidebarOrderController(
    orderClient(() => { started.resolve(); return pending.promise; }, async () => confirmed),
    () => {},
  );
  controller.sync(controller.stamp(), confirmed);
  const oldPoll = controller.stamp();
  const save = controller.save(["optimistic"]);
  await started.promise;
  controller.sync(oldPoll, { order: ["stale-poll"], revision: 2 });
  controller.sync(controller.stamp(), { order: ["in-flight-stale-poll"], revision: 4 });
  assert.deepEqual(controller.snapshot().order, ["optimistic"]);

  pending.reject(new Error("offline"));
  await save;
  assert.deepEqual(controller.snapshot().order, confirmed.order);
  assert.equal(controller.snapshot().revision, 4);
  assert.equal(controller.snapshot().error, "offline");
});

test("queued writes use the latest confirmed revision after an earlier failure", async () => {
  const confirmed = { order: ["server"], revision: 4, warning: null };
  const first = deferred<SidebarOrderSnapshot>();
  const second = deferred<SidebarOrderSnapshot>();
  const firstStarted = deferred<void>();
  const secondStarted = deferred<void>();
  const calls: Array<{ order: string[]; revision: number }> = [];
  const controller = createSidebarOrderController(
    orderClient((order, revision) => {
      calls.push({ order, revision });
      if (calls.length === 1) { firstStarted.resolve(); return first.promise; }
      secondStarted.resolve();
      return second.promise;
    }, async () => confirmed),
    () => {},
  );
  controller.sync(controller.stamp(), confirmed);
  const firstSave = controller.save(["move-one"]);
  const secondSave = controller.save(["move-two"]);
  await firstStarted.promise;
  first.reject(new Error("temporary failure"));
  await firstSave;
  await secondStarted.promise;

  assert.deepEqual(controller.snapshot().order, confirmed.order);
  assert.equal(controller.snapshot().error, "temporary failure");
  assert.deepEqual(calls.map((call) => call.revision), [4, 4]);

  second.resolve({ order: ["move-two"], revision: 5, warning: null });
  await secondSave;
  assert.deepEqual(controller.snapshot().order, ["move-two"]);
  assert.equal(controller.snapshot().revision, 5);
  assert.equal(controller.snapshot().error, null);
});

test("a stale revision refreshes the server order and cancels queued full-list writes", async () => {
  const confirmed = { order: ["old"], revision: 3, warning: null };
  const refreshed = { order: ["other-folder-move"], revision: 4, warning: null };
  const saveResponse = deferred<SidebarOrderSnapshot>();
  const started = deferred<void>();
  let writes = 0;
  const controller = createSidebarOrderController(
    orderClient(() => { writes += 1; started.resolve(); return saveResponse.promise; }, async () => refreshed),
    () => {},
  );
  controller.sync(controller.stamp(), confirmed);
  const firstSave = controller.save(["stale-client-order"]);
  const queuedSave = controller.save(["stale-client-order-two"]);
  await started.promise;
  saveResponse.reject(Object.assign(new Error("stale revision"), { status: 409 }));
  await Promise.all([firstSave, queuedSave]);

  assert.equal(writes, 1);
  assert.deepEqual(controller.snapshot().order, refreshed.order);
  assert.equal(controller.snapshot().revision, 4);
  assert.match(controller.snapshot().error || "", /another client/i);
});


test("unreadable-store failures refresh and expose the preserved-file warning", async () => {
  const confirmed = { order: ["before-corruption"], revision: 2, warning: null };
  const damaged = {
    order: [],
    revision: 0,
    warning: "Sidebar order file is unreadable and was preserved; repair it before saving.",
  };
  const unreadable = Object.assign(new Error("Sidebar order storage is unreadable."), { status: 422 });
  const controller = createSidebarOrderController(
    orderClient(async () => { throw unreadable; }, async () => damaged),
    () => {},
  );
  controller.sync(controller.stamp(), confirmed);
  assert.equal(controller.snapshot().warning, null);
  await controller.save(["unsaved"]);
  assert.deepEqual(controller.snapshot().order, []);
  assert.equal(controller.snapshot().warning, damaged.warning);
  assert.match(controller.snapshot().error || "", /unreadable/i);
});


test("duplicate local sessions use stable session keys with persona/offline anchors", () => {
  const folder = "/workspace/alpha";
  const identity = "Lapis";
  const personaKey = orderKey(folder, identity);
  const sessionA = localSessionOrderKey(folder, identity, "session-a");
  const sessionB = localSessionOrderKey(folder, identity, "session-b");
  const conversationB = localConversationOrderKey(folder, identity, "conversation-b");
  const offline = localOfflineOrderKey(folder, identity);
  const sessions = [
    { session_id: "session-a", conversation_id: "conversation-a", identity, workspace: folder },
    { session_id: "session-b", conversation_id: "conversation-b", identity, workspace: folder },
  ];
  const offlineAgents = [{ identity, workspace: folder }];
  const rows = buildLocalOrderRows(sessions, offlineAgents, []);
  assert.deepEqual(rows.map((row) => row.key), [sessionA, sessionB, offline]);
  assert.equal(new Set(rows.map((row) => row.key)).size, rows.length);

  const moved = moveWithinFolder(groupByFolder(rows, []), sessionB, -1, []);
  assert.deepEqual(moved, [sessionB, conversationB, personaKey, sessionA, localConversationOrderKey(folder, identity, "conversation-a"), offline]);
  assert.equal(moved?.filter((key) => key === personaKey).length, 1);

  // A new process/session id can reuse the durable conversation id and find
  // the prior row order; offline-only and legacy single-session views keep the
  // persona anchor rather than inheriting a process id.
  const restarted = buildLocalOrderRows(
    [{ session_id: "session-b-restarted", conversation_id: "conversation-b", identity, workspace: folder }],
    [],
    moved || [],
  );
  assert.deepEqual(restarted[0].fallbackKeys, [conversationB, personaKey]);
  const offlineOnly = buildLocalOrderRows([], offlineAgents, moved || []);
  assert.equal(offlineOnly[0].key, offline);
  const legacyOffline = buildLocalOrderRows([], offlineAgents, [personaKey]);
  assert.equal(legacyOffline[0].key, personaKey);
  const legacySingle = buildLocalOrderRows([sessions[0]], [], [personaKey]);
  assert.equal(legacySingle[0].key, personaKey);
});


test("conversation fallbacks preserve exact interleaving when session IDs rotate", () => {
  const folder = "/workspace/alpha";
  const lapis = "lapis";
  const persona = orderKey(folder, lapis);
  const initial = buildLocalOrderRows([
    { session_id: "b-live", conversation_id: "conv-b", identity: lapis, workspace: folder },
    { session_id: "a-live", conversation_id: "conv-a", identity: lapis, workspace: folder },
    { session_id: "c-live", conversation_id: "conv-c", identity: "sapphire", workspace: folder },
  ], [], []);
  const sessionA = localSessionOrderKey(folder, lapis, "a-live");
  const groups = groupByFolder(initial, []);
  assert.deepEqual(groups[0].rows.map((row) => row.session?.conversation_id), ["conv-b", "conv-a", "conv-c"]);

  const saved = moveWithinFolder(groups, sessionA, 1, []);
  const expectedB = localSessionOrderKey(folder, lapis, "b-live");
  const expectedC = orderKey(folder, "sapphire");
  assert.deepEqual(saved, [
    expectedB,
    localConversationOrderKey(folder, lapis, "conv-b"),
    persona,
    expectedC,
    sessionA,
    localConversationOrderKey(folder, lapis, "conv-a"),
  ]);

  const afterRestart = buildLocalOrderRows([
    { session_id: "b-new-process", conversation_id: "conv-b", identity: lapis, workspace: folder },
    { session_id: "a-new-process", conversation_id: "conv-a", identity: lapis, workspace: folder },
    { session_id: "c-new-process", conversation_id: "conv-c", identity: "sapphire", workspace: folder },
  ], [], saved || []);
  const restored = groupByFolder(afterRestart, saved || []);
  assert.deepEqual(restored[0].rows.map((row) => row.session?.conversation_id), ["conv-b", "conv-c", "conv-a"]);
});

test("same-conversation duplicate rows remain individually movable without key collisions", () => {
  const folder = "/workspace/alpha";
  const identity = "lapis";
  const rows = buildLocalOrderRows([
    { session_id: "session-left", conversation_id: "same-conversation", identity, workspace: folder },
    { session_id: "session-right", conversation_id: "same-conversation", identity, workspace: folder },
  ], [], []);
  assert.equal(new Set(rows.map((row) => row.key)).size, 2);
  const groups = groupByFolder(rows, []);
  const moved = moveWithinFolder(groups, rows[1].key, -1, []);
  assert.deepEqual(moved?.slice(0, 2), [rows[1].key, localConversationOrderKey(folder, identity, "same-conversation")]);
});


test("specific conversation ranks preserve interleaving when session IDs rotate", () => {
  const folder = "/workspace/alpha";
  const lapis = "lapis";
  const personaKey = orderKey(folder, lapis);
  const initial = buildLocalOrderRows([
    { session_id: "b-live", conversation_id: "conv-b", identity: lapis, workspace: folder },
    { session_id: "a-live", conversation_id: "conv-a", identity: lapis, workspace: folder },
    { session_id: "c-live", conversation_id: "conv-c", identity: "sapphire", workspace: folder },
  ], [], []);
  const sessionA = localSessionOrderKey(folder, lapis, "a-live");
  const groups = groupByFolder(initial, []);
  const saved = moveWithinFolder(groups, sessionA, 1, []);
  const sessionB = localSessionOrderKey(folder, lapis, "b-live");
  const personaSapphire = orderKey(folder, "sapphire");
  assert.deepEqual(saved, [
    sessionB,
    localConversationOrderKey(folder, lapis, "conv-b"),
    personaKey,
    personaSapphire,
    sessionA,
    localConversationOrderKey(folder, lapis, "conv-a"),
  ]);

  const afterRestart = buildLocalOrderRows([
    { session_id: "b-new-process", conversation_id: "conv-b", identity: lapis, workspace: folder },
    { session_id: "a-new-process", conversation_id: "conv-a", identity: lapis, workspace: folder },
    { session_id: "c-new-process", conversation_id: "conv-c", identity: "sapphire", workspace: folder },
  ], [], saved || []);
  const restored = groupByFolder(afterRestart, saved || []);
  assert.deepEqual(restored[0].rows.map((row) => row.session?.conversation_id), ["conv-b", "conv-c", "conv-a"]);
});

test("two rows sharing a conversation stay unique and movable", () => {
  const folder = "/workspace/alpha";
  const identity = "lapis";
  const sessions = [
    { session_id: "left-session", conversation_id: "shared-conversation", identity, workspace: folder },
    { session_id: "right-session", conversation_id: "shared-conversation", identity, workspace: folder },
  ];
  const rows = buildLocalOrderRows(sessions, [], []);
  assert.equal(new Set(rows.map((row) => row.key)).size, 2);
  const moved = moveWithinFolder(groupByFolder(rows, []), rows[1].key, -1, []);
  assert.deepEqual(moved?.slice(0, 3), [
    rows[1].key,
    localConversationOrderKey(folder, identity, "shared-conversation"),
    orderKey(folder, identity),
  ]);
  const afterRestart = buildLocalOrderRows([
    { session_id: "left-new", conversation_id: "shared-conversation", identity, workspace: folder },
    { session_id: "right-new", conversation_id: "shared-conversation", identity, workspace: folder },
  ], [], moved || []);
  assert.equal(new Set(afterRestart.map((row) => row.key)).size, 2);
  assert.deepEqual(groupByFolder(afterRestart, moved || [])[0].rows.map((row) => row.session?.conversation_id), [
    "shared-conversation",
    "shared-conversation",
  ]);
});
