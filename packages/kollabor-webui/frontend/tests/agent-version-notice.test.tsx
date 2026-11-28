import { createRequire } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, isAgentTooOld } from "@/api";
import { DaemonNotice } from "@/components/code/DaemonNotice";
import { AgentVersionNotice, agentVersionNotice, versionNoticeKey } from "@/components/shell/AgentVersionNotice";
import { TerminalView } from "@/components/terminal/TerminalView";

const terminalMock = vi.hoisted(() => ({ errorCode: "daemon_outdated" }));

vi.mock("@/components/terminal/use-terminal-ws-connection", () => ({
  useTerminalWsConnection: () => ({
    status: "error",
    errorCode: terminalMock.errorCode,
    errorMessage: "This agent runs older code.",
    reconnecting: false,
    refusal: null,
    close: () => {},
  }),
}));
vi.mock("@/components/terminal/terminal-viewer", () => ({ TerminalViewer: () => null }));
vi.mock("@/components/code/floating-sidebar", () => ({
  FloatingFileSidebar: ({ children }: { children?: unknown }) => children,
  useFileSidebarVisibility: () => ({ visible: true, narrow: false, toggle: () => {}, hide: () => {} }),
}));

const agent = { name: "lapis", agent_id: "a1", state: "idle", current_task: "", solo: false, project: "/w" };
const { JSDOM } = createRequire(import.meta.url)("jsdom") as {
  JSDOM: new (html: string) => { window: { document: { body: { textContent: string | null } } } };
};
const visibleText = (html: string) => new JSDOM(html).window.document.body.textContent ?? "";

describe("visibleText", () => {
  it("preserves text that looks like an HTML element", () => {
    const text = "<script>alert(1)</script>";
    const html = renderToStaticMarkup(<span>{text}</span>);

    expect(visibleText(html)).toBe(text);
  });
});

describe("agentVersionNotice", () => {
  it("is silent when the agent runs the engine's version", () => {
    expect(agentVersionNotice({ engineVersion: "0.21.0+aaaa1111", agent: { ...agent, version: "0.21.0+aaaa1111" }, restartAgents: true })).toBeNull();
  });

  it("names stop-and-start, not /restart, when the chat cannot reach an older agent (#1451)", () => {
    const older = { ...agent, version: "0.20.0" };
    expect(agentVersionNotice({ engineVersion: "0.21.0", agent: older, restartAgents: false, chatOutdated: true }))
      .toMatchObject({ kind: "unknown", name: "lapis" });
    expect(agentVersionNotice({ engineVersion: "0.21.0", agent: older, restartAgents: true, chatOutdated: true }))
      .toMatchObject({ kind: "button" });
    expect(agentVersionNotice({ engineVersion: "0.21.0", agent: { ...agent, version: "0.21.0" }, restartAgents: false, chatOutdated: true }))
      .toMatchObject({ kind: "unknown", name: "lapis" });
  });

  it("keys each chat's notice by session, and a 426 notice apart from a dismissed version notice (#1451 review)", () => {
    expect(versionNoticeKey("s1", undefined, true)).not.toBe(versionNoticeKey("s2", undefined, true));
    expect(versionNoticeKey("s1", "a1", true)).not.toBe(versionNoticeKey("s1", "a1", false));
    expect(versionNoticeKey("s1", "a1", false)).toBe("s1|a1");
    expect(versionNoticeKey(undefined, "a1", false)).toBe("");
  });

  it("never leaves an outdated chat without a notice (#1451 review)", () => {
    const current = { ...agent, version: "0.21.0" };
    expect(agentVersionNotice({ engineVersion: "0.21.0", agent: current, restartAgents: true, chatOutdated: true }))
      .toMatchObject({ kind: "button", name: "lapis" });
    expect(agentVersionNotice({ engineVersion: "0.21.0", agent: undefined, restartAgents: true, chatOutdated: true, chatName: "ruby" }))
      .toMatchObject({ kind: "unknown", name: "ruby" });
    expect(agentVersionNotice({ engineVersion: "", agent: undefined, restartAgents: false, chatOutdated: true, chatName: "ruby" }))
      .toMatchObject({ kind: "unknown", name: "ruby" });
  });

  it("is silent when either side does not say (no agent row, no engine version)", () => {
    expect(agentVersionNotice({ engineVersion: "0.21.0+aaaa1111", agent: undefined, restartAgents: true })).toBeNull();
    expect(agentVersionNotice({ engineVersion: "", agent: { ...agent, version: "0.20.0" }, restartAgents: true })).toBeNull();
  });

  it("offers the button when restart_agents is on and the versions differ", () => {
    const notice = agentVersionNotice({ engineVersion: "0.21.0+aaaa1111", agent: { ...agent, version: "0.20.0+bbbb2222" }, restartAgents: true });
    expect(notice).toEqual(expect.objectContaining({ kind: "button" }));
    expect(notice?.detail).toContain("0.20.0+bbbb2222");
    expect(notice?.detail).toContain("0.21.0+aaaa1111");
  });

  it("offers a manual restart when automatic restarts are off", () => {
    expect(agentVersionNotice({ engineVersion: "0.21.0+aaaa1111", agent: { ...agent, version: "0.20.0+bbbb2222" }, restartAgents: false })).toEqual(
      expect.objectContaining({ kind: "button" }),
    );
  });

  it("treats an agent that publishes no version as older", () => {
    expect(agentVersionNotice({ engineVersion: "0.21.0+aaaa1111", agent: { ...agent, version: "" }, restartAgents: false })).not.toBeNull();
  });
});

