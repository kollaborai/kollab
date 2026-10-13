// Run: node --test tests/agent-network.test.ts (Node 24 strips the types).
import assert from "node:assert/strict";
import test from "node:test";
import { groupNetworkTree, groupRemoteByDevice, networkOf, targetsOn } from "../src/components/shell/agent-network.ts";

test("remote agents group under the computer they run on, both sorted by name", () => {
  const groups = groupRemoteByDevice([
    { name: "lapis", device: "prod-box", handle: "lapis@prod-box" },
    { name: "ruby", device: "devbox", handle: "ruby@devbox" },
    { name: "agate", device: "prod-box", handle: "agate@prod-box" },
  ]);

  assert.deepEqual(groups.map((group) => group.device), ["devbox", "prod-box"]);
  assert.deepEqual(groups[1].agents.map((agent) => agent.name), ["agate", "lapis"]);
});

test("no remote rows means no computer groups", () => {
  assert.deepEqual(groupRemoteByDevice([]), []);
});

test("in a chat with an agent on another computer, @names are that computer's", () => {
  const targets = targetsOn("prod-box", [
    { name: "lapis", device: "prod-box", handle: "lapis@prod-box", state: "working" },
    { name: "ruby", device: "devbox", handle: "ruby@devbox" },
  ]);

  assert.deepEqual(targets.agents, [{ name: "lapis", active: true, state: "working" }]);
  assert.deepEqual(targets.remote.map((agent) => agent.handle), ["ruby@devbox"]);
});

test("a failure reads as a sentence: no HTTP status, capitalized", async () => {
  const { ApiError, errorText } = await import("../src/api.ts");
  assert.equal(
    errorText(new ApiError("503: could not open this agent: box has not let mac open its agents", 503, null)),
    "Could not open this agent: box has not let mac open its agents",
  );
  assert.equal(errorText("engine unreachable"), "Engine unreachable");
});

test("the multi-network tree nests networks > devices > workspaces > agents (#158)", () => {
  const tree = groupNetworkTree(
    [
      {
        id: "home",
        name: "home-net",
        trust: "open",
        devices: [
          {
            device: "server-box",
            workspaces: [{ label: "kollab", agents: [{ name: "lapis", device: "server-box", handle: "lapis@server-box" }] }],
          },
          {
            device: "mac",
            workspaces: [{ label: "", agents: [{ name: "ruby", device: "mac", handle: "ruby@mac" }] }],
          },
        ],
      },
      {
        id: "lab",
        name: "lab-net",
        trust: "agents",
        devices: [
          {
            device: "gpu-rig",
            workspaces: [
              { label: "train", agents: [{ name: "agate", device: "gpu-rig", handle: "agate@gpu-rig" }] },
              { label: "eval", agents: [{ name: "onyx", device: "gpu-rig", handle: "onyx@gpu-rig" }] },
            ],
          },
        ],
      },
    ],
    "this-box",
    [],
  );

  assert.deepEqual(tree.map((net) => net.id), ["home", "lab"]);
  assert.deepEqual(tree[0].devices.map((dev) => dev.device), ["server-box", "mac"]);
  assert.deepEqual(tree[1].devices[0].workspaces.map((ws) => ws.label), ["train", "eval"]);
  assert.deepEqual(
    tree[1].devices[0].workspaces.map((ws) => ws.agents.map((agent) => agent.name)).flat(),
    ["agate", "onyx"],
  );
});

test("an engine without the aggregate synthesizes one group from the flat rows", () => {
  const tree = groupNetworkTree(undefined, "this-box", [
    { name: "lapis", device: "prod-box", handle: "lapis@prod-box" },
    { name: "agate", device: "prod-box", handle: "agate@prod-box" },
    { name: "ruby", device: "devbox", handle: "ruby@devbox" },
  ]);

  assert.equal(tree.length, 1);
  const flat = tree[0];
  assert.equal(flat.id, "flat");
  assert.deepEqual(flat.devices.map((dev) => dev.device), ["devbox", "prod-box"]);
  assert.deepEqual(
    flat.devices[1].workspaces.map((ws) => ws.agents.map((agent) => agent.name)).flat(),
    ["agate", "lapis"],
  );
  // Every flat row lands in the synthesized tree exactly once.
  assert.equal(
    flat.devices.reduce((sum, dev) => sum + dev.workspaces.reduce((n, ws) => n + ws.agents.length, 0), 0),
    3,
  );
});

test("no networks and no remote rows means one empty synthesized group", () => {
  const tree = groupNetworkTree(undefined, "this-box", []);
  assert.equal(tree.length, 1);
  assert.deepEqual(tree[0].devices, []);
});

test("targetsOn narrows by network and workspace once rows carry them", () => {
  const rows = [
    { name: "lapis", device: "server-box", handle: "lapis@server-box", network: "home", workspace: "kollab" },
    { name: "agate", device: "gpu-rig", handle: "agate@gpu-rig", network: "lab", workspace: "train" },
    { name: "onyx", device: "gpu-rig", handle: "onyx@gpu-rig", network: "lab", workspace: "eval" },
  ];

  // Narrowing to the lab network drops home-net rows from both halves.
  const onLab = targetsOn("gpu-rig", rows, "lab");
  assert.deepEqual(onLab.agents.map((agent) => agent.name), ["agate", "onyx"]);
  assert.deepEqual(onLab.remote.map((agent) => agent.handle), []);

  const onLabEval = targetsOn("gpu-rig", rows, "lab", "eval");
  assert.deepEqual(onLabEval.agents.map((agent) => agent.name), ["onyx"]);
});

test("targetsOn filters are ignored by rows without the fields (old engines)", () => {
  const rows = [
    { name: "lapis", device: "server-box", handle: "lapis@server-box" },
    { name: "agate", device: "gpu-rig", handle: "agate@gpu-rig" },
  ];

  const targets = targetsOn("gpu-rig", rows, "lab", "train");
  assert.deepEqual(targets.agents.map((agent) => agent.name), ["agate"]);
  assert.deepEqual(targets.remote.map((agent) => agent.handle), ["lapis@server-box"]);
});

test("an agent's own network says whether one here carries it and where to start one", () => {
  const opal = { name: "opal", device: "lab-box", handle: "opal@lab-box", state: "idle" };
  const lapis = { name: "lapis", device: "prod-box", handle: "lapis@prod-box", state: "idle" };
  const network = {
    device: "mac-box",
    remote: [lapis, opal],
    live: true,
    networks: [
      { id: "h", name: "home-net", live: true, devices: [{ device: "prod-box", workspaces: [{ label: "", agents: [lapis] }] }] },
      {
        id: "l",
        name: "lab-net",
        live: false,
        folder: "/w/lab",
        devices: [{ device: "lab-box", workspaces: [{ label: "", agents: [opal] }] }],
      },
    ],
  };

  // home-net is live although lab-net went quiet: opening lapis goes straight through.
  assert.deepEqual(networkOf(network, "lapis@prod-box"), { live: true, folder: undefined, agents: [lapis] });
  // Opening opal first starts an agent in lab-net's own folder.
  assert.deepEqual(networkOf(network, "opal@lab-box"), { live: false, folder: "/w/lab", agents: [opal] });
  // Without per-network blocks the flat fields answer.
  assert.deepEqual(networkOf({ device: "mac-box", remote: [opal], live: false, folder: "/w" }, "opal@lab-box"), {
    live: false,
    folder: "/w",
    agents: [opal],
  });
});
