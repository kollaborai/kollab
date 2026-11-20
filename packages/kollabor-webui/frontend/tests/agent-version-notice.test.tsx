import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, isAgentTooOld } from "@/api";
import { DaemonNotice } from "@/components/code/DaemonNotice";
import { AgentVersionNotice, agentVersionNotice } from "@/components/shell/AgentVersionNotice";
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
const visibleText = (html: string) => html.replace(/<[^>]+>/g, "");

describe("agentVersionNotice", () => {
  it("is silent when the agent runs the engine's version", () => {
    expect(agentVersionNotice({ engineVersion: "0.21.0+aaaa1111", agent: { ...agent, version: "0.21.0+aaaa1111" }, restartAgents: true })).toBeNull();
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

  it("points at /restart when the flag is off", () => {
    expect(agentVersionNotice({ engineVersion: "0.21.0+aaaa1111", agent: { ...agent, version: "0.20.0+bbbb2222" }, restartAgents: false })).toEqual(
      expect.objectContaining({ kind: "command" }),
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

  it("keeps the disclosure on the actions row at phone widths; only the expanded text spans full width", () => {
    const notice = agentVersionNotice({ ...differ, restartAgents: true })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} />);
    // No full-width disclosure row, or the summary and the Dismiss X each orphan onto their own row at 390px.
    expect(html).not.toMatch(/<details class="[^"]*basis-full/);
    // The expanded text is the strip's last row, after the Dismiss, spanning the strip.
    expect(html).toMatch(/<span id="[^"]+" class="[^"]*order-last w-full/);
  });

  it("flag off: the /restart line and no button", () => {
    const notice = agentVersionNotice({ ...differ, restartAgents: false })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} />);
    expect(html).toContain("Run /restart to update the agent version running.");
    expect(html).not.toContain("Restart Agent");
    expect(html).not.toContain("<button");
  });
});

describe("agents that cannot restart themselves (#968)", () => {
  const sentence = "This agent started before Restart Agent existed. Run kollab --hub stop lapis in a terminal, then select its Offline row in the sidebar to start it again.";
  const noVersion = { engineVersion: "0.21.0+aaaa1111", agent: { ...agent, version: "" } };
  const older = { engineVersion: "0.21.0+aaaa1111", agent: { ...agent, version: "0.20.0+bbbb2222" } };

  it("an agent that publishes no version gets the too-old line with its name, with the flag on or off", () => {
    expect(agentVersionNotice({ ...noVersion, restartAgents: true })).toEqual(expect.objectContaining({ kind: "too-old", name: "lapis" }));
    expect(agentVersionNotice({ ...noVersion, restartAgents: false })).toEqual(expect.objectContaining({ kind: "too-old", name: "lapis" }));
  });

  it("keeps the --hub flag intact in a wrapping code line, with Dismiss only", () => {
    const notice = agentVersionNotice({ ...noVersion, restartAgents: true })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} onDismiss={() => {}} />);
    expect(visibleText(html)).toContain(sentence);
    expect(html).toMatch(/<code class="[^\"]*\bwhitespace-normal\b[^\"]*">kollab <span class="[^\"]*\bwhitespace-nowrap\b[^\"]*">--hub<\/span> stop lapis<\/code>/);
    expect(html).not.toContain("Try Again");
    expect(html.match(/<button/g)?.length).toBe(1);
  });

  it("allows a 37-character agent name to wrap without splitting the --hub flag", () => {
    const name = "studio-mac-agent-01234567890123456789";
    const notice = agentVersionNotice({ ...noVersion, agent: { ...noVersion.agent, name }, restartAgents: true })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} />);

    expect(visibleText(html)).toContain(`kollab --hub stop ${name}`);
    expect(html).toMatch(new RegExp(`<code class="[^\\"]*\\bwhitespace-normal\\b[^\\"]*">kollab <span class="[^\\"]*\\bwhitespace-nowrap\\b[^\\"]*">--hub<\\/span> stop ${name}<\\/code>`));
  });

  it("a 409 that says the agent is too old: the same sentence, Dismiss only, no Try Again", () => {
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
    expect(visibleText(html)).toContain(sentence);
    expect(html).not.toContain("Try Again");
    expect(html.match(/<button/g)?.length).toBe(1);
  });

  it("any other failure keeps its Try Again", () => {
    const notice = agentVersionNotice({ ...older, restartAgents: true })!;
    const html = renderToStaticMarkup(
      <AgentVersionNotice notice={notice} busy={false} failure="409: this agent is not on a hub, so it cannot restart itself" onRestart={() => {}} onDismiss={() => {}} />,
    );
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

describe("daemon outdated notices", () => {
  const stopAndStart = "This agent started before Restart Agent existed. Run kollab --hub stop lapis in a terminal, then select its Offline row in the sidebar to start it again.";

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

  it.each(["Terminal", "Code", "Git"])("shows the working stop-and-start instruction in %s for an unversioned agent", (panel) => {
    const html = renderToStaticMarkup(<DaemonNotice {...panelNoticeProps("", false)} />);
    expect(visibleText(html), panel).toContain(stopAndStart);
    expect(html).toMatch(/<code class="[^\"]*whitespace-nowrap[^\"]*">kollab --hub stop lapis<\/code>/);
    expect(html).not.toContain("Retry");
  });

  it("shows Restart Agent for a versioned agent when the restart setting is on", () => {
    const html = renderToStaticMarkup(<DaemonNotice {...panelNoticeProps("0.21.0+bbbb2222", true)} />);
    expect(html).toContain("Restart Agent");
  });

  it("shows /restart for a versioned agent when the restart setting is off", () => {
    const html = renderToStaticMarkup(<DaemonNotice {...panelNoticeProps("0.21.0+bbbb2222", false)} />);
    expect(html).toContain("Run /restart to update the agent version running.");
  });

  it("keeps a usable stop-and-start action while version information is unavailable", () => {
    const html = renderToStaticMarkup(
      <DaemonNotice notice={null} fallbackName="lapis" busy={false} onRestart={() => {}} />,
    );
    expect(visibleText(html)).toContain("This agent runs older code. Run kollab --hub stop lapis in a terminal");
    expect(html).toMatch(/<code class="[^\"]*whitespace-nowrap[^\"]*">kollab --hub stop lapis<\/code>/);
  });

  it("the Terminal does not offer Retry for daemon_outdated", () => {
    const html = renderToStaticMarkup(
      <TerminalView sessionId="s1" workspace="/workspace" daemonNotice={panelNoticeProps("", false)} />,
    );
    expect(visibleText(html)).toContain(stopAndStart);
    expect(html).toMatch(/<code class="[^\"]*whitespace-nowrap[^\"]*">kollab --hub stop lapis<\/code>/);
    expect(html).not.toMatch(/>Retry<\/button>/);
  });

  it("keeps Retry for other Terminal errors", () => {
    terminalMock.errorCode = "request_failed";
    const html = renderToStaticMarkup(
      <TerminalView sessionId="s1" workspace="/workspace" daemonNotice={panelNoticeProps("", false)} />,
    );
    expect(html).toMatch(/>Retry<\/button>/);
  });
});
