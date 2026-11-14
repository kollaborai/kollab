// @vitest-environment jsdom
// Issue #1098: the guided setup never auto-opened on first run because the
// guide's persist effect wrote its storage record on mount, before App's
// startup check looked for it. These tests run in jsdom so real effects and
// real localStorage execute, and they talk to the same helpers App reads.
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { SetupGuide } from "../src/setup/SetupGuide";
import {
  STORAGE_KEY,
  loadGuideState,
  shouldAutoOpenGuide,
} from "../src/setup/guide-model";
import type { EngineApi } from "@/api";

const api = {
  listProfiles: async () => ({ profiles: [], active: undefined }),
  listSessions: async () => null,
  listAgentPool: async () => ({ agents: [] }),
} as unknown as EngineApi;

let container: HTMLElement | null = null;
let root: Root | null = null;

// Owns `open` exactly like App does: the guide's dismissal only survives
// because the host closes the dialog (onOpenChange), so the test harness
// must honor it too.
function GuideHarness({ initialOpen }: { initialOpen: boolean }) {
  const [open, setOpen] = useState(initialOpen);
  return (
    <SetupGuide
      api={api}
      open={open}
      onOpenChange={setOpen}
      onOpenSettings={() => {}}
      onStartSession={() => {}}
    />
  );
}

async function renderGuide(open: boolean) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root!.render(<GuideHarness initialOpen={open} />);
  });
  // Flush passive effects (the persist effect runs after mount).
  await act(async () => {});
}

afterEach(async () => {
  if (root) {
    const unmounting = root;
    await act(async () => unmounting.unmount());
  }
  container?.remove();
  container = null;
  root = null;
  localStorage.clear();
});

describe("guided setup first run (#1098)", () => {
  it("mounting the guide alone never creates the first-run record", async () => {
    await renderGuide(false);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    // No record means the startup check still sees a first run.
    expect(shouldAutoOpenGuide(localStorage.getItem(STORAGE_KEY))).toBe(true);
  });

  it("engaging the tour persists progress", async () => {
    await renderGuide(true);
    const next = document.querySelector(
      '[data-testid="setup-guide-next"]',
    ) as HTMLButtonElement;
    await act(async () => {
      next.click();
    });
    await act(async () => {});
    const raw = localStorage.getItem(STORAGE_KEY);
    expect(JSON.parse(raw!)).toEqual({ step: "provider", dismissed: false });
    // Progress, not a first run: a reload resumes at the provider step.
    expect(shouldAutoOpenGuide(raw)).toBe(false);
    expect(loadGuideState(raw).step).toBe("provider");
  });

  it("a dismissal persists and a reload does not reopen", async () => {
    await renderGuide(true);
    const skip = document.querySelector(
      'button[aria-label="Skip setup"]',
    ) as HTMLButtonElement;
    await act(async () => {
      skip.click();
    });
    await act(async () => {});
    const raw = localStorage.getItem(STORAGE_KEY);
    expect(JSON.parse(raw!).dismissed).toBe(true);
    expect(shouldAutoOpenGuide(raw)).toBe(false);
    // Reload simulation: a fresh mount reads the same record.
    expect(loadGuideState(raw).dismissed).toBe(true);
    await renderGuide(false);
    expect(loadGuideState(localStorage.getItem(STORAGE_KEY)).dismissed).toBe(true);
  });

  it("shouldAutoOpenGuide decides from the record, not just its presence", () => {
    expect(shouldAutoOpenGuide(null)).toBe(true);
    // The record old builds wrote on mount: no user decision in it.
    expect(shouldAutoOpenGuide('{"step":"welcome","dismissed":false}')).toBe(true);
    expect(shouldAutoOpenGuide('{"step":"provider","dismissed":false}')).toBe(false);
    expect(shouldAutoOpenGuide('{"step":"done","dismissed":true}')).toBe(false);
    // A corrupt record must not block the guide forever.
    expect(shouldAutoOpenGuide("not-json{")).toBe(true);
  });
});
