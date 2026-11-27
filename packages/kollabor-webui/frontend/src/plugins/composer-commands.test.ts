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
import { ComposerLockContext, Thread } from "@/components/assistant-ui/thread";
import { ChatStoreProvider, type ChatSnapshot, type LiveStore } from "@/chat-store";
import type { InputRequest } from "@/chat-stream";
import { DEFAULT_SLASH_COMMANDS, type SlashCommand } from "@/api";
import { PANEL_COMMAND_NAMES } from "@/components/panels/panel-model";
import { loadComposerDraft, saveComposerDraft } from "../composer-draft";
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

if (typeof HTMLElement !== "undefined" && typeof HTMLElement.prototype.scrollTo !== "function") {
  Object.defineProperty(HTMLElement.prototype, "scrollTo", { configurable: true, value: () => {} });
}

const sendCapture = vi.hoisted(() => ({
  handlers: [] as Array<(event: { preventDefault: () => void }) => void>,
  submitHandlers: [] as Array<(event: { preventDefault: () => void; defaultPrevented?: boolean }) => void>,
  text: "",
  attachments: [] as unknown[],
  composerState: null as { text: string; attachments: unknown[] } | null,
  composerListeners: new Set<() => void>(),
  syncComposerState: false,
  slashPaletteIds: [] as string[],
  setTextCalls: 0,
  busySubmit: null as ((event: { preventDefault: () => void; defaultPrevented?: boolean }) => void) | null,
  sendWaitingNow: null as (() => void) | null,
  runtime: null as unknown,
  transportCommands: [] as unknown[],
  queueRequests: [] as Array<{ text: string; sendNow: boolean }>,
  newMessages: 0,
  modelRuns: 0,
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
            if (sendCapture.syncComposerState) aui.composer.setText(text);
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
      Root: (props: Record<string, unknown>) => {
        const onSubmit = props.onSubmit as
          | ((event: { preventDefault: () => void; defaultPrevented?: boolean }) => void)
          | undefined;
        if (onSubmit) sendCapture.submitHandlers.push(onSubmit);
        return React.createElement(
          actual.ComposerPrimitive.Root as unknown as React.ComponentType<Record<string, unknown>>,
          props,
        );
      },
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

// The thread sends through the chat store. This fake records each send the way the
// tests count them: a send reaches the agent (modelRuns) and is listed in queueRequests;
// Send Now marks that entry. `running` stands in for a turn the daemon is running.
const chatStoreState = vi.hoisted(() => ({
  running: false,
  listeners: new Set<() => void>(),
}));
function fakeChatStore(): LiveStore {
  let snap = {
    rows: [], inputs: {}, running: chatStoreState.running, connection: "live",
    hasEarlier: false, olderStatus: "idle", goalReplies: {},
  } as unknown as ChatSnapshot;
  return {
    subscribe: (listener: () => void) => {
      chatStoreState.listeners.add(listener);
      return () => chatStoreState.listeners.delete(listener);
    },
    getSnapshot: () => {
      if (snap.running !== chatStoreState.running) snap = { ...snap, running: chatStoreState.running };
      return snap;
    },
    send: vi.fn(async (input: InputRequest) => {
      sendCapture.modelRuns += 1;
      sendCapture.queueRequests.push({ text: input.text, sendNow: false });
    }),
    sendNow: vi.fn(async () => {
      const last = sendCapture.queueRequests.at(-1);
      if (last) last.sendNow = true;
    }),
    cancel: vi.fn(async () => {}),
    stop: vi.fn(async () => {}),
    loadOlder: vi.fn(async () => {}),
    retry: vi.fn(),
    checkGoalReply: vi.fn(),
    start: vi.fn(),
    dispose: vi.fn(),
  } as unknown as LiveStore;
}
/** Lets the composer's async send reach the fake store. */
async function flushSends() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}
function sentToAgent(text: string): boolean {
  return sendCapture.queueRequests.some((request) => request.text === text);
}
function setStoreRunning(running: boolean) {
  chatStoreState.running = running;
  chatStoreState.listeners.forEach((listener) => listener());
}

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
      web: { entry: "web/dist/index.js", style: null, hash: "test" },
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

  it("F4 lets the next web-capable plugin own a slash command", () => {
    const noWeb = record("alpha", [command("publish")], false, 1);
    noWeb.entry = { ...noWeb.entry, web: null };
    const capable = record("beta", [command("publish")], true, 2);

    expect(pluginCommandEntries([noWeb, capable], new Set()).map(({ pluginId }) => pluginId)).toEqual(["beta"]);
    expect(pluginCommandConflicts([noWeb, capable], new Set())).toEqual([]);
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

const threadAdapter: ChatModelAdapter = {
  run: async () => {
    sendCapture.modelRuns += 1;
    return { content: [] };
  },
};

function putThreadCommand(name: string, run = () => {}, toast = vi.fn()) {
  pluginRegistry.put({
    id: "punctuation",
    name: "Punctuation",
    entry: {
      web: { entry: "web/dist/index.js", style: null, hash: "test" },
      contributes: { slash: [name] },
    } as PluginEntry,
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

function putLoadingThreadCommand(name: string, status: PluginRecord["status"] = "loading") {
  pluginRegistry.put({
    id: "punctuation",
    name: "Punctuation",
    entry: {
      web: { entry: "web/dist/index.js", style: null, hash: "test" },
      contributes: { slash: [name] },
    } as PluginEntry,
    status,
    error: null,
    retries: 0,
    k: null,
    contributions: EMPTY_CONTRIBUTIONS,
  });
}

function LocalThreadHarness({
  commands,
  commandCatalogStatus,
  draftKey,
  locked,
}: {
  commands: SlashCommand[];
  commandCatalogStatus?: "loading" | "ready" | "empty" | "unavailable" | "fallback";
  draftKey?: string | null;
  locked?: string;
}) {
  const runtime = useLocalRuntime(threadAdapter);
  sendCapture.runtime = runtime;
  return createElement(
    AssistantRuntimeProvider,
    { runtime },
    createElement(
      ComposerLockContext.Provider,
      { value: locked ?? "" },
      createElement(Thread, {
        agents: [],
        commands,
        commandCatalogStatus,
        draftKey,
        attachmentsEnabled: false,
      }),
    ),
  );
}

// One fake chat store per engine session: a replacement session is a new store, as in the app.
const storesBySession = new Map<string, LiveStore>();

const ThreadHarness: ComponentType<{
  commands?: SlashCommand[];
  commandCatalogStatus?: "loading" | "ready" | "empty" | "unavailable" | "fallback";
  draftKey?: string | null;
  queueSessionId?: string | null;
  locked?: string;
}> = ({ commands = [], commandCatalogStatus, draftKey, queueSessionId, locked }) => {
  const host = useExternalStoreRuntime<ThreadMessage>({
    messages: [],
    onNew: async () => { sendCapture.newMessages += 1; },
  });
  const sessionKey = queueSessionId ?? "default";
  let store = storesBySession.get(sessionKey);
  if (!store) {
    store = fakeChatStore();
    storesBySession.set(sessionKey, store);
  }
  return createElement(
    ChatStoreProvider,
    { store },
    createElement(
      AssistantRuntimeProvider,
      { runtime: host },
      createElement(
        ThreadListItemRuntimeProvider,
        { runtime: host.threads.mainItem },
        createElement(LocalThreadHarness, { commands, commandCatalogStatus, draftKey, locked }),
      ),
    ),
  );
};

afterEach(() => pluginRegistry.reset());
afterEach(() => {
  vi.unstubAllGlobals();
  sendCapture.handlers.length = 0;
  sendCapture.submitHandlers.length = 0;
  sendCapture.composerListeners.clear();
  sendCapture.text = "";
  sendCapture.attachments.length = 0;
  sendCapture.composerState = null;
  sendCapture.syncComposerState = false;
  sendCapture.slashPaletteIds = [];
  sendCapture.setTextCalls = 0;
  sendCapture.busySubmit = null;
  sendCapture.sendWaitingNow = null;
  sendCapture.runtime = null;
  sendCapture.transportCommands.length = 0;
  sendCapture.queueRequests.length = 0;
  sendCapture.newMessages = 0;
  sendCapture.modelRuns = 0;
  storesBySession.clear();
  chatStoreState.running = false;
  chatStoreState.listeners.clear();
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
      const githubToken = "ghp_abcdefghijklmnopqrstuvwxyz";
      const databasePassword = "correct-horse-7";
      const secret = `GITHUB_TOKEN=${githubToken}; DB unavailable: postgres://alice:${databasePassword}@db.internal/kollab`;
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
      expect(log.mock.calls.flat().join(" ")).not.toContain(githubToken);
      expect(log.mock.calls.flat().join(" ")).not.toContain(databasePassword);
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

  it("preserves the rejected command draft in its session across another session", async () => {
    prepareMountedThread();
    sendCapture.syncComposerState = true;
    const sessionA = "plugin-command-draft-session-a";
    const sessionB = "plugin-command-draft-session-b";
    const draftA = "/publish from session a";
    const draftB = "draft owned by session b";
    saveComposerDraft(sessionA, draftA);
    saveComposerDraft(sessionB, draftB);
    let reject!: (error: unknown) => void;
    const pending = new Promise<void>((_resolve, fail) => { reject = fail; });
    void pending.catch(() => {});
    const toast = putThreadCommand("publish", () => pending);
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    try {
      await act(async () => root.render(createElement(ThreadHarness, { draftKey: sessionA })));
      expect(sendCapture.text).toBe(draftA);
      const submit = sendCapture.handlers.at(-1);
      expect(submit).toBeTypeOf("function");
      await act(async () => submit?.({ preventDefault: () => {} }));
      expect(sendCapture.text).toBe("");
      expect(loadComposerDraft(sessionA)).toBe(draftA);

      await act(async () => root.render(createElement(ThreadHarness, { draftKey: sessionB })));
      expect(sendCapture.text).toBe(draftB);
      await act(async () => {
        reject(new Error("late rejection"));
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      expect(sendCapture.text).toBe(draftB);
      expect(loadComposerDraft(sessionB)).toBe(draftB);
      await act(async () => root.render(createElement(ThreadHarness, { draftKey: sessionA })));
      expect(sendCapture.text).toBe(draftA);
      expect(loadComposerDraft(sessionA)).toBe(draftA);
      expect(toast).toHaveBeenCalledWith(
        "Could not run /publish. Check the browser console for redacted details.",
      );
    } finally {
      await act(async () => root.unmount());
      host.remove();
      saveComposerDraft(sessionA, "");
      saveComposerDraft(sessionB, "");
      log.mockRestore();
    }
  });

  it.each([
    ["the same session", false],
    ["returning from another session", true],
  ] as const)(
    "preserves a newer identical draft after command success while %s",
    async (_timing, switchSession) => {
      prepareMountedThread();
      sendCapture.syncComposerState = true;
      const sessionA = `plugin-command-draft-success-a-${switchSession}`;
      const sessionB = `plugin-command-draft-success-b-${switchSession}`;
      const draft = "/publish now";
      saveComposerDraft(sessionA, "");
      saveComposerDraft(sessionB, "draft owned by session b");
      let resolve!: () => void;
      const pending = new Promise<void>((done) => { resolve = done; });
      putThreadCommand("publish", () => pending);
      const host = document.createElement("div");
      document.body.append(host);
      const root = createRoot(host);

      try {
        await act(async () => root.render(createElement(ThreadHarness, { draftKey: sessionA })));
        publishComposerText(draft);
        const submit = sendCapture.handlers.at(-1);
        expect(submit).toBeTypeOf("function");
        await act(async () => submit?.({ preventDefault: () => {} }));
        expect(sendCapture.text).toBe("");
        expect(loadComposerDraft(sessionA)).toBe(draft);

        // The composer autosaves a newly typed generation with the same text.
        publishComposerText(draft);
        saveComposerDraft(sessionA, draft);
        if (switchSession) {
          await act(async () => root.render(createElement(ThreadHarness, { draftKey: sessionB })));
          await act(async () => root.render(createElement(ThreadHarness, { draftKey: sessionA })));
        }

        await act(async () => {
          resolve();
          await Promise.resolve();
        });

        expect(loadComposerDraft(sessionA)).toBe(draft);
        expect(sendCapture.text).toBe(draft);
      } finally {
        await act(async () => root.unmount());
        host.remove();
        saveComposerDraft(sessionA, "");
        saveComposerDraft(sessionB, "");
      }
    },
  );

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

      await act(async () => pluginRegistry.remove("punctuation"));
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
    async (commandCatalogStatus) => {
      const execute = vi.fn();
      sendCapture.text = "/exit";
      putThreadCommand("exit", execute);

      renderToStaticMarkup(createElement(ThreadHarness, { commands: [], commandCatalogStatus }));
      const submit = sendCapture.handlers.at(-1);
      expect(submit).toBeTypeOf("function");
      let prevented = false;
      submit?.({ preventDefault: () => { prevented = true; } });
      await flushSends();

      expect(execute).not.toHaveBeenCalled();
      // The composer sends every message through the chat store, so the event is always
      // prevented; the plugin did not take it, the agent got it.
      expect(prevented).toBe(true);
      expect(sentToAgent("/exit")).toBe(true);
      expect(sendCapture.slashPaletteIds).not.toContain("exit");
    },
  );

  it("reserves native aliases from the loaded catalog", async () => {
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
    await flushSends();

    expect(execute).not.toHaveBeenCalled();
    expect(prevented).toBe(true);
    expect(sentToAgent("/exit")).toBe(true);
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
      // A turn the daemon is running.
      await act(async () => setStoreRunning(true));
      expect(sendCapture.busySubmit).toBeTypeOf("function");

      publishComposerText("/exit");
      let prevented = false;
      await act(async () => {
        sendCapture.busySubmit?.({ preventDefault: () => { prevented = true; } });
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      expect(execute).not.toHaveBeenCalled();
      expect(prevented).toBe(true);
      expect(sendCapture.queueRequests).toContainEqual(expect.objectContaining({ text: "/exit" }));
    } finally {
      resolveRun({ content: [] });
      await act(async () => root.unmount());
      host.remove();
      runAdapter.mockRestore();
    }
  });
});

describe("early plugin command submission", () => {
  const invokeFormSubmit = () => {
    const submit = sendCapture.submitHandlers.at(-1);
    expect(submit).toBeTypeOf("function");
    let prevented = false;
    submit?.({
      preventDefault: () => { prevented = true; },
      get defaultPrevented() { return prevented; },
    });
    return prevented;
  };

  it("keeps a declared plugin slash out of submit while the session catalog is loading", () => {
    sendCapture.text = "/probe";
    pluginRegistry.setListed(null);
    putLoadingThreadCommand("probe");

    renderToStaticMarkup(createElement(ThreadHarness, { commandCatalogStatus: "loading" }));

    expect(invokeFormSubmit()).toBe(true);
    expect(sendCapture.queueRequests).toHaveLength(0);
    expect(sendCapture.text).toBe("/probe");
  });

  it.each([
    ["ordinary slash prose", "/please explain this error", false],
    ["a known plugin command that is still loading", "/probe now", true],
  ] as const)("submits %s normally after the bounded catalog wait", async (_label, text, knownPlugin) => {
    vi.useFakeTimers();
    prepareMountedThread();
    sendCapture.text = text;
    if (knownPlugin) {
      pluginRegistry.setListed(null);
      putLoadingThreadCommand("probe");
    }
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    try {
      await act(async () => root.render(createElement(ThreadHarness, {
        commandCatalogStatus: "loading",
        draftKey: "plugin-command-wait-test",
      })));
      const runtime = sendCapture.runtime as {
        thread: { composer: { setText(text: string): void } };
      };
      await act(async () => {
        runtime.thread.composer.setText(text);
        publishComposerText(text);
      });
      const form = host.querySelector("form");
      expect(form).not.toBeNull();
      await act(async () => form?.requestSubmit());
      expect(sendCapture.modelRuns).toBe(knownPlugin ? 0 : 1);
      expect(sendCapture.text).toBe(text);

      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });

      expect(sendCapture.modelRuns).toBe(1);
    } finally {
      await act(async () => root.unmount());
      host.remove();
      saveComposerDraft("plugin-command-wait-test", "");
      vi.useRealTimers();
    }
  });

  it("does not replay a delayed slash into a replacement engine session", async () => {
    vi.useFakeTimers();
    prepareMountedThread();
    const draft = "/zzz explain this";
    pluginRegistry.setListed(null);
    putLoadingThreadCommand("probe");
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => root.render(createElement(ThreadHarness, {
        commandCatalogStatus: "loading",
        draftKey: "same-conversation",
        queueSessionId: "engine-before",
      })));
      const runtime = sendCapture.runtime as {
        thread: { composer: { setText(text: string): void } };
      };
      await act(async () => {
        runtime.thread.composer.setText(draft);
        publishComposerText(draft);
      });
      const form = host.querySelector("form");
      expect(form).not.toBeNull();
      await act(async () => form?.requestSubmit());
      expect(sendCapture.modelRuns).toBe(0);

      await act(async () => root.render(createElement(ThreadHarness, {
        commandCatalogStatus: "loading",
        draftKey: "same-conversation",
        queueSessionId: "engine-after",
        locked: "Switching session",
      })));
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });

      expect(sendCapture.modelRuns).toBe(0);
      expect(sendCapture.text).toBe(draft);
    } finally {
      await act(async () => root.unmount());
      host.remove();
      saveComposerDraft("same-conversation", "");
      vi.useRealTimers();
    }
  });

  it.each(["wait timeout", "catalog becomes ready"] as const)(
    "keeps a pending plugin slash in its old draft when %s replays under the composer lock",
    async (replay) => {
      vi.useFakeTimers();
      prepareMountedThread();
      const draft = "/probe now";
      const draftKey = "plugin-lock-old-session";
      const queueSessionId = "plugin-lock-old-engine";
      const run = vi.fn();
      saveComposerDraft(draftKey, "");
      pluginRegistry.setListed(null);
      putLoadingThreadCommand("probe");
      const host = document.createElement("div");
      document.body.append(host);
      const root = createRoot(host);

      try {
        await act(async () => root.render(createElement(ThreadHarness, {
          commandCatalogStatus: "loading",
          draftKey,
          queueSessionId,
        })));
        const runtime = sendCapture.runtime as {
          thread: { composer: { setText(text: string): void } };
        };
        await act(async () => {
          runtime.thread.composer.setText(draft);
          publishComposerText(draft);
        });
        const form = host.querySelector("form");
        expect(form).not.toBeNull();
        await act(async () => form?.requestSubmit());
        expect(sendCapture.modelRuns).toBe(0);

        await act(async () => root.render(createElement(ThreadHarness, {
          commandCatalogStatus: "loading",
          draftKey,
          queueSessionId,
          locked: "Switching session",
        })));
        if (replay === "catalog becomes ready") {
          await act(async () => {
            putThreadCommand("probe", run);
            root.render(createElement(ThreadHarness, {
              commandCatalogStatus: "ready",
              draftKey,
              queueSessionId,
              locked: "Switching session",
            }));
          });
        } else {
          await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
        }

        expect(sendCapture.modelRuns).toBe(0);
        expect(run).not.toHaveBeenCalled();
        expect(sendCapture.text).toBe(draft);
        expect(loadComposerDraft(draftKey)).toBe(draft);
      } finally {
        await act(async () => root.unmount());
        host.remove();
        saveComposerDraft(draftKey, "");
        vi.useRealTimers();
      }
    },
  );

  it("does not hold an unknown slash draft when the plugin inventory has no entries", async () => {
    sendCapture.text = "/probe";

    renderToStaticMarkup(createElement(ThreadHarness, { commandCatalogStatus: "ready" }));

    invokeFormSubmit();
    await flushSends();
    expect(sentToAgent("/probe")).toBe(true);
  });

  it("does not lose ordinary slash prose when the session changes during catalog loading", async () => {
    vi.useFakeTimers();
    prepareMountedThread();
    pluginRegistry.setListed(null);
    putLoadingThreadCommand("probe");
    const draft = "/summarize this paragraph";
    const targetDraftKey = "round14-target-session";
    saveComposerDraft(targetDraftKey, "");
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    try {
      await act(async () => root.render(createElement(ThreadHarness, {
        commandCatalogStatus: "loading",
        queueSessionId: "round14-source-session",
      })));
      const runtime = sendCapture.runtime as {
        thread: { composer: { setText(text: string): void } };
      };
      await act(async () => {
        runtime.thread.composer.setText(draft);
        publishComposerText(draft);
      });
      const form = host.querySelector("form");
      expect(form).not.toBeNull();
      await act(async () => form?.requestSubmit());

      await act(async () => root.render(createElement(ThreadHarness, {
        commandCatalogStatus: "loading",
        draftKey: targetDraftKey,
        queueSessionId: "round14-target-session",
        locked: "Switching session",
      })));
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });

      expect(sendCapture.modelRuns).toBe(1);
    } finally {
      await act(async () => root.unmount());
      host.remove();
      saveComposerDraft(targetDraftKey, "");
      vi.useRealTimers();
    }
  });

  it("keeps a declared plugin slash while its web module is importing", () => {
    sendCapture.text = "/probe";
    pluginRegistry.setListed(null);
    putLoadingThreadCommand("probe");

    renderToStaticMarkup(createElement(ThreadHarness, { commandCatalogStatus: "ready" }));

    expect(invokeFormSubmit()).toBe(true);
    expect(sendCapture.queueRequests).toHaveLength(0);
    expect(sendCapture.text).toBe("/probe");
  });

  it("blocks the Send button before the form can submit a still-loading plugin command", () => {
    sendCapture.text = "/probe";
    pluginRegistry.setListed(null);
    putLoadingThreadCommand("probe");
    renderToStaticMarkup(createElement(ThreadHarness, { commandCatalogStatus: "ready" }));
    const click = sendCapture.handlers.at(-1);
    expect(click).toBeTypeOf("function");
    let prevented = false;

    click?.({ preventDefault: () => { prevented = true; } });

    expect(prevented).toBe(true);
    expect(sendCapture.text).toBe("/probe");
  });

  it("does not add a still-loading plugin command to the busy-turn queue", async () => {
    prepareMountedThread();
    let resolveRun!: (value: ChatModelRunResult) => void;
    const activeRun = new Promise<ChatModelRunResult>((resolve) => { resolveRun = resolve; });
    const runAdapter = vi.spyOn(threadAdapter, "run").mockReturnValue(activeRun);
    pluginRegistry.setListed(null);
    putLoadingThreadCommand("probe");

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => root.render(createElement(ThreadHarness, { commandCatalogStatus: "ready" })));
      const runtime = sendCapture.runtime as {
        thread: { composer: { setText(text: string): void; send(): void }; getState(): { isRunning: boolean } };
      };
      // A turn the daemon is running.
      await act(async () => setStoreRunning(true));

      publishComposerText("/probe");
      let prevented = false;
      await act(async () => {
        sendCapture.busySubmit?.({
          preventDefault: () => { prevented = true; },
          get defaultPrevented() { return prevented; },
        });
        await Promise.resolve();
      });

      expect(prevented).toBe(true);
      expect(sendCapture.text).toBe("/probe");
      expect(sendCapture.transportCommands).not.toContainEqual(expect.objectContaining({
        type: "add-message",
        message: { role: "user", parts: [{ type: "text", text: "/probe" }] },
      }));
      expect(sentToAgent("/probe")).toBe(false);
    } finally {
      resolveRun({ content: [] });
      await act(async () => root.unmount());
      host.remove();
      runAdapter.mockRestore();
    }
  });

  it("sends Cmd+Enter slash prose immediately when no plugins are present", async () => {
    prepareMountedThread();
    pluginRegistry.setListed(null);
    let resolveRun!: (value: ChatModelRunResult) => void;
    const activeRun = new Promise<ChatModelRunResult>((resolve) => { resolveRun = resolve; });
    const runAdapter = vi.spyOn(threadAdapter, "run").mockReturnValue(activeRun);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const draftKey = "plugin-submit-send-now";
    const text = "/please summarize the failing tests";

    try {
      await act(async () => root.render(createElement(ThreadHarness, {
        commandCatalogStatus: "loading",
        draftKey,
      })));
      const runtime = sendCapture.runtime as {
        thread: {
          composer: { setText(text: string): void; send(): void };
          getState(): { isRunning: boolean };
        };
      };
      // A turn the daemon is running; assistant-ui's runtime runs too, as in the app, so
      // its input leaves Enter to the composer's busy-turn handler.
      await act(async () => {
        setStoreRunning(true);
        runtime.thread.composer.setText("start a turn");
        runtime.thread.composer.send();
        await Promise.resolve();
      });

      const input = host.querySelector("textarea[aria-label='Message input']");
      expect(input).not.toBeNull();
      await act(async () => {
        runtime.thread.composer.setText(text);
        publishComposerText(text);
        input?.dispatchEvent(new KeyboardEvent("keydown", {
          key: "Enter",
          metaKey: true,
          bubbles: true,
        }));
        await flushSends();
      });
      expect(sendCapture.queueRequests).toContainEqual(expect.objectContaining({ text, sendNow: true }));

      await act(async () => {
        root.render(createElement(ThreadHarness, { commandCatalogStatus: "ready", draftKey }));
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      expect(sendCapture.queueRequests).toHaveLength(1);
    } finally {
      resolveRun({ content: [] });
      await act(async () => root.unmount());
      host.remove();
      saveComposerDraft(draftKey, "");
      runAdapter.mockRestore();
    }
  });

  it.each([
    ["inventory listing failure", "unavailable" as const, "inventory" as const],
    ["plugin import failure", "ready" as const, "plugin" as const],
  ])("releases the slash gate after %s", async (_label, commandCatalogStatus, failure) => {
    sendCapture.text = "/probe";
    if (failure === "inventory") pluginRegistry.setListed("Plugin inventory is unavailable.");
    else {
      pluginRegistry.setListed(null);
      putLoadingThreadCommand("probe", "failed");
    }
    renderToStaticMarkup(createElement(ThreadHarness, { commandCatalogStatus }));

    invokeFormSubmit();
    await flushSends();
    expect(sentToAgent("/probe")).toBe(true);
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
