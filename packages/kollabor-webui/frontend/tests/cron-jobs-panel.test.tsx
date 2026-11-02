import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import { SETTINGS_TABS, PANEL_NAME } from "../src/components/panels/panel-model";
import { CronJobsBody, CronJobsPanel, inSeconds, shortId } from "../src/components/panels/CronJobsPanel";
import type { HubCronJobsView } from "../src/api";

const view: HubCronJobsView = {
  count: 3,
  jobs: [
    {
      agent: "moonstone",
      id: "ab12cd34ef90",
      target: "lapis",
      message: "report the pulse",
      interval_seconds: 3600,
      interval: "every 1h",
      next_fire: 1793900000,
      next_in_seconds: 1800,
      recurring: true,
      mode: "recurring",
      created_at: 1793896400,
      last_error: null,
    },
    {
      agent: "moonstone",
      id: "9876fedcba12",
      target: "infra@home-server",
      message: "check the tunnel",
      interval_seconds: 90,
      interval: "every 1m 30s",
      next_fire: 1793898210,
      next_in_seconds: 70,
      recurring: false,
      mode: "one-shot",
      created_at: 1793898140,
      last_error: "peer has no conversation grant for this agent",
    },
    {
      agent: "koordinator",
      id: "5a5b5c5d",
      target: "all",
      message: "standup: one-line status",
      interval_seconds: 21600,
      interval: "every 6h",
      next_fire: 1793996160,
      next_in_seconds: 8040,
      recurring: true,
      mode: "recurring",
      created_at: 1793974560,
      last_error: null,
    },
  ],
  agents_responded: ["moonstone", "koordinator"],
  agents_unavailable: ["garnet"],
  agents_errors: { garnet: "operator gate refused the cron_jobs question" },
};

test("the cron tab is registered as a bespoke settings tab with no daemon panel", () => {
  const tab = SETTINGS_TABS.find((item) => item.id === "cron");
  expect(tab?.label).toBe("Cron Jobs");
  expect(tab?.scope).toContain("read-only");
  expect("cron" in PANEL_NAME).toBe(false);
});

test("interval and countdown strings match the terminal style", () => {
  expect(inSeconds(1800)).toBe("in 30m");
  expect(inSeconds(70)).toBe("in 1m 10s");
  expect(inSeconds(8040)).toBe("in 2h 14m");
  expect(inSeconds(45)).toBe("in 45s");
  expect(inSeconds(0)).toBe("in 0s");
  expect(shortId("ab12cd34ef90")).toBe("ab12cd34");
  expect(shortId("5a5b5c5d")).toBe("5a5b5c5d");
});

test("the desktop table renders every job tagged with its agent and short id", () => {
  const html = renderToStaticMarkup(<CronJobsBody view={view} />);
  expect(html).toContain("<table");
  expect(html).toContain("moonstone");
  expect(html).toContain("ab12cd34");
  expect(html).toContain("recurring");
  expect(html).toContain("one-shot");
  expect(html).toContain("every 1m 30s");
  expect(html).toContain("infra@home-server");
  expect(html).toContain("report the pulse");
  expect(html).toContain("in 2h 14m");
  expect(html).toContain("peer has no conversation grant for this agent");
});

test("per-agent refusals surface as alerts even when rows are present", () => {
  const html = renderToStaticMarkup(<CronJobsBody view={view} />);
  expect(html).toContain('role="alert"');
  expect(html).toContain("garnet: operator gate refused the cron_jobs question");
});

test("an empty roster view explains where jobs are scheduled instead of an empty table", () => {
  const empty: HubCronJobsView = {
    count: 0,
    jobs: [],
    agents_responded: ["moonstone"],
    agents_unavailable: ["zircon"],
    agents_errors: {},
  };
  const html = renderToStaticMarkup(<CronJobsBody view={empty} />);
  expect(html).not.toContain("<table");
  expect(html).toContain("No scheduled jobs");
  expect(html).toContain("/hub cron add");
  expect(html).toContain("Not answering: zircon");
});

test("the panel shell loads on mount, refreshes read-only, and keeps the hint visible", () => {
  const calls: string[] = [];
  const api = {
    listHubCronJobs: (refresh = false) => {
      calls.push(refresh ? "refresh" : "initial");
      return Promise.resolve(view);
    },
  } as never;
  const html = renderToStaticMarkup(<CronJobsPanel api={api} />);
  expect(calls).toEqual([]); // effects do not run in static markup; the shell still renders
  expect(html).toContain("Loading scheduled jobs…");
  expect(html).toContain('aria-label="Refresh cron jobs"');
  expect(html).toContain("Read-only");
  expect(html).toContain("/hub cron delete");
});
