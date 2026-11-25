// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createRoot } from "react-dom/client";
import { act, createElement, type ComponentType, type ReactNode } from "react";
import type { ThreadMessage } from "@assistant-ui/core";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  type ChatModelAdapter, type ChatModelRunResult,
} from "@assistant-ui/react";
import {
  ThreadListItemRuntimeProvider,
  useExternalStoreRuntime,
} from "@assistant-ui/core/react";
import { Thread } from "@/components/assistant-ui/thread";
import { DEFAULT_SLASH_COMMANDS, type SlashCommand } from "@/api";
import { PANEL_COMMAND_NAMES } from "@/components/panels/panel-model";
import {
  EMPTY_CONTRIBUTIONS,
  pluginRegistry,
  type PluginRecord,
} from "./registry";
import type { CommandSpec, PluginApi, PluginEntry } from "./sdk";
import {
  matchPluginCommand,
  pluginCommandConflicts,
  pluginCommandEntries,
  pluginCommandFailure,
  pluginCommandItems,
  reservedCommandKeys,
} from "./composer-commands";

const sendCapture = vi.hoisted(() => ({
  handlers: [] as Array<(event: { preventDefault: () => void }) => void>,
  text: "",
  attachments: [] as unknown[],
  composerState: null as { text: string; attachments: unknown[] } | null,
  composerListeners: new Set<() => void>(),
  slashPaletteIds: [] as string[],
  setTextCalls: 0,
  busySubmit: null as ((event: { preventDefault: () => void; defaultPrevented?: boolean }) => void) | null,
  runtime: null as unknown,
  transportCommands: [] as unknown[],
}));

vi.mock("@assistant-ui/react", async (importOriginal) => {
  const React = await import("react");
  const actual = await importOriginal<typeof import("@assistant-ui/react")>();
  return {
    ...actual,
    useAui: () => {
      const aui = actual.useAui();
      return React.useMemo(() => ({
        ...aui,
        composer: {
          ...aui.composer,
          getState: () => {
            if (
              !sendCapture.composerState ||
              sendCapture.composerState.text !== sendCapture.text ||
              sendCapture.composerState.attachments !== sendCapture.attachments
            ) {
              sendCapture.composerState = {
                text: sendCapture.text,
                attachments: sendCapture.attachments,
              };
            }
            return sendCapture.composerState;
          },
          subscribe: (listener: () => void) => {
            sendCapture.composerListeners.add(listener);
            return () => sendCapture.composerListeners.delete(listener);
          },
          setText: (text: string) => {
            sendCapture.setTextCalls += 1;
            sendCapture.text = text;
            sendCapture.composerState = {
              text,
              attachments: sendCapture.attachments,
            };
            for (const listener of sendCapture.composerListeners) listener();
          },
        },
      }), [aui]);
    },
    useAssistantTransportSendCommand: () => (command: unknown) => {
      sendCapture.transportCommands.push(command);
    },
    ComposerPrimitive: {
      ...actual.ComposerPrimitive,
      Send: ({
        onClick,
        children,
      }: {
        onClick?: (event: { preventDefault: () => void }) => void;
        children?: ReactNode;
      }) => {
        if (onClick) sendCapture.handlers.push(onClick);
        return React.createElement(React.Fragment, null, children);
      },
    },
  };
});

vi.mock("@/components/assistant-ui/composer-palette", async () => {
  const React = await import("react");
  return {
    ComposerPalette: ({ char, items }: { char: "/" | "@"; items: readonly { id: string; label: string }[] }) => {
      if (char === "/") sendCapture.slashPaletteIds = items.map((item) => item.id);
      return React.createElement(
        "div",
        { "data-testid": "composer-palette" },
        ...items.map((item) => React.createElement("span", { key: item.id }, item.label)),
      );
    },
  };
});

vi.mock("@/components/assistant-ui/tooltip-icon-button", async () => {
  const React = await import("react");
  return {
    TooltipIconButton: ({
      children,
      onClick,
      ...props
    }: {
      children?: ReactNode;
      onClick?: (event: { preventDefault: () => void; defaultPrevented?: boolean }) => void;
      "aria-label"?: string;
    }) => {
      if (props["aria-label"] === "Send message while generating") sendCapture.busySubmit = onClick ?? null;
      return React.createElement("button", { ...props, onClick }, children);
    },
  };
});

