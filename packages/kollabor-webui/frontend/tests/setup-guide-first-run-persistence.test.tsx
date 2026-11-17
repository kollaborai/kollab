import { describe, expect, it, vi } from "vitest";

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    useState: (initial: unknown) => [typeof initial === "function" ? (initial as () => unknown)() : initial, () => {}],
    useEffect: (effect: () => void | (() => void)) => { effect(); },
    useMemo: (factory: () => unknown) => factory(),
    useCallback: (callback: unknown) => callback,
    useRef: (initial: unknown) => ({ current: initial }),
  };
});

import { SetupGuide } from "../src/setup/SetupGuide";
import { STORAGE_KEY } from "../src/setup/guide-model";
import type { EngineApi } from "@/api";

const storage = new Map<string, string>();
const localStorage = {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => { storage.set(key, value); },
};
const api = {} as unknown as EngineApi;

describe("SetupGuide first-run persistence", () => {
  it("does not mark a pristine browser as returning just because the guide mounted closed", () => {
    storage.clear();
    vi.stubGlobal("window", { localStorage });

    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    SetupGuide({
      api,
      open: false,
      onOpenChange: () => {},
      onOpenSettings: () => {},
      onStartSession: () => {},
    });

    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
