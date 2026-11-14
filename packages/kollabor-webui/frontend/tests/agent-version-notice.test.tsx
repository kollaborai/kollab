import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ApiError, isAgentTooOld } from "@/api";
import { AgentVersionNotice, agentVersionNotice } from "@/components/shell/AgentVersionNotice";

const agent = { name: "lapis", agent_id: "a1", state: "idle", current_task: "", solo: false, project: "/w" };

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

  it("the too-old line is plain text: the sentence and a Dismiss, no Restart Agent or Try Again", () => {
    const notice = agentVersionNotice({ ...noVersion, restartAgents: true })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} onDismiss={() => {}} />);
    expect(html).toContain(sentence);
    expect(html).not.toContain("Try Again");
    expect(html.match(/<button/g)?.length).toBe(1);
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
    expect(html).toContain(sentence);
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
