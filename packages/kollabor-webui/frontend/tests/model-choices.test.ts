import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";
import type { EngineApi, ModelChoice } from "../src/api.ts";
import {
  MAX_POLLS,
  POLL_MS,
  getModelChoices,
  loadModelChoices,
  rowsForProfiles,
  subscribeModelChoices,
} from "../src/components/shell/model-choices.ts";

type Discovery = Record<string, { status: "ready" | "partial" | "unavailable"; more?: boolean; message?: string }>;
type Answer = { choices: ModelChoice[]; discovery?: Discovery };

const row = (profile: string, model = "", effort = ""): ModelChoice => ({ profile, provider: "acp", model, effort });
const loading = { status: "partial", more: true } as const;
const ready = { status: "ready" } as const;

/** An engine that answers with each given answer in turn (the last one repeats); an Error rejects. */
function fakeApi(...answers: (Answer | Error)[]) {
  /** The `refresh` argument of each ask; `workspaces` holds the folder of the same ask. */
  const calls: Array<boolean | undefined> = [];
  const workspaces: Array<string | undefined> = [];
  const api = {
    listModelChoices: vi.fn(async (workspace?: string, refresh?: boolean) => {
      workspaces.push(workspace);
      calls.push(refresh);
      const next = answers[Math.min(calls.length - 1, answers.length - 1)];
      if (next instanceof Error) throw next;
      return next;
    }),
  } as unknown as EngineApi;
  return { api, calls, workspaces };
}

afterEach(() => {
  vi.useRealTimers();
});

test("the first load fills the store and tells the pickers listening", async () => {
  const { api, calls } = fakeApi({ choices: [row("p", "a"), row("p", "b")], discovery: { p: ready } });
  const heard = vi.fn();
  const stop = subscribeModelChoices(api, heard);
  assert.equal(getModelChoices(api).choices, null);

  await loadModelChoices(api);

  assert.deepEqual(getModelChoices(api), {
    choices: [row("p", "a"), row("p", "b")],
    discovering: [],
    incomplete: [],
    failed: false,
  });
  assert.ok(heard.mock.calls.length >= 1);
  assert.deepEqual(calls, [undefined]); // a plain load never asks for a re-probe
  stop();
});

test("a response without choices does not crash the picker", async () => {
  const { api } = fakeApi({} as Answer);

  await loadModelChoices(api);

  assert.deepEqual(getModelChoices(api).choices, []);
  assert.equal(getModelChoices(api).failed, false);
});

test("a harness still loading is polled every 1.5 s until it is done", async () => {
  vi.useFakeTimers();
  const { api, calls } = fakeApi(
    { choices: [row("p")], discovery: { p: loading } },
    { choices: [row("p", "a")], discovery: { p: loading } },
    { choices: [row("p", "a"), row("p", "b")], discovery: { p: ready } },
  );
  const done = loadModelChoices(api);

  await vi.advanceTimersByTimeAsync(0);
  assert.deepEqual(getModelChoices(api).discovering, ["p"]);
  assert.equal(calls.length, 1);
  await vi.advanceTimersByTimeAsync(POLL_MS - 1);
  assert.equal(calls.length, 1); // not before the interval
  await vi.advanceTimersByTimeAsync(1);
  assert.equal(calls.length, 2);
  await vi.advanceTimersByTimeAsync(POLL_MS);
  await done;

  assert.equal(calls.length, 3);
  assert.deepEqual(getModelChoices(api).discovering, []);
  assert.deepEqual(getModelChoices(api).incomplete, []);
  assert.deepEqual(getModelChoices(api).choices, [row("p", "a"), row("p", "b")]);
});

test("a load that is running is joined, not repeated", async () => {
  const { api, calls } = fakeApi({ choices: [row("p", "a")], discovery: { p: ready } });
  const first = loadModelChoices(api);
  const second = loadModelChoices(api);
  await Promise.all([first, second]);
  assert.equal(calls.length, 1);
});

