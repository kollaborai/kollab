// Run: npx vitest run tests/prompts-page.test.ts
// The System Prompts page: its typed client against fixture transports, its
// pure Libraries + Roles model, and the rendered view (SSR) over fixtures.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { jsx } from "react/jsx-runtime";

import { PromptsApi, PromptsApiError } from "../src/prompts-api";
import {
  diffLines,
  includedTotals,
  isDirty,
  libraryGroups,
  roleGroups,
  sectionFolder,
  titleFromKey,
  toggleRow,
  variantLabel,
  variantNames,
  variantOf,
} from "../src/components/prompts/prompts-model";
import { PromptsView } from "../src/components/prompts/PromptsPage";
import {
  FIXTURE_COMPOSITION,
  FIXTURE_CONTENT,
  FIXTURE_LIBRARIES,
  FIXTURE_PREVIEW,
  FIXTURE_PROMPTS,
  FIXTURE_ROLE_SECTIONS,
  FIXTURE_ROLES,
  fixtureTransport,
} from "./fixtures/prompts";

describe("PromptsApi", () => {
  it("lists prompts from GET /prompts", async () => {
    const fixture = fixtureTransport();
    const api = new PromptsApi(fixture.transport);
    const result = await api.listPrompts();
    expect(result.prompts).toEqual(FIXTURE_PROMPTS);
    expect(fixture.calls[0]).toMatchObject({ path: "/prompts", init: {} });
  });

  it("lists sections under the bundle's encoded path", async () => {
    const fixture = fixtureTransport();
    const api = new PromptsApi(fixture.transport);
    const result = await api.listSections("coder");
    expect(result.sections).toEqual(FIXTURE_ROLE_SECTIONS);
    expect(fixture.calls[0].path).toBe("/prompts/coder/sections");
  });

  it("encodes bundle and key path segments", async () => {
    const fixture = fixtureTransport();
    const api = new PromptsApi(fixture.transport);
    await expect(
      api.getSection("my agent", "hook system"),
    ).rejects.toBeInstanceOf(PromptsApiError);
    expect(fixture.calls[0].path).toBe(
      "/prompts/my%20agent/sections/hook%20system",
    );
  });

  it("saves an override with PUT and a JSON content body", async () => {
    const fixture = fixtureTransport();
    const api = new PromptsApi(fixture.transport);
    await api.saveSection("coder", "identity", "Rewritten identity.\n");
    expect(fixture.calls[0]).toMatchObject({
      path: "/prompts/coder/sections/identity",
    });
    expect(fixture.calls[0].init.method).toBe("PUT");
    expect(JSON.parse(String(fixture.calls[0].init.body))).toEqual({
      content: "Rewritten identity.\n",
    });
    expect(fixture.saved["coder/identity"]).toBe("Rewritten identity.\n");
    await expect(api.getSection("coder", "identity")).resolves.toMatchObject({
      content: "Rewritten identity.\n",
      overridden: true,
    });
    await expect(api.listSections("coder")).resolves.toMatchObject({
      sections: expect.arrayContaining([
        expect.objectContaining({ key: "identity", overridden: true }),
      ]),
    });
  });

  it("deletes an override with DELETE on the same path", async () => {
    const fixture = fixtureTransport();
    const api = new PromptsApi(fixture.transport);
    await api.deleteOverride("coder", "output-style");
    expect(fixture.calls[0].init.method).toBe("DELETE");
    expect(fixture.calls[0].path).toBe("/prompts/coder/sections/output-style");
    expect(fixture.deleted).toEqual(["coder/output-style"]);
    await expect(api.getSection("coder", "output-style")).resolves.toMatchObject({
      content: FIXTURE_CONTENT["output-style"].content,
      overridden: false,
    });
    await expect(api.listSections("coder")).resolves.toMatchObject({
      sections: expect.arrayContaining([
        expect.objectContaining({ key: "output-style", overridden: false }),
      ]),
    });
  });

  it("fetches the rendered preview", async () => {
    const fixture = fixtureTransport();
    const api = new PromptsApi(fixture.transport);
    const preview = await api.preview("coder");
    expect(preview).toEqual(FIXTURE_PREVIEW);
    expect(fixture.calls[0].path).toBe("/prompts/coder/preview");
  });

  it("propagates server errors with their status", async () => {
    const api = new PromptsApi(fixtureTransport().transport);
    await expect(api.getSection("coder", "missing")).rejects.toThrow(
      /Section not found/,
    );
  });
});

