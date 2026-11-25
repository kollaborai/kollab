// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test } from "vitest";
import {
  SchedulesSection,
  formatEvery,
  parseEvery,
} from "../src/components/panels/SchedulesPanel";
import type { EngineApi, HubSchedule, HubScheduleFields } from "../src/api";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function row(over: Partial<HubSchedule> = {}): HubSchedule {
  return {
    id: "ab12cd34",
    agents: ["lapis"],
    prompt: "report the pulse",
    interval_seconds: 3600,
    anchor: 1000,
    active: true,
    when_busy: "queue",
    skill: "",
    created_by: "web",
    created_at: 900,
    updated_at: 900,
    revision: 1,
    last_run: null,
    ...over,
  };
}

type Call = [string, ...unknown[]];

function stubApi(rows: HubSchedule[], fail = "") {
  const calls: Call[] = [];
  const api = {
    listSchedules: () => Promise.resolve({ schedules: rows, file: "~/.kollab/schedules.json" }),
    createSchedule: (fields: HubScheduleFields) => {
      calls.push(["create", fields]);
      return fail ? Promise.reject(new Error(fail)) : Promise.resolve({ schedule: row(), via: "local" });
    },
    updateSchedule: (id: string, fields: HubScheduleFields) => {
      calls.push(["update", id, fields]);
      return fail ? Promise.reject(new Error(fail)) : Promise.resolve({ schedule: row(), via: "local" });
    },
    deleteSchedule: (id: string) => {
      calls.push(["delete", id]);
      return Promise.resolve({ ok: true, via: "local" });
    },
  } as unknown as EngineApi;
  return { api, calls };
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mount(api: EngineApi) {
  await act(async () => {
    root.render(<SchedulesSection api={api} />);
  });
  await settle();
}

function button(name: string) {
  const found = [...host.querySelectorAll("button")].find(
    (el) => el.textContent?.trim() === name || el.getAttribute("aria-label") === name,
  );
  expect(found, `button ${name}`).toBeTruthy();
  return found as HTMLButtonElement;
}

async function click(el: HTMLElement) {
  await act(async () => el.click());
  await settle();
}

function setValue(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function field(label: string) {
  const el = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | null;
  expect(el, `field ${label}`).toBeTruthy();
  return el!;
}

test("parseEvery reads 30s, 5m, 1h and 2h30m and refuses the rest", () => {
  expect(parseEvery("30s")).toBe(30);
  expect(parseEvery("5m")).toBe(300);
  expect(parseEvery("1h")).toBe(3600);
  expect(parseEvery("2h30m")).toBe(9000);
  expect(parseEvery(" 1d ")).toBe(86400);
  expect(parseEvery("")).toBeNull();
  expect(parseEvery("soon")).toBeNull();
  expect(parseEvery("0m")).toBeNull();
});

test("formatEvery writes the shortest form parseEvery reads back", () => {
  expect(formatEvery(30)).toBe("30s");
  expect(formatEvery(3600)).toBe("1h");
  expect(formatEvery(9000)).toBe("2h30m");
  expect(parseEvery(formatEvery(9000))).toBe(9000);
});

test("the list shows each schedule and the detail opens on click", async () => {
  const { api } = stubApi([
    row(),
    row({ id: "ff00ff00", agents: ["a", "b", "c"], prompt: "standup", active: false, interval_seconds: 21600 }),
  ]);
  await mount(api);
  expect(host.textContent).toContain("report the pulse");
  expect(host.textContent).toContain("3 agents");
  expect(host.textContent).toContain("every 6h");
  expect(host.textContent).toContain("Paused");
  await click(button("3 agents every 6h standup Paused"));
  expect(button("Resume")).toBeTruthy();
  expect(button("Edit")).toBeTruthy();
  expect(button("Delete")).toBeTruthy();
});

test("Pause sends active false for the selected schedule", async () => {
  const { api, calls } = stubApi([row()]);
  await mount(api);
  await click(button("Pause"));
  expect(calls).toEqual([["update", "ab12cd34", { active: false }]]);
});

test("Edit saves the changed fields", async () => {
  const { api, calls } = stubApi([row()]);
  await mount(api);
  await click(button("Edit"));
  expect(field("Prompt").value).toBe("report the pulse");
  expect(field("Every").value).toBe("1h");
  expect(field("Runs in").value).toBe("lapis");
  setValue(field("Prompt"), "report the pulse twice");
  setValue(field("Every"), "30m");
  setValue(field("Runs in"), "lapis, infra@devbox");
  await click(button("Open a new instance"));
  await click(button("Save schedule"));
  expect(calls).toEqual([
    [
      "update",
      "ab12cd34",
      {
        prompt: "report the pulse twice",
        interval_seconds: 1800,
        agents: ["lapis", "infra@devbox"],
        when_busy: "new_instance",
        active: true,
        skill: "",
      },
    ],
  ]);
});

test("Add schedule creates a new one", async () => {
  const { api, calls } = stubApi([]);
  await mount(api);
  expect(host.textContent).toContain("No schedules yet");
  await click(button("Add schedule"));
  setValue(field("Prompt"), "check the tunnel");
  setValue(field("Every"), "5m");
  setValue(field("Runs in"), "infra@devbox");
  await click(button("Save schedule"));
  expect(calls).toEqual([
    [
      "create",
      {
        prompt: "check the tunnel",
        interval_seconds: 300,
        agents: ["infra@devbox"],
        when_busy: "queue",
        active: true,
        skill: "",
      },
    ],
  ]);
});

test("a bad Every or empty fields block Save with a message", async () => {
  const { api, calls } = stubApi([]);
  await mount(api);
  await click(button("Add schedule"));
  setValue(field("Prompt"), "x");
  setValue(field("Every"), "soon");
  setValue(field("Runs in"), "lapis");
  await click(button("Save schedule"));
  expect(calls).toEqual([]);
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("Every");
});

test("Delete asks once more before deleting", async () => {
  const { api, calls } = stubApi([row()]);
  await mount(api);
  await click(button("Delete"));
  expect(calls).toEqual([]);
  await click(button("Confirm delete"));
  expect(calls).toEqual([["delete", "ab12cd34"]]);
});

test("a failed save shows the engine's reason", async () => {
  const { api } = stubApi([row()], "No hub coordinator took the change");
  await mount(api);
  await click(button("Pause"));
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("No hub coordinator");
});