test("opening again asks again, and the list already held stays up meanwhile", async () => {
  const { api } = fakeApi({ choices: [row("p", "a")], discovery: { p: ready } });
  await loadModelChoices(api);

  let answer!: (value: Answer) => void;
  (api.listModelChoices as ReturnType<typeof vi.fn>).mockImplementationOnce(
    () => new Promise<Answer>((resolve) => (answer = resolve)),
  );
  const revalidating = loadModelChoices(api);
  assert.deepEqual(getModelChoices(api).choices, [row("p", "a")]); // no flash of "Loading models…"
  answer({ choices: [row("p", "a"), row("p", "c")], discovery: { p: ready } });
  await revalidating;
  assert.deepEqual(getModelChoices(api).choices, [row("p", "a"), row("p", "c")]);
});

test("the poll cap turns a harness that never finishes into one that did not load", async () => {
  vi.useFakeTimers();
  const { api, calls } = fakeApi({ choices: [row("p")], discovery: { p: loading, q: { status: "partial" } } });
  const done = loadModelChoices(api);
  await vi.advanceTimersByTimeAsync(POLL_MS * (MAX_POLLS + 2));
  await done;

  assert.equal(calls.length, MAX_POLLS);
  assert.deepEqual(getModelChoices(api).discovering, []);
  assert.deepEqual(getModelChoices(api).incomplete, ["q", "p"]); // stalled first, then the ones still loading
});

test("a failed first load says so, and the next good answer clears it", async () => {
  const { api } = fakeApi(new Error("offline"), { choices: [row("p", "a")], discovery: { p: ready } });
  await loadModelChoices(api);
  assert.equal(getModelChoices(api).failed, true);
  assert.equal(getModelChoices(api).choices, null);

  await loadModelChoices(api);
  assert.equal(getModelChoices(api).failed, false);
  assert.deepEqual(getModelChoices(api).choices, [row("p", "a")]);
});

test("a failed refresh keeps the list the page already holds", async () => {
  const { api } = fakeApi({ choices: [row("p", "a")], discovery: { p: ready } }, new Error("offline"));
  await loadModelChoices(api);
  await loadModelChoices(api);
  assert.deepEqual(getModelChoices(api).choices, [row("p", "a")]);
  assert.equal(getModelChoices(api).failed, false);
  assert.deepEqual(getModelChoices(api).incomplete, []);
});

test("a failure while a harness is loading moves it to the ones that did not load", async () => {
  vi.useFakeTimers();
  const { api } = fakeApi({ choices: [row("p")], discovery: { p: loading } }, new Error("offline"));
  const done = loadModelChoices(api);
  await vi.advanceTimersByTimeAsync(POLL_MS);
  await done;
  assert.deepEqual(getModelChoices(api).discovering, []);
  assert.deepEqual(getModelChoices(api).incomplete, ["p"]);
});

test("Retry asks the engine to re-probe", async () => {
  const { api, calls } = fakeApi({ choices: [row("p", "a")], discovery: { p: ready } });
  await loadModelChoices(api);
  await loadModelChoices(api, { retry: true });
  assert.deepEqual(calls, [undefined, true]);
});

test("a Retry pressed while another harness is still loading rides on the next poll", async () => {
  vi.useFakeTimers();
  const { api, calls } = fakeApi(
    { choices: [row("p"), row("q", "x")], discovery: { p: loading, q: { status: "partial" } } },
    { choices: [row("p", "a"), row("q", "x")], discovery: { p: ready, q: ready } },
  );
  const running = loadModelChoices(api);
  await vi.advanceTimersByTimeAsync(0);
  const joined = loadModelChoices(api, { retry: true });
  await vi.advanceTimersByTimeAsync(POLL_MS);
  await Promise.all([running, joined]);
  assert.deepEqual(calls, [undefined, true]);
});

test("a Retry pressed during a final request still runs after that request settles", async () => {
  let release!: (answer: Answer) => void;
  const calls: Array<boolean | undefined> = [];
  const api = {
    listModelChoices: vi.fn((_workspace?: string, refresh?: boolean) => {
      calls.push(refresh);
      if (refresh) return Promise.resolve({ choices: [row("p", "fresh")], discovery: { p: ready } });
      return new Promise<Answer>((resolve) => { release = resolve; });
    }),
  } as unknown as EngineApi;

  const running = loadModelChoices(api);
  const retry = loadModelChoices(api, { retry: true });
  release({ choices: [row("p", "old")], discovery: { p: { status: "partial" } } });
  await Promise.all([running, retry]);

  assert.deepEqual(calls, [undefined, true]);
  assert.deepEqual(getModelChoices(api).choices, [row("p", "fresh")]);
});