describe("PromptsApi libraries and compositions", () => {
  it("lists libraries and roles", async () => {
    const fixture = fixtureTransport();
    const result = await new PromptsApi(fixture.transport).listLibraries();
    expect(result.roles).toEqual(FIXTURE_ROLES);
    expect(result.libraries.map((l) => l.name)).toEqual(["defaults", "local-lite"]);
    expect(fixture.calls[0].path).toBe("/prompts/libraries");
  });

  it("saves and deletes library sections under encoded paths", async () => {
    const fixture = fixtureTransport();
    const api = new PromptsApi(fixture.transport);
    await api.saveLibrarySection("local lite", "tool workflow", "Short.\n");
    expect(fixture.calls[0].path).toBe("/prompts/libraries/local%20lite/sections/tool%20workflow");
    expect(fixture.calls[0].init.method).toBe("PUT");
    expect(fixture.saved["library:local lite/tool workflow"]).toBe("Short.\n");
    await api.deleteLibrarySection("defaults", "tool-workflow");
    expect(fixture.calls[1].init.method).toBe("DELETE");
    expect(fixture.deleted).toEqual(["library:defaults/tool-workflow"]);
  });

  it("reads and writes a role's composition", async () => {
    const fixture = fixtureTransport();
    const api = new PromptsApi(fixture.transport);
    await expect(api.getComposition("coder")).resolves.toEqual(FIXTURE_COMPOSITION);
    await api.saveComposition("coder", FIXTURE_COMPOSITION);
    expect(fixture.calls[1]).toMatchObject({ path: "/prompts/coder/composition" });
    expect(fixture.calls[1].init.method).toBe("PUT");
    expect(JSON.parse(fixture.saved.composition)).toEqual(FIXTURE_COMPOSITION);
  });

  it("previews a named variant", async () => {
    const fixture = fixtureTransport();
    await new PromptsApi(fixture.transport).preview("coder", "local");
    expect(fixture.calls[0].path).toBe("/prompts/coder/preview?variant=local");
  });
});

const [DEFAULTS, LOCAL_LITE] = FIXTURE_LIBRARIES;

describe("prompts model", () => {
  it("titles keys and folders", () => {
    expect(titleFromKey("01-tool-workflow")).toBe("Tool Workflow");
    expect(titleFromKey("mcp")).toBe("MCP");
    expect(sectionFolder("bundles/agents/_base/sections/protocols/x.md")).toBe("Protocols");
    expect(sectionFolder("bundles/agents/_base/sections/00-header.md")).toBe("Core");
    expect(sectionFolder(null)).toBe("Core");
    expect(variantLabel("all")).toBe("All Models");
    expect(variantLabel("local")).toBe("Local Models");
    expect(variantNames(FIXTURE_COMPOSITION)).toEqual(["all", "local"]);
    expect(variantNames(null)).toEqual(["all"]);
  });

  it("orders a role in render order: role, libraries, per-turn last", () => {
    const groups = roleGroups(FIXTURE_ROLE_SECTIONS, FIXTURE_LIBRARIES, variantOf(FIXTURE_COMPOSITION, "all"));
    expect(groups.map((g) => g.name)).toEqual(["Role Sections", "Core", "Protocols", "Sections", "Per-Turn Context"]);
    expect(groups[0].rows.map((r) => r.key)).toEqual(["identity", "output-style", "skills"]);
    const last = groups.at(-1)!.rows;
    expect(last.map((r) => r.key)).toEqual(["01-session-context"]);
    expect(last[0]).toMatchObject({ included: true, checkable: false });
  });

  it("ticks what each variant sends and marks the role's own copy", () => {
    const rows = (variant: string) =>
      Object.fromEntries(
        roleGroups(FIXTURE_ROLE_SECTIONS, FIXTURE_LIBRARIES, variantOf(FIXTURE_COMPOSITION, variant))
          .flatMap((g) => g.rows)
          .map((r) => [r.id, r]),
      );
    const all = rows("all");
    expect(all["defaults:notifications"].included).toBe(false);
    expect(all["defaults:tool-workflow"]).toMatchObject({ included: true, overridden: true });
    expect(all["local-lite:tool-workflow-lite"].included).toBe(false);
    const local = rows("local");
    expect(local["defaults:tool-workflow"].included).toBe(false);
    expect(local["defaults:notifications"].included).toBe(true);
    expect(local["local-lite:tool-workflow-lite"]).toMatchObject({ included: true, ref: "local-lite/tool-workflow-lite" });
  });

  it("toggles defaults by exclusion and user libraries by addition", () => {
    const groups = roleGroups(FIXTURE_ROLE_SECTIONS, FIXTURE_LIBRARIES, variantOf(FIXTURE_COMPOSITION, "all"));
    const row = (id: string) => groups.flatMap((g) => g.rows).find((r) => r.id === id)!;
    const on = toggleRow(FIXTURE_COMPOSITION, "all", row("defaults:notifications"), true);
    expect(on.variants.all.exclude).toEqual([]);
    expect(on.variants.local).toEqual(FIXTURE_COMPOSITION.variants.local);
    const off = toggleRow(on, "all", row("defaults:00-header"), false);
    expect(off.variants.all.exclude).toEqual(["00-header"]);
    const added = toggleRow(off, "all", row("local-lite:tool-workflow-lite"), true);
    expect(added.variants.all.add).toEqual(["local-lite/tool-workflow-lite"]);
    expect(toggleRow(added, "all", row("local-lite:tool-workflow-lite"), false).variants.all.add).toEqual([]);
    expect(toggleRow(null, "local", row("defaults:00-header"), false).variants.local.exclude).toEqual(["00-header"]);
  });

  it("totals what is sent and lists a library by folder", () => {
    const groups = roleGroups(FIXTURE_ROLE_SECTIONS, FIXTURE_LIBRARIES, variantOf(FIXTURE_COMPOSITION, "all"));
    expect(includedTotals(groups)).toEqual({ sections: 6, tokens: 84 + 210 + 512 + 49 + 392 + 1477 });
    expect(libraryGroups(DEFAULTS).map((g) => g.name)).toEqual(["Core", "Protocols", "Per-Turn Context"]);
    expect(libraryGroups(LOCAL_LITE)[0].rows[0]).toMatchObject({ title: "Tool Workflow Lite", usedBy: 0 });
  });

  it("detects dirty drafts and diffs lines", () => {
    expect(isDirty("changed", "stored")).toBe(true);
    expect(isDirty("stored", "stored")).toBe(false);
    expect(isDirty("typed", null)).toBe(true);
    expect(diffLines("a\nb", "a\nc")).toEqual([
      { mark: "-", text: "b" },
      { mark: " ", text: "a" },
      { mark: "+", text: "c" },
    ]);
  });
});

