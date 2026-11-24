import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  type ChatModelAdapter,
} from "@assistant-ui/react";
import {
  ThreadListItemRuntimeProvider,
  useExternalStoreRuntime,
} from "@assistant-ui/core/react";
import { Thread } from "@/components/assistant-ui/thread";
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
  setTextCalls: 0,
}));

vi.mock("@assistant-ui/react", async (importOriginal) => {
  const React = await import("react");
  const actual = await importOriginal<typeof import("@assistant-ui/react")>();
  return {
    ...actual,
    useAui: () => {
      const aui = actual.useAui();
      return {
        ...aui,
        composer: {
          ...aui.composer,
          getState: () => ({ text: sendCapture.text, attachments: sendCapture.attachments }),
          setText: (text: string) => {
            sendCapture.setTextCalls += 1;
            sendCapture.text = text;
          },
        },
      };
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
        return React.createElement("button", null, children);
      },
    },
  };
});

vi.mock("@/components/assistant-ui/composer-palette", async () => {
  const React = await import("react");
  return {
    ComposerPalette: ({ items }: { items: readonly { id: string; label: string }[] }) =>
      React.createElement(
        "div",
        { "data-testid": "composer-palette" },
        ...items.map((item) => React.createElement("span", { key: item.id }, item.label)),
      ),
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
    entry: (enableOrder === undefined ? {} : { enable_order: enableOrder }) as PluginRecord["entry"],
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
    expect([...keys].sort()).toEqual(["cfg", "config", "help"]);
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
  it("records the losing plugin and first-enabled winner for Settings", async () => {
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
  it("names the command and keeps the error on one line", () => {
    expect(pluginCommandFailure("hello", new Error("first line\nsecond line"))).toBe(
      "Could not run /hello: first line second line",
    );
  });

  it("names the command alone when the error has no text", () => {
    expect(pluginCommandFailure("hello", new Error("  "))).toBe("Could not run /hello.");
  });
});

const threadAdapter: ChatModelAdapter = { run: async () => ({ content: [] }) };

function putThreadCommand(name: string, run = () => {}, toast = vi.fn()) {
  pluginRegistry.put({
    id: "punctuation",
    name: "Punctuation",
    entry: {} as PluginEntry,
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

function LocalThreadHarness() {
  const runtime = useLocalRuntime(threadAdapter);
  return createElement(
    AssistantRuntimeProvider,
    { runtime },
    createElement(Thread, { agents: [], commands: [], attachmentsEnabled: false }),
  );
}

function ThreadHarness() {
  const host = useExternalStoreRuntime({ messages: [], onNew: async () => {} });
  return createElement(
    AssistantRuntimeProvider,
    { runtime: host },
    createElement(
      ThreadListItemRuntimeProvider,
      { runtime: host.threads.mainItem },
      createElement(LocalThreadHarness),
    ),
  );
}

afterEach(() => pluginRegistry.reset());
afterEach(() => {
  sendCapture.handlers.length = 0;
  sendCapture.text = "";
  sendCapture.attachments.length = 0;
  sendCapture.setTextCalls = 0;
});

describe("plugin command composer labels", () => {
  it("keeps a label when a command name contains only separators", () => {
    putThreadCommand("__");

    const html = renderToStaticMarkup(createElement(ThreadHarness));

    expect(html).toContain(">__</span>");
  });

  it.each(["throws", "rejects"] as const)(
    "restores a plugin command draft when its handler %s",
    async (failure) => {
      const draft = "/publish now";
      sendCapture.text = draft;
      const execute = vi.fn(() => {
        if (failure === "throws") throw new Error("offline");
        return Promise.reject(new Error("offline"));
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
      if (failure === "rejects") await Promise.resolve();

      expect(sendCapture.text).toBe(draft);
      expect(sendCapture.setTextCalls).toBe(2);
      expect(toast).toHaveBeenCalledOnce();
    },
  );
});