vi.mock("@/voice/voice-room", () => ({
  useRoomVoiceMode: () => null,
  useVoiceRoomLive: () => false,
  VoiceRoomBar: () => null,
}));

const k = {} as PluginApi;
const run = () => undefined;

afterEach(() => vi.restoreAllMocks());

function command(name: string, description = `${name} command`): CommandSpec {
  return { name, description, run };
}

function record(id: string, commands: CommandSpec[], withK = true, enableOrder?: number): PluginRecord {
  return {
    id,
    name: id,
    entry: {
      ...(enableOrder === undefined ? {} : { enable_order: enableOrder }),
      contributes: { slash: commands.map(({ name }) => name) },
    } as PluginRecord["entry"],
    status: "ready",
    error: null,
    retries: 0,
    k: withK ? k : null,
    contributions: { ...EMPTY_CONTRIBUTIONS, commands },
  };
}

describe("reservedCommandKeys", () => {
  it("reserves native names and aliases without the slash, in lower case", () => {
    const keys = reservedCommandKeys([{ name: "/Config", aliases: ["/CFG"] }, { name: "help" }]);
    expect(keys.has("cfg")).toBe(true);
    expect(keys.has("config")).toBe(true);
    expect(keys.has("help")).toBe(true);
  });

  it("reserves every built-in and panel command when the native catalog is empty", () => {
    const keys = reservedCommandKeys([]);
    for (const command of DEFAULT_SLASH_COMMANDS) {
      expect(keys.has(command.name)).toBe(true);
      for (const alias of command.aliases ?? []) expect(keys.has(alias)).toBe(true);
    }
    for (const name of PANEL_COMMAND_NAMES) expect(keys.has(name)).toBe(true);
  });
});

describe("pluginCommandEntries", () => {
  it("reserves browser panel commands omitted from the engine list", () => {
    const entries = pluginCommandEntries(
      [record("alpha", [command("prompts")])],
      reservedCommandKeys([]),
    );
    expect(entries).toEqual([]);
  });

  it("hides every built-in and panel command even when the catalog is empty", () => {
    const names = [
      ...DEFAULT_SLASH_COMMANDS.flatMap(({ name, aliases }) => [name, ...(aliases ?? [])]),
      ...PANEL_COMMAND_NAMES,
    ];
    const entries = pluginCommandEntries(
      [record("alpha", names.map((name) => command(name)))],
      reservedCommandKeys([]),
    );
    expect(entries).toEqual([]);
  });

  it("skips reserved native names and plugins whose code has not loaded", () => {
    const entries = pluginCommandEntries(
      [record("alpha", [command("config"), command("deploy")]), record("beta", [command("ping")], false)],
      new Set(["config"]),
    );
    expect(entries.map((entry) => entry.name)).toEqual(["deploy"]);
  });

  it("the first plugin by name wins a command name, whatever its case", () => {
    const entries = pluginCommandEntries(
      [record("alpha", [command("deploy", "from alpha")]), record("beta", [command("Deploy", "from beta")])],
      new Set(),
    );
    expect(entries).toHaveLength(1);
    expect(entries[0].pluginId).toBe("alpha");
    expect(entries[0].description).toBe("from alpha");
  });

  it("the first enabled plugin wins even when its name sorts later", () => {
    const entries = pluginCommandEntries(
      [
        record("alpha", [command("deploy", "from alpha")], true, 2),
        record("zulu", [command("deploy", "from zulu")], true, 1),
      ],
      new Set(),
    );
    expect(entries).toHaveLength(1);
    expect(entries[0].pluginId).toBe("zulu");
    expect(entries[0].description).toBe("from zulu");
  });

  it("keeps a declared earlier owner while that plugin is still loading", () => {
    const waiting = record("alpha", [command("deploy")], false, 1);
    const loadedLater = record("zulu", [command("deploy")], true, 2);

    expect(pluginCommandEntries([loadedLater, waiting], new Set())).toEqual([]);
    expect(pluginCommandEntries([loadedLater, record("alpha", [command("deploy")], true, 1)], new Set())
      .map(({ pluginId }) => pluginId)).toEqual(["alpha"]);
    expect(pluginCommandConflicts([loadedLater, waiting], new Set())).toEqual([
      {
        pluginId: "zulu",
        pluginName: "zulu",
        commandName: "deploy",
        winnerPluginId: "alpha",
        winnerName: "alpha",
      },
    ]);
  });

  it("preserves API order when enable order is missing", () => {
    const entries = pluginCommandEntries(
      [record("beta", [command("deploy", "from beta")]), record("alpha", [command("deploy", "from alpha")])],
      new Set(),
    );
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ pluginId: "beta", description: "from beta" });
  });

  it("preserves API order when enable order is tied", () => {
    const entries = pluginCommandEntries(
      [
        record("beta", [command("deploy", "from beta")], true, 1),
        record("alpha", [command("deploy", "from alpha")], true, 1),
      ],
      new Set(),
    );
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ pluginId: "beta", description: "from beta" });
  });
});

