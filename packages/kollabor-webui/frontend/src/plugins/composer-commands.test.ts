import { describe, expect, it } from "vitest";
import { EMPTY_CONTRIBUTIONS, type PluginRecord } from "./registry";
import type { CommandSpec, PluginApi } from "./sdk";
import {
  matchPluginCommand,
  pluginCommandEntries,
  pluginCommandItems,
  reservedCommandKeys,
} from "./composer-commands";

const k = {} as PluginApi;
const run = () => undefined;

function command(name: string, description = `${name} command`): CommandSpec {
  return { name, description, run };
}

function record(id: string, commands: CommandSpec[], withK = true): PluginRecord {
  return {
    id,
    name: id,
    entry: {} as PluginRecord["entry"],
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
      id: "plugin:hello:hello",
      label: "Label hello",
      description: "Say hello",
      type: "command",
      category: "plugins",
      insertText: "hello",
    });
  });
});
