import assert from "node:assert/strict";
import test from "node:test";
import { deviceOrderKey, groupByFolder, orderedDevices, orderKey, reorderDevicesWithinNetwork } from "../src/components/shell/sidebar-order.ts";
import { createSidebarOrderController, type SidebarOrderSnapshot } from "../src/components/shell/sidebar-order-controller.ts";

test("device identity uses the producer's network ID and exact device name", () => {
  const key = deviceOrderKey("origin-slug", "office-laptop");
  assert.deepEqual(JSON.parse(key), ["device", "origin-slug", "office-laptop"]);
  assert.notEqual(key, deviceOrderKey("another-origin", "office-laptop"));
  assert.notEqual(key, deviceOrderKey("origin-slug", "renamed-laptop"));
  assert.notEqual(deviceOrderKey("a,b", "c"), deviceOrderKey("a", "b,c"));
  assert.notEqual(key, orderKey("/work", "office-laptop", ["remote", "origin-slug", "office-laptop"]));
});

test("all device positions survive refreshed presence and preserve children and every other saved key", () => {
  const network = "origin-slug";
  const agentRows = ["ruby", "lapis"].map((name) => ({
    key: orderKey("/work", `${name}@office-laptop`, ["remote", network, "office-laptop"]),
    folder: "/work",
  }));
  const unrelated = [
    agentRows[1].key,
    deviceOrderKey(network, "temporarily-absent"),
    orderKey("/hidden", "hidden-agent"),
    deviceOrderKey("other-network", "office-laptop"),
    agentRows[0].key,
    orderKey("/work", "lapis", ["conversation", "durable-history"]),
  ];
  for (let count = 1; count <= 5; count += 1) {
    const devices = Array.from({ length: count }, (_, index) => ({
      device: `device-${index}`,
      workspaces: [{ label: "/work", agents: [{ name: "ruby" }, { name: "lapis" }] }],
    }));
    const ownKeys = new Set(devices.map((device) => deviceOrderKey(network, device.device)));
    const saved = [deviceOrderKey(network, devices[0].device), ...unrelated];
    const initial = orderedDevices(network, devices, saved);
    const agentsBefore = groupByFolder(agentRows, saved);
    const inputBefore = structuredClone(devices);
    for (let from = 0; from < count; from += 1) {
      for (let to = 0; to < count; to += 1) {
        const key = deviceOrderKey(network, initial[from].device);
        const next = reorderDevicesWithinNetwork(network, initial, key, to, saved);
        if (from === to) {
          assert.equal(next, null);
          continue;
        }
        assert.ok(next);
        const refreshed = orderedDevices(network, [...devices].reverse(), next);
        assert.equal(refreshed[to], initial[from]);
        assert.deepEqual(refreshed.filter((device) => device !== initial[from]), initial.filter((_, index) => index !== from));
        for (const device of refreshed) assert.equal(device.workspaces, devices.find((row) => row.device === device.device)?.workspaces);
        assert.deepEqual(groupByFolder(agentRows, next), agentsBefore);
        assert.deepEqual(next.filter((savedKey) => !ownKeys.has(savedKey)), saved.filter((savedKey) => !ownKeys.has(savedKey)));
        assert.equal(new Set(next).size, next.length);
        assert.deepEqual(devices, inputBefore);
      }
    }
    for (const invalid of [-1, count, 0.5, NaN, Infinity]) {
      assert.equal(reorderDevicesWithinNetwork(network, initial, deviceOrderKey(network, initial[0].device), invalid, saved), null);
    }
    assert.equal(reorderDevicesWithinNetwork(network, initial, deviceOrderKey("other-network", initial[0].device), 0, saved), null);
  }
  assert.equal(reorderDevicesWithinNetwork(network, [], "missing", 0, unrelated), null);
});

test("a device drop cannot overwrite another client's agent order and retries against its refreshed revision", async () => {
  const network = "origin-slug";
  const devices = [{ device: "first" }, { device: "second" }];
  const agents = [orderKey("/work", "lapis"), orderKey("/work", "ruby")];
  const deviceKeys = devices.map((device) => deviceOrderKey(network, device.device));
  let server: SidebarOrderSnapshot = { order: [...deviceKeys, ...agents], revision: 4, warning: null };
  const attempts: number[] = [];
  const controller = createSidebarOrderController({
    async saveSidebarOrder(order, revision) {
      attempts.push(revision);
      if (revision !== server.revision) throw Object.assign(new Error("another client changed order"), { status: 409 });
      server = { order, revision: revision + 1, warning: null };
      return server;
    },
    async getSidebarOrder() { return server; },
    isConflict: (error) => error instanceof Error && "status" in error && error.status === 409,
    isRecoveryRequired: () => false,
    errorText: (error) => String(error),
  }, () => {});
  controller.sync(controller.stamp(), server);
  let release = controller.beginDrag();
  server = { order: [...deviceKeys, ...agents.toReversed()], revision: 5, warning: null };
  controller.sync(controller.stamp(), server);
  assert.equal(controller.snapshot().revision, 4);
  const stale = reorderDevicesWithinNetwork(network, devices, deviceKeys[1], 0, controller.snapshot().order);
  assert.ok(stale);
  await controller.save(stale);
  release();
  assert.deepEqual(attempts, [4]);
  assert.deepEqual(controller.snapshot().order, server.order);
  assert.equal(controller.snapshot().revision, 5);

  release = controller.beginDrag();
  const retry = reorderDevicesWithinNetwork(network, devices, deviceKeys[1], 0, controller.snapshot().order);
  assert.ok(retry);
  await controller.save(retry);
  release();
  assert.deepEqual(attempts, [4, 5]);
  assert.deepEqual(server.order, [...deviceKeys.toReversed(), ...agents.toReversed()]);
  assert.equal(server.revision, 6);
});