describe("pluginCommandConflicts", () => {
  it("records the losing plugin and first-enabled winner for diagnostics", async () => {
    const { pluginCommandConflicts } = (await import("./composer-commands")) as unknown as {
      pluginCommandConflicts: (
        plugins: readonly PluginRecord[],
        reserved: ReadonlySet<string>,
      ) => Array<{
        pluginId: string;
        pluginName: string;
        commandName: string;
        winnerPluginId: string;
        winnerName: string;
      }>;
    };
    expect(
      pluginCommandConflicts(
        [
          record("alpha", [command("deploy")], true, 1),
          record("zulu", [command("deploy")], true, 2),
        ],
        new Set(),
      ),
    ).toEqual([
      {
        pluginId: "zulu",
        pluginName: "zulu",
        commandName: "deploy",
        winnerPluginId: "alpha",
        winnerName: "alpha",
      },
    ]);
  });

  it("warns with the command, losing plugin and winner", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => {});
    const module = (await import("./composer-commands")) as unknown as {
      warnPluginCommandConflicts: (
        conflicts: ReturnType<typeof pluginCommandConflicts>,
      ) => void;
    };
    expect(module.warnPluginCommandConflicts).toBeTypeOf("function");
    if (typeof module.warnPluginCommandConflicts !== "function") return;

    module.warnPluginCommandConflicts(
      pluginCommandConflicts(
        [
          record("winner", [command("deploy")], true, 1),
          record("loser", [command("deploy")], true, 2),
        ],
        new Set(),
      ),
    );

    expect(warning).toHaveBeenCalledOnce();
    const message = String(warning.mock.calls[0]?.[0] ?? "");
    expect(message).toContain("/deploy");
    expect(message).toContain("loser");
    expect(message).toContain("winner");
  });
});

describe("matchPluginCommand", () => {
  const entries = pluginCommandEntries([record("hello", [command("hello"), command("hello-world")])], new Set());

  it("splits the command name from its arguments", () => {
    const match = matchPluginCommand("/hello there friend", entries);
    expect(match?.entry.name).toBe("hello");
    expect(match?.args).toBe("there friend");
  });

  it("matches a bare command with empty arguments, ignoring surrounding space", () => {
    expect(matchPluginCommand("  /hello  ", entries)?.args).toBe("");
  });

  it("matches the whole name only, so a longer name is not taken for a shorter one", () => {
    expect(matchPluginCommand("/hello-world", entries)?.entry.name).toBe("hello-world");
    expect(matchPluginCommand("/hello-", entries)).toBeNull();
  });

  it("matches the name in any case", () => {
    expect(matchPluginCommand("/HELLO", entries)?.entry.name).toBe("hello");
  });

  it("ignores text that is not a command", () => {
    expect(matchPluginCommand("hello", entries)).toBeNull();
    expect(matchPluginCommand("say /hello", entries)).toBeNull();
    expect(matchPluginCommand("/unknown", entries)).toBeNull();
  });
});

