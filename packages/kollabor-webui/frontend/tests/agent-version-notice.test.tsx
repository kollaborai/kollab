import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
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

  it("flag off: the /restart line and no button", () => {
    const notice = agentVersionNotice({ ...differ, restartAgents: false })!;
    const html = renderToStaticMarkup(<AgentVersionNotice notice={notice} busy={false} onRestart={() => {}} />);
    expect(html).toContain("Run /restart to update the agent version running.");
    expect(html).not.toContain("Restart Agent");
    expect(html).not.toContain("<button");
  });
});