describe("AgentVersionNotice", () => {
  const differ = { engineVersion: "0.21.0+aaaa1111", agent: { ...agent, version: "0.20.0+bbbb2222" } };

  it("flag on: the sentence and a Restart Agent button, no version in the visible text", () => {
    const notice = agentVersionNotice({ ...differ, restartAgents: true })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} />);
    expect(html).toContain("This agent runs an older version of Kollab.");
    expect(html).toContain("Restart Agent");
    expect(html.replace(/title="[^"]*"/g, "")).not.toMatch(/0\.2\d\.\d/);
  });

  it("makes the agent and engine versions available through a touch-accessible disclosure", () => {
    const notice = agentVersionNotice({ ...differ, restartAgents: true })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} />);
    expect(html).toContain('<summary aria-label="Version details"');
    expect(html).not.toContain(">Version details</summary>");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("aria-controls=");
  });

  it("resets the browser's disclosure marker so no triangle stacks on the info icon", () => {
    const notice = agentVersionNotice({ ...differ, restartAgents: true })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} />);
    expect(html).toMatch(/<summary[^>]*class="[^"]*list-none/);
    expect(html).toMatch(/<summary[^>]*class="[^"]*\[&amp;::-webkit-details-marker\]:hidden/);
  });

  it("centers the version disclosure and gives touch users a 44px target", () => {
    const notice = agentVersionNotice({ ...differ, restartAgents: true })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} />);
    expect(html).toMatch(/<summary[^>]*class="[^"]*inline-flex[^\"]*items-center[^\"]*justify-center/);
    expect(html).toContain("pointer-coarse:size-11");
  });

  it("keeps the disclosure on the actions row at phone widths; only the expanded text spans full width", () => {
    const notice = agentVersionNotice({ ...differ, restartAgents: true })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} />);
    // No full-width disclosure row, or the summary and the Dismiss X each orphan onto their own row at 390px.
    expect(html).not.toMatch(/<details class="[^"]*basis-full/);
    // The expanded text is the strip's last row, after the Dismiss, spanning the strip.
    expect(html).toMatch(/<span id="[^"]+" class="[^"]*order-last w-full/);
  });

  it("flag off: still offers Restart Agent instead of a command", () => {
    const notice = agentVersionNotice({ ...differ, restartAgents: false })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} />);
    expect(html).toContain("This agent runs an older version of Kollab.");
    expect(html).toContain("Restart Agent");
    expect(html).not.toContain("/restart");
  });

  it("explains a pending restart without agent-turn jargon", () => {
    const notice = agentVersionNotice({ ...differ, restartAgents: false })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy waiting onRestart={() => {}} />);
    expect(visibleText(html)).toContain("This agent will restart after its current work finishes.");
    expect(visibleText(html)).not.toContain("turn");
  });

  it("keeps the notice dismissible while a restart waits", () => {
    const notice = agentVersionNotice({ ...differ, restartAgents: false })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy waiting onRestart={() => {}} onDismiss={() => {}} />);
    expect(html).toContain('aria-label="Dismiss"');
  });
});