describe("pluginCommandItems", () => {
  it("lists each plugin command as a palette item in the plugins category that inserts its name", () => {
    const entries = pluginCommandEntries([record("hello", [command("hello", "Say hello")])], new Set());
    const [item] = pluginCommandItems(entries, (name) => `Label ${name}`);
    expect(item).toMatchObject({
      id: "hello",
      label: "Label hello",
      description: "Say hello",
      type: "command",
      category: "plugins",
      insertText: "hello",
    });
  });
});

describe("pluginCommandFailure", () => {
  it.each([
    ["Error", new Error("GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz")],
    ["string", "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz"],
    ["empty Error", new Error("  ")],
  ])("keeps %s command failures fixed and safe", (_kind, error) => {
    expect(pluginCommandFailure("hello", error)).toBe(
      "Could not run /hello. Check the browser console for redacted details.",
    );
  });
});

describe("plugin command draft restoration", () => {
  it("requires an unchanged composer revision and the same session", async () => {
    const module = (await import("./composer-commands")) as unknown as {
      canRestorePluginCommandDraft?: (
        currentRevision: number,
        clearedRevision: number,
        currentSession: unknown,
        commandSession: unknown,
      ) => boolean;
    };
    expect(module.canRestorePluginCommandDraft).toBeTypeOf("function");
    if (!module.canRestorePluginCommandDraft) return;

    expect(module.canRestorePluginCommandDraft(4, 4, "session-a", "session-a")).toBe(true);
    expect(module.canRestorePluginCommandDraft(5, 4, "session-a", "session-a")).toBe(false);
    expect(module.canRestorePluginCommandDraft(4, 4, "session-b", "session-a")).toBe(false);
  });
});

function publishComposerText(text: string) {
  sendCapture.text = text;
  sendCapture.composerState = { text, attachments: sendCapture.attachments };
  for (const listener of sendCapture.composerListeners) listener();
}

function prepareMountedThread() {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
}

const threadAdapter: ChatModelAdapter = { run: async () => ({ content: [] }) };

function putThreadCommand(name: string, run = () => {}, toast = vi.fn()) {
  pluginRegistry.put({
    id: "punctuation",
    name: "Punctuation",
    entry: { contributes: { slash: [name] } } as PluginEntry,
    status: "ready",
    error: null,
    retries: 0,
    k: { id: "punctuation", toast } as unknown as PluginApi,
    contributions: {
      ...EMPTY_CONTRIBUTIONS,
      commands: [{ name, description: "A punctuation command", run }],
    },
  });
  return toast;
}

function LocalThreadHarness({
  commands,
  commandCatalogStatus,
  draftKey,
}: {
  commands: SlashCommand[];
  commandCatalogStatus?: "loading" | "ready" | "empty" | "unavailable" | "fallback";
  draftKey?: string | null;
}) {
  const runtime = useLocalRuntime(threadAdapter);
  sendCapture.runtime = runtime;
  return createElement(
    AssistantRuntimeProvider,
    { runtime },
    createElement(Thread, { agents: [], commands, commandCatalogStatus, draftKey, attachmentsEnabled: false }),
  );
}

const ThreadHarness: ComponentType<{
  commands?: SlashCommand[];
  commandCatalogStatus?: "loading" | "ready" | "empty" | "unavailable" | "fallback";
  draftKey?: string | null;
}> = ({ commands = [], commandCatalogStatus, draftKey }) => {
  const host = useExternalStoreRuntime<ThreadMessage>({
    messages: [],
    onNew: async () => {},
  });
  return createElement(
    AssistantRuntimeProvider,
    { runtime: host },
    createElement(
      ThreadListItemRuntimeProvider,
      { runtime: host.threads.mainItem },
      createElement(LocalThreadHarness, { commands, commandCatalogStatus, draftKey }),
    ),
  );
};

afterEach(() => pluginRegistry.reset());
afterEach(() => {
  vi.unstubAllGlobals();
  sendCapture.handlers.length = 0;
  sendCapture.composerListeners.clear();
  sendCapture.text = "";
  sendCapture.attachments.length = 0;
  sendCapture.composerState = null;
  sendCapture.slashPaletteIds = [];
  sendCapture.setTextCalls = 0;
  sendCapture.busySubmit = null;
  sendCapture.runtime = null;
  sendCapture.transportCommands.length = 0;
});