test("profile warm key follows the engine's opaque launch fingerprint", async () => {
  const { profileChoicesKey } = await import("../src/components/shell/model-choices.ts");
  const base = [{ name: "p", provider: "acp", choices_fingerprint: "first" }];
  assert.notEqual(
    profileChoicesKey(base),
    profileChoicesKey([{ ...base[0], choices_fingerprint: "edited-launch" }]),
  );
});

test("an engine restarted with the page open does not blank a harness the page already lists", async () => {
  const { api } = fakeApi(
    { choices: [row("p", "a"), row("p", "b"), row("p", "c"), row("n", "m")], discovery: { p: ready, n: ready } },
    // the restarted engine knows nothing about p yet: one blank row and "loading"; n changed
    { choices: [row("p"), row("n", "m2")], discovery: { p: loading, n: ready } },
    // the engine has probed two of three: still fewer rows than the page holds
    { choices: [row("p", "a"), row("p", "b"), row("n", "m2")], discovery: { p: loading, n: ready } },
    // the final answer always wins
    { choices: [row("p", "a"), row("p", "b"), row("p", "d"), row("n", "m2")], discovery: { p: ready, n: ready } },
  );
  await loadModelChoices(api);

  vi.useFakeTimers();
  const done = loadModelChoices(api);
  await vi.advanceTimersByTimeAsync(0);
  assert.deepEqual(getModelChoices(api).choices, [row("p", "a"), row("p", "b"), row("p", "c"), row("n", "m2")]);
  assert.deepEqual(getModelChoices(api).discovering, ["p"]);
  await vi.advanceTimersByTimeAsync(POLL_MS);
  assert.deepEqual(getModelChoices(api).choices, [row("p", "a"), row("p", "b"), row("p", "c"), row("n", "m2")]);
  await vi.advanceTimersByTimeAsync(POLL_MS);
  await done;
  assert.deepEqual(getModelChoices(api).choices, [row("p", "a"), row("p", "b"), row("p", "d"), row("n", "m2")]);
  assert.deepEqual(getModelChoices(api).discovering, []);
});

test("a changed ACP launch fingerprint drops only that profile's cached rows while loading", async () => {
  const { api } = fakeApi(
    {
      choices: [row("p", "old-a"), row("p", "old-b"), row("native", "n")],
      discovery: { p: ready, native: ready },
    },
    {
      choices: [row("p"), row("native", "n")],
      discovery: { p: loading, native: ready },
    },
    {
      choices: [row("p", "new-a"), row("native", "n")],
      discovery: { p: ready, native: ready },
    },
  );
  const oldProfile = [{ name: "p", provider: "acp", choices_fingerprint: "before" }];
  const editedProfile = [{ name: "p", provider: "acp", choices_fingerprint: "after" }];

  await loadModelChoices(api, { profiles: oldProfile } as never);
  vi.useFakeTimers();
  const refresh = loadModelChoices(api, { profiles: editedProfile } as never);
  await vi.advanceTimersByTimeAsync(0);

  assert.deepEqual(getModelChoices(api).choices, [row("p"), row("native", "n")]);
  assert.deepEqual(getModelChoices(api).discovering, ["p"]);
  await vi.advanceTimersByTimeAsync(POLL_MS);
  await refresh;
});