describe("agents that cannot restart themselves (#968)", () => {
  const sentence = "This agent runs an older version of Kollab.";
  const noVersion = { engineVersion: "0.21.0+aaaa1111", agent: { ...agent, version: "" } };
  const older = { engineVersion: "0.21.0+aaaa1111", agent: { ...agent, version: "0.20.0+bbbb2222" } };

  it("an agent that publishes no version gets the too-old line with its name, with the flag on or off", () => {
    expect(agentVersionNotice({ ...noVersion, restartAgents: true })).toEqual(expect.objectContaining({ kind: "too-old", name: "lapis" }));
    expect(agentVersionNotice({ ...noVersion, restartAgents: false })).toEqual(expect.objectContaining({ kind: "too-old", name: "lapis" }));
  });

  it("offers in-page recovery while the engine version is still loading", () => {
    const notice = agentVersionNotice({ engineVersion: "", agent: noVersion.agent, restartAgents: false });
    const html = renderToStaticMarkup(<DaemonNotice notice={notice} fallbackName="lapis" busy={false} onRestart={() => {}} />);

    expect(visibleText(html)).toContain(sentence);
    expect(html).toContain("Restart Agent");
    expect(html).not.toContain("kollab --");
    expect(visibleText(html)).not.toContain("in a terminal");
  });

  it("offers Restart Agent for an unversioned agent and keeps Dismiss", () => {
    const notice = agentVersionNotice({ ...noVersion, restartAgents: true })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} onDismiss={() => {}} />);
    expect(visibleText(html)).toContain(sentence);
    expect(html).toContain("Restart Agent");
    expect(html).not.toContain("kollab --");
    expect(visibleText(html)).not.toContain("in a terminal");
    expect(html).not.toContain("Try Again");
    expect(html.match(/<button/g)?.length).toBe(2);
  });

  it("does not expose the old agent name in recovery copy", () => {
    const name = "studio-mac-agent-01234567890123456789";
    const notice = agentVersionNotice({ ...noVersion, agent: { ...noVersion.agent, name }, restartAgents: false })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} />);
    expect(visibleText(html)).toContain(sentence);
    expect(visibleText(html)).not.toContain(name);
    expect(html).toContain("Restart Agent");
    expect(html).not.toContain("kollab --");
  });

  it("a too-old 409 shows a safe failure and a retry action, with no terminal command", () => {
    const notice = agentVersionNotice({ ...older, restartAgents: true })!;
    const html = renderToStaticMarkup(
      <AgentVersionNotice
        notice={notice}
        busy={false}
        tooOld
        failure="409: this agent is too old to restart itself: quit it and start it again"
        onRestart={() => {}}
        onDismiss={() => {}}
      />,
    );
    expect(html).toContain('role="alert"');
    expect(visibleText(html)).toContain("This agent could not be restarted.");
    expect(visibleText(html)).not.toContain("by this engine");
    expect(html).toContain("Try Again");
    expect(html).not.toContain("kollab --");
    expect(visibleText(html)).not.toContain("quit it and start it again");
  });

  it("any other failure keeps its Try Again", () => {
    const notice = agentVersionNotice({ ...older, restartAgents: true })!;
    const html = renderToStaticMarkup(
      <AgentVersionNotice notice={notice} busy={false} failure="409: this agent is not on a hub, so it cannot restart itself" onRestart={() => {}} onDismiss={() => {}} />,
    );
    expect(visibleText(html)).toContain("Couldn't restart this agent.");
    expect(visibleText(html)).not.toContain("409:");
    expect(visibleText(html)).not.toContain("not on a hub");
    expect(html).toContain("409: this agent is not on a hub, so it cannot restart itself");
    expect(html).toContain('aria-label="Restart details"');
    expect(html).toContain("Try Again");
    expect(html).not.toContain("started before Restart Agent existed");
  });

  it("only the engine's too-old 409 counts as too old", () => {
    expect(isAgentTooOld(new ApiError("409: this agent is too old to restart itself: quit it and start it again", 409, undefined))).toBe(true);
    expect(isAgentTooOld(new ApiError("409: this agent is not on a hub, so it cannot restart itself", 409, undefined))).toBe(false);
    expect(isAgentTooOld(new ApiError("500: this agent is too old to restart itself", 500, undefined))).toBe(false);
    expect(isAgentTooOld(new Error("this agent is too old to restart itself"))).toBe(false);
  });
});