describe("plugin command composer labels", () => {
  it("keeps a label when a command name contains only separators", () => {
    putThreadCommand("__");

    const html = renderToStaticMarkup(createElement(ThreadHarness));

    expect(html).toContain(">__</span>");
  });

  it.each(["throws", "rejects", "returns a thenable"] as const)(
    "restores a plugin command draft when its handler %s",
    async (failure) => {
      const draft = "/publish now";
      const secret = "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz";
      const error = new Error(secret);
      const log = vi.spyOn(console, "error").mockImplementation(() => {});
      sendCapture.text = draft;
      const execute = vi.fn(() => {
        if (failure === "throws") throw error;
        if (failure === "rejects") return Promise.reject(error);
        return { then: (_resolve: unknown, reject: (reason: unknown) => void) => reject(error) };
      });
      const toast = putThreadCommand("publish", execute);

      renderToStaticMarkup(createElement(ThreadHarness));
      const beforeSend = sendCapture.handlers.at(-1);
      expect(sendCapture.handlers).not.toHaveLength(0);
      expect(beforeSend).toBeTypeOf("function");
      let prevented = false;
      beforeSend?.({ preventDefault: () => { prevented = true; } });
      expect(prevented).toBe(true);
      expect(execute).toHaveBeenCalledWith("now");
      if (failure !== "throws") await new Promise((resolve) => setTimeout(resolve, 0));

      expect(sendCapture.text).toBe(draft);
      expect(sendCapture.setTextCalls).toBe(2);
      expect(toast).toHaveBeenCalledWith(
        "Could not run /publish. Check the browser console for redacted details.",
      );
      expect(log.mock.calls.flat().join(" ")).toContain("[redacted]");
      expect(log.mock.calls.flat().join(" ")).not.toContain(secret);
    },
  );

  it("does not restore a rejected command over a composer changed after it was cleared", async () => {
    prepareMountedThread();
    const draft = "/publish now";
    let reject!: (error: unknown) => void;
    const pending = new Promise<void>((_resolve, fail) => { reject = fail; });
    void pending.catch(() => {});
    const execute = vi.fn(() => pending);
    sendCapture.text = "";
    const toast = putThreadCommand("publish", execute);

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => root.render(createElement(ThreadHarness, { draftKey: "session-a" })));
      publishComposerText(draft);
      sendCapture.setTextCalls = 0;
      const submit = sendCapture.handlers.at(-1);
      expect(submit).toBeTypeOf("function");
      await act(async () => submit?.({ preventDefault: () => {} }));
      expect(sendCapture.text).toBe("");

      // Another message was edited and cleared while the plugin command was pending.
      publishComposerText("another message");
      publishComposerText("");
      await act(async () => {
        reject(new Error("late rejection"));
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      expect(sendCapture.text).toBe("");
      expect(sendCapture.setTextCalls).toBe(1);
      expect(toast).toHaveBeenCalledWith("Could not run /publish. Check the browser console for redacted details.");
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });

  it("ignores a command rejection after its plugin unloads", async () => {
    prepareMountedThread();
    let reject!: (error: unknown) => void;
    const pending = new Promise<void>((_resolve, fail) => { reject = fail; });
    void pending.catch(() => {});
    const toast = putThreadCommand("publish", () => pending);
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    try {
      await act(async () => root.render(createElement(ThreadHarness, { draftKey: "session-a" })));
      publishComposerText("/publish now");
      const submit = sendCapture.handlers.at(-1);
      expect(submit).toBeTypeOf("function");
      await act(async () => submit?.({ preventDefault: () => {} }));
      expect(sendCapture.text).toBe("");

      pluginRegistry.remove("punctuation");
      await act(async () => {
        reject(new Error("late rejection"));
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(sendCapture.text).toBe("");
      expect(toast).not.toHaveBeenCalled();
      expect(log).not.toHaveBeenCalled();
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});

describe("native command reservations", () => {
  it.each(["loading", "unavailable", "fallback"] as const)(
    "keeps plugin commands out of submit and the palette while the catalog is %s",
    (commandCatalogStatus) => {
      const execute = vi.fn();
      sendCapture.text = "/exit";
      putThreadCommand("exit", execute);

      renderToStaticMarkup(createElement(ThreadHarness, { commands: [], commandCatalogStatus }));
      const submit = sendCapture.handlers.at(-1);
      expect(submit).toBeTypeOf("function");
      let prevented = false;
      submit?.({ preventDefault: () => { prevented = true; } });

      expect(execute).not.toHaveBeenCalled();
      expect(sendCapture.text).toBe("/exit");
      expect(prevented).toBe(false);
      expect(sendCapture.slashPaletteIds).not.toContain("exit");
    },
  );

  it("reserves native aliases from the loaded catalog", () => {
    const execute = vi.fn();
    sendCapture.text = "/exit";
    putThreadCommand("exit", execute);

    renderToStaticMarkup(createElement(ThreadHarness, {
      commands: [{ name: "quit", aliases: ["exit"], description: "Exit" }],
      commandCatalogStatus: "ready",
    }));
    const submit = sendCapture.handlers.at(-1);
    let prevented = false;
    submit?.({ preventDefault: () => { prevented = true; } });

    expect(execute).not.toHaveBeenCalled();
    expect(prevented).toBe(false);
    expect(sendCapture.slashPaletteIds).not.toContain("exit");
  });

  it("queues a native-looking slash from the busy submit path until the catalog is ready", async () => {
    prepareMountedThread();
    let resolveRun!: (value: ChatModelRunResult) => void;
    const activeRun = new Promise<ChatModelRunResult>((resolve) => { resolveRun = resolve; });
    const runAdapter = vi.spyOn(threadAdapter, "run").mockReturnValue(activeRun);
    const execute = vi.fn();
    putThreadCommand("exit", execute);

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => root.render(createElement(ThreadHarness, {
        commands: [],
        commandCatalogStatus: "loading",
      })));
      const runtime = sendCapture.runtime as {
        thread: {
          composer: { setText(text: string): void; send(): void };
          getState(): { isRunning: boolean };
        };
      };
      await act(async () => {
        runtime.thread.composer.setText("start a turn");
        runtime.thread.composer.send();
        await Promise.resolve();
      });
      expect(runtime.thread.getState().isRunning).toBe(true);
      expect(sendCapture.busySubmit).toBeTypeOf("function");

      publishComposerText("/exit");
      let prevented = false;
      await act(async () => {
        sendCapture.busySubmit?.({ preventDefault: () => { prevented = true; } });
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      expect(execute).not.toHaveBeenCalled();
      expect(prevented).toBe(true);
      expect(sendCapture.transportCommands).toContainEqual(expect.objectContaining({
        type: "add-message",
        message: { role: "user", parts: [{ type: "text", text: "/exit" }] },
      }));
    } finally {
      resolveRun({ content: [] });
      await act(async () => root.unmount());
      host.remove();
      runAdapter.mockRestore();
    }
  });
});

describe("slash catalog notice", () => {
  const statuses = ["loading", "empty", "fallback", "unavailable"] as const;

  it("says nothing when the catalog is ready", async () => {
    const { slashCatalogNotice } = await import("@/components/assistant-ui/slash-catalog-notice");

    expect(slashCatalogNotice("ready", false)).toBeNull();
    expect(slashCatalogNotice("ready", true)).toBeNull();
  });

  it("does not claim no commands are available while a plugin command is listed", async () => {
    const { slashCatalogNotice } = await import("@/components/assistant-ui/slash-catalog-notice");

    expect(slashCatalogNotice("empty", true)).toBeNull();
    expect(slashCatalogNotice("empty", false)).toBe(
      "No commands are available for this session. You can still type a slash command.",
    );
  });

  it.each(statuses.filter((status) => status !== "empty"))(
    "keeps the %s message whether or not plugin commands exist",
    async (status) => {
      const { slashCatalogNotice } = await import("@/components/assistant-ui/slash-catalog-notice");

      const message = slashCatalogNotice(status, false);
      expect(message).toBeTruthy();
      expect(slashCatalogNotice(status, true)).toBe(message);
    },
  );
});
