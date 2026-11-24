import type { ReactElement, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  plugins: [] as unknown[],
  hookIndex: 0,
  memos: [] as Array<{ deps: unknown[]; value: unknown } | undefined>,
  refs: [] as Array<{ current: unknown } | undefined>,
}));

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  const memo = <T,>(create: () => T, deps: readonly unknown[]): T => {
    const index = state.hookIndex++;
    const cached = state.memos[index];
    if (cached && cached.deps.length === deps.length && cached.deps.every((dep, i) => dep === deps[i])) {
      return cached.value as T;
    }
    const value = create();
    state.memos[index] = { deps: [...deps], value };
    return value;
  };

  return {
    ...actual,
    useId: () => "plugin-owner",
    useMemo: memo,
    useCallback: <T,>(callback: T, deps: readonly unknown[]) => memo(() => callback, deps),
    useRef: <T,>(initialValue: T) => {
      const index = state.hookIndex++;
      return (state.refs[index] ??= { current: initialValue }) as { current: T };
    },
  };
});

vi.mock("./registry", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./registry")>();
  return { ...actual, usePlugins: () => ({ plugins: state.plugins }) };
});

import { PluginTaggedText } from "./renderers";
import { EMPTY_CONTRIBUTIONS, type PluginRecord } from "./registry";

function plugin(id: string): PluginRecord {
  return {
    id,
    name: id,
    entry: {} as PluginRecord["entry"],
    status: "ready",
    error: null,
    retries: 0,
    k: {} as PluginRecord["k"],
    contributions: {
      ...EMPTY_CONTRIBUTIONS,
      renderers: [{ tag: "card", render: () => null }],
    },
  };
}

function components() {
  state.hookIndex = 0;
  const element = PluginTaggedText({ text: "<card>x</card>" }) as ReactElement<{
    components: Record<string, unknown>;
  }>;
  return element.props.components;
}

beforeEach(() => {
  state.plugins = [];
  state.hookIndex = 0;
  state.memos = [];
  state.refs = [];
});

describe("tag renderer component identity", () => {
  it("keeps the Markdown anchor type stable while reading the latest registry", () => {
    state.plugins = [plugin("first")];
    const first = components();

    state.plugins = [plugin("second")];
    const second = components();

    expect(second.a).toBe(first.a);
    const renderAnchor = first.a as (props: { href: string; children: ReactNode }) => ReactNode;
    const mounted = renderAnchor({
      href: "https://plugin-tag.kollab.invalid/?owner=plugin-owner&index=0&name=card&content=x",
      children: "x",
    }) as ReactElement<{ record: PluginRecord }>;
    expect(mounted.props.record.id).toBe("second");
  });
});