describe("other Web UI recovery copy", () => {
  it("does not tell a browser user to restart the engine from a terminal", () => {
    const source = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
    const message = source.match(/result\.hub !== enabled\) throw new Error\("([^"]+)"\);/)?.[1];
    expect(message).toBe("This engine version does not support changing hub membership. The setting was not changed.");
  });
});

describe("daemon outdated notices", () => {
  const outdatedCopy = "This agent runs an older version of Kollab.";

  beforeEach(() => {
    terminalMock.errorCode = "daemon_outdated";
  });

  function panelNoticeProps(version: string, restartAgents: boolean) {
    return {
      notice: agentVersionNotice({
        engineVersion: "0.22.0+aaaa1111",
        agent: { ...agent, version },
        restartAgents,
      }),
      fallbackName: agent.name,
      busy: false,
      onRestart: () => {},
    };
  }

  it.each(["Terminal", "Code", "Git"])("shows Restart Agent in %s for an unversioned agent", (panel) => {
    const html = renderToStaticMarkup(<DaemonNotice {...panelNoticeProps("", false)} />);
    expect(visibleText(html), panel).toContain(outdatedCopy);
    expect(html).toContain("Restart Agent");
    expect(html).not.toContain("kollab --");
    expect(visibleText(html)).not.toContain("in a terminal");
    expect(html).not.toContain("Try Again");
  });

  it("shows Restart Agent for a versioned agent when the restart setting is on", () => {
    const html = renderToStaticMarkup(<DaemonNotice {...panelNoticeProps("0.21.0+bbbb2222", true)} />);
    expect(html).toContain("Restart Agent");
  });

  it("shows Restart Agent for a versioned agent when automatic restarts are off", () => {
    const html = renderToStaticMarkup(<DaemonNotice {...panelNoticeProps("0.21.0+bbbb2222", false)} />);
    expect(html).toContain("Restart Agent");
    expect(html).not.toContain("/restart");
  });

  it("does not invent a local stop command when the daemon has no live agent identity", () => {
    const props = { notice: null, fallbackName: "<agent name>", busy: false, onRestart: () => {} };
    const html = renderToStaticMarkup(<DaemonNotice {...props} />);
    expect(visibleText(html)).toContain(outdatedCopy);
    expect(visibleText(html)).not.toContain("<agent name>");
    expect(html).toContain("Restart Agent");
    expect(html).not.toContain("kollab --");
  });

  it("the Terminal does not offer Retry for daemon_outdated", () => {
    const html = renderToStaticMarkup(
      <TerminalView sessionId="s1" workspace="/workspace" daemonNotice={panelNoticeProps("", false)} />,
    );
    expect(visibleText(html)).toContain(outdatedCopy);
    expect(html).toContain("Restart Agent");
    expect(html).not.toContain("kollab --");
    expect(html).not.toMatch(/>Retry<\/button>/);
  });

  it("restores Retry after the agent runs the engine version", () => {
    const html = renderToStaticMarkup(
      <TerminalView
        sessionId="s1"
        workspace="/workspace"
        agentIsCurrent
        daemonNotice={panelNoticeProps("0.22.0+aaaa1111", true)}
      />,
    );
    expect(html).toMatch(/>Retry<\/button>/);
  });

  it("removes the stale stop instruction after the agent runs the engine version", () => {
    const html = renderToStaticMarkup(
      <TerminalView
        sessionId="s1"
        workspace="/workspace"
        agentIsCurrent
        daemonNotice={panelNoticeProps("0.22.0+aaaa1111", true)}
      />,
    );
    expect(visibleText(html)).not.toContain("kollab --");
  });

  it("keeps Retry for other Terminal errors", () => {
    terminalMock.errorCode = "request_failed";
    const html = renderToStaticMarkup(
      <TerminalView sessionId="s1" workspace="/workspace" daemonNotice={panelNoticeProps("", false)} />,
    );
    expect(html).toMatch(/>Retry<\/button>/);
  });
});


describe("PanelHost server copy", () => {
  it("passes panel responses through without a browser-side matcher", () => {
    const source = readFileSync(new URL("../src/components/panels/PanelHost.tsx", import.meta.url), "utf8");
    expect(source).not.toContain("webUiPanelCopy");
    expect(source).toContain("text: result.message");
    expect(existsSync(new URL("../src/components/panels/panel-web-copy.ts", import.meta.url))).toBe(false);
  });
});
