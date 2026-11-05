import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  AGENT_TYPE_GLYPHS,
  AgentTypeGlyph,
  resolveSidebarAgentType,
} from "@/components/shell/AgentTypeGlyph";

const bundledTypes = [
  "coder",
  "community",
  "creative-writer",
  "data-analyst",
  "default",
  "kollabor",
  "koordinator",
  "maintainer",
  "native",
  "pr-shepherd",
  "release-planner",
  "research",
  "system",
  "tech-dude",
  "technical-writer",
  "triager",
] as const;

describe("AgentTypeGlyph", () => {
  it.each(bundledTypes)("maps the %s bundle to one glyph", (type) => {
    const glyph = AGENT_TYPE_GLYPHS[type];
    expect(glyph).toBeTruthy();
    expect(Array.from(glyph)).toHaveLength(1);
    expect(renderToStaticMarkup(<AgentTypeGlyph type={type} />)).toContain(glyph);
  });

  it("uses the coordinator alias for the same glyph as koordinator", () => {
    expect(AGENT_TYPE_GLYPHS.coordinator).toBe(AGENT_TYPE_GLYPHS.koordinator);
  });

  it("uses the session bundle before type metadata from its roster entry", () => {
    expect(resolveSidebarAgentType({ agent_type: "research", agent: "coder" }, "default")).toBe("default");
    expect(resolveSidebarAgentType({ agent_type: "", agent: "coder" }, "research")).toBe("research");
  });

  it("uses each session bundle when one identity runs different bundles across workspaces", () => {
    const poolByName = new Map([
      ["shared-agent", { name: "shared-agent", agent_type: "research", agent: "research" }],
    ]);
    const sessions = [
      { identity: "shared-agent", workspace: "/projects/research", agent: "research" },
      { identity: "shared-agent", workspace: "/projects/coding", agent: "coder" },
    ];

    const glyphs = sessions.map((session) => {
      const type = resolveSidebarAgentType(poolByName.get(session.identity), session.agent);
      return {
        workspace: session.workspace,
        type,
        markup: renderToStaticMarkup(<AgentTypeGlyph type={type} />),
      };
    });

    expect(glyphs.map(({ workspace, type }) => [workspace, type])).toEqual([
      ["/projects/research", "research"],
      ["/projects/coding", "coder"],
    ]);
    expect(glyphs[0].markup).toContain(AGENT_TYPE_GLYPHS.research);
    expect(glyphs[1].markup).toContain(AGENT_TYPE_GLYPHS.coder);
  });

  it("uses the session bundle when a present roster entry has blank type fields", () => {
    expect(resolveSidebarAgentType({ agent_type: "", agent: "" }, " research ")).toBe("research");
  });

  it("uses default only for a present roster entry with an empty type and bundle", () => {
    expect(resolveSidebarAgentType({ agent_type: "", agent: "" })).toBe("default");
    expect(resolveSidebarAgentType(undefined, "coder")).toBe("coder");
    expect(resolveSidebarAgentType(undefined, "  ")).toBeUndefined();
    expect(resolveSidebarAgentType(undefined)).toBeUndefined();
  });

  it("labels the glyph for assistive technology and hover at muted 0.75em", () => {
    const markup = renderToStaticMarkup(<AgentTypeGlyph type="research" />);
    expect(markup).toContain('role="img"');
    expect(markup).toContain('title="research"');
    expect(markup).toContain('aria-label="research"');
    expect(markup).toContain("text-[0.75em]");
    expect(markup).toContain("text-muted-foreground");
  });

  it.each(["constructor", "__proto__"])("treats %s as an unknown type", (type) => {
    const markup = renderToStaticMarkup(<AgentTypeGlyph type={type} />);
    expect(markup).toContain("◇");
    expect(markup).toContain(`title="${type}"`);
    expect(markup).toContain(`aria-label="${type}"`);
  });

  it("uses a generic labeled glyph for unknown types and omits missing types", () => {
    const unknown = renderToStaticMarkup(<AgentTypeGlyph type="custom-agent-v2" />);
    expect(unknown).toContain("◇");
    expect(unknown).toContain('title="custom-agent-v2"');
    expect(unknown).toContain('aria-label="custom-agent-v2"');
    expect(renderToStaticMarkup(<AgentTypeGlyph />)).toBe("");
    expect(renderToStaticMarkup(<AgentTypeGlyph type="   " />)).toBe("");
  });
});