function renderView(overrides: Record<string, unknown> = {}) {
  const noop = () => {};
  return renderToString(
    jsx(PromptsView, {
      libraries: FIXTURE_LIBRARIES,
      roles: FIXTURE_ROLES,
      loadError: null,
      target: { kind: "role", name: "coder" },
      groups: roleGroups(FIXTURE_ROLE_SECTIONS, FIXTURE_LIBRARIES, variantOf(FIXTURE_COMPOSITION, "all")),
      composition: FIXTURE_COMPOSITION,
      variant: "all",
      selected: null,
      stored: null,
      draft: "",
      tab: "edit",
      preview: null,
      busy: false,
      message: null,
      editorOpen: false,
      onOpenTarget: noop,
      onSelectRow: noop,
      onToggleRow: noop,
      onVariant: noop,
      onAddVariant: noop,
      onAddLibrary: noop,
      onAddSection: noop,
      onDraftChange: noop,
      onTab: noop,
      onSave: noop,
      onReset: noop,
      onEditInLibrary: noop,
      onCloseEditor: noop,
      ...overrides,
    }),
  );
}

describe("PromptsView role (rendered)", () => {
  const html = renderView();

  it("renders sidebar, sections and editor", () => {
    for (const id of ["prompts-page", "prompts-sidebar", "prompts-sections", "prompts-editor", "prompts-target-select"]) {
      expect(html).toContain(`data-testid="${id}"`);
    }
  });

  it("lists libraries and roles in the sidebar", () => {
    expect(html.split('data-testid="library-row"').length - 1).toBe(2);
    expect(html.split('data-testid="role-row"').length - 1).toBe(2);
    expect(html).toContain("Defaults");
    expect(html).toContain("Local Lite");
  });

  it("shows variants, a checkbox per library row, and per-turn context", () => {
    expect(html).toContain("All Models");
    expect(html).toContain("Local Models");
    // four library rows plus the per-turn row, which shows ticked and disabled
    expect(html.split('data-testid="section-include"').length - 1).toBe(5);
    expect(html.split('disabled=""').length - 1).toBeGreaterThanOrEqual(1);
    expect(html).toContain("Per-Turn Context");
    expect(html).toContain("Session Context");
  });
});

describe("PromptsView editor (rendered)", () => {
  const groups = roleGroups(FIXTURE_ROLE_SECTIONS, FIXTURE_LIBRARIES, variantOf(FIXTURE_COMPOSITION, "all"));
  const row = (id: string) => groups.flatMap((g) => g.rows).find((r) => r.id === id)!;

  it("offers a role copy of a Defaults section with Reset and Edit in Library", () => {
    const content = FIXTURE_CONTENT["tool-workflow"].content;
    const html = renderView({
      selected: row("defaults:tool-workflow"),
      stored: { content, changed: true },
      draft: content,
      editorOpen: true,
    });
    expect(html).toContain('data-testid="section-content"');
    expect(html).toContain("Save for Coder");
    expect(html).toContain('data-testid="reset-section"');
    expect(html).toContain("Reset to Library");
    expect(html).toContain('data-testid="edit-in-library"');
  });

  it("keeps per-turn context read-only", () => {
    const html = renderView({
      selected: row("defaults:01-session-context"),
      stored: { content: "ctx", changed: false },
      draft: "ctx",
      editorOpen: true,
    });
    expect(html).toContain("Read-only: rendered each turn");
    expect(html).not.toContain('data-testid="save-section"');
  });

  it("shows a library's Used by counts", () => {
    const html = renderView({ target: { kind: "library", name: "defaults" }, groups: libraryGroups(DEFAULTS) });
    expect(html).toContain("Used by");
    expect(html).not.toContain('data-testid="section-include"');
  });
});