test("an in-flight answer cannot restore rows after a profile's launch fingerprint changes", async () => {
  const { api } = fakeApi({ choices: [row("p", "old"), row("native", "n")], discovery: { p: ready, native: ready } });
  const oldProfile = [{ name: "p", provider: "acp", choices_fingerprint: "before" }];
  const editedProfile = [{ name: "p", provider: "acp", choices_fingerprint: "after" }];
  await loadModelChoices(api, { profiles: oldProfile } as never);

  let answer!: (value: Answer) => void;
  (api.listModelChoices as ReturnType<typeof vi.fn>).mockImplementationOnce(
    () => new Promise<Answer>((resolve) => (answer = resolve)),
  );
  (api.listModelChoices as ReturnType<typeof vi.fn>).mockImplementationOnce(() =>
    Promise.resolve({ choices: [row("p", "new"), row("native", "n")], discovery: { p: ready, native: ready } }),
  );
  const inFlight = loadModelChoices(api, { profiles: oldProfile } as never);
  const joined = loadModelChoices(api, { profiles: editedProfile } as never);

  assert.deepEqual(getModelChoices(api).choices, [row("native", "n")]);
  answer({ choices: [row("p", "old"), row("native", "n")], discovery: { p: ready, native: ready } });
  await Promise.all([inFlight, joined]);

  assert.deepEqual(getModelChoices(api).choices, [row("p", "new"), row("native", "n")]);
});

test("a first-ever warm shows each batch as it arrives, rows only growing", async () => {
  vi.useFakeTimers();
  const { api } = fakeApi(
    { choices: [row("p")], discovery: { p: loading } },
    { choices: [row("p", "a"), row("p", "b")], discovery: { p: loading } },
    { choices: [row("p", "a"), row("p", "b"), row("p", "c")], discovery: { p: ready } },
  );
  const done = loadModelChoices(api);
  await vi.advanceTimersByTimeAsync(0);
  assert.deepEqual(getModelChoices(api).choices, [row("p")]);
  await vi.advanceTimersByTimeAsync(POLL_MS);
  assert.deepEqual(getModelChoices(api).choices, [row("p", "a"), row("p", "b")]);
  await vi.advanceTimersByTimeAsync(POLL_MS);
  await done;
  assert.equal(getModelChoices(api).choices?.length, 3);
});

test("rows of a deleted profile never show; with no profile list yet every row shows", () => {
  const rows = [row("kept", "a"), row("deleted", "b")];
  assert.deepEqual(rowsForProfiles(rows, [{ name: "kept" }]), [row("kept", "a")]);
  assert.deepEqual(rowsForProfiles(rows, []), rows);
});

test("each folder is asked for by its own trimmed path, and a Retry re-probes only that folder", async () => {
  const { api, calls, workspaces } = fakeApi({ choices: [row("p", "a")], discovery: { p: ready } });
  await loadModelChoices(api);
  await loadModelChoices(api, { workspace: "  /work/a  " });
  await loadModelChoices(api, { workspace: "/work/a", retry: true });

  assert.deepEqual(workspaces, ["", "/work/a", "/work/a"]); // the engine's own folder is the empty string
  assert.deepEqual(calls, [undefined, undefined, true]); // only the folder's own ask carried the re-probe
  assert.equal(getModelChoices(api, "  /work/a  "), getModelChoices(api, "/work/a")); // one list per trimmed path
});

test("a folder's answer never lands in the engine folder's list, nor the other way round", async () => {
  const waiting = new Map<string, (answer: Answer) => void>();
  const api = {
    listModelChoices: vi.fn(
      (workspace = "") =>
        new Promise<Answer>((resolve) => {
          waiting.set(workspace, resolve);
        }),
    ),
  } as unknown as EngineApi;

  const engine = loadModelChoices(api);
  const folder = loadModelChoices(api, { workspace: "/work/a" });
  assert.deepEqual([...waiting.keys()].sort(), ["", "/work/a"]); // two loads: the second did not join the first

  waiting.get("/work/a")?.({ choices: [row("p", "folder-a")], discovery: { p: ready } });
  await folder;
  assert.deepEqual(getModelChoices(api, "/work/a").choices, [row("p", "folder-a")]);
  assert.equal(getModelChoices(api).choices, null); // the engine folder's own answer has not come yet

  waiting.get("")?.({ choices: [row("p", "engine")], discovery: { p: ready } });
  await engine;
  assert.deepEqual(getModelChoices(api).choices, [row("p", "engine")]);
  assert.deepEqual(getModelChoices(api, "/work/a").choices, [row("p", "folder-a")]); // the later answer left it be
});
