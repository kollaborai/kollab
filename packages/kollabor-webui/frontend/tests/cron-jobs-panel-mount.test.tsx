// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test } from "vitest";
import { CronJobsPanel } from "../src/components/panels/CronJobsPanel";
import { ApiError, type EngineApi } from "../src/api";

// Mounted rather than server-rendered: the load effect has to run and the
// endpoint has to settle before the panel shows what a user sees after a 503.

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

function apiWith(listHubCronJobs: () => Promise<unknown>) {
  return { listHubCronJobs } as unknown as EngineApi;
}

async function mountAndSettle(api: EngineApi) {
  await act(async () => {
    root.render(<CronJobsPanel api={api} />);
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

test("a settled endpoint failure no longer reads as still loading", async () => {
  await mountAndSettle(
    apiWith(() =>
      Promise.reject(new ApiError("cron inspection is temporarily unavailable.", 503, null)),
    ),
  );
  const text = host.textContent ?? "";
  expect(text).not.toContain("Loading scheduled jobs");
  expect(text).toContain("Cron inspection is temporarily unavailable.");
  expect(text).toContain("Scheduled jobs unavailable");
});

test("the loading summary stays up while the first request is still in flight", async () => {
  await mountAndSettle(apiWith(() => new Promise(() => {})));
  const text = host.textContent ?? "";
  expect(text).toContain("Loading scheduled jobs…");
  expect(text).not.toContain("unavailable");
});
