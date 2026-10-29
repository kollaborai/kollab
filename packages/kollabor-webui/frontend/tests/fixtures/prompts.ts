/**
 * Fixtures for the System Prompts page tests: the same shapes the engine's
 * prompt-section API returns (see src/prompts-api.ts for the contract).
 */
import type {
  Composition,
  PromptLibrary,
  PromptEntry,
  PromptSection,
  PromptSectionContent,
  PromptPreview,
} from "../../src/prompts-api";

export const FIXTURE_PROMPTS: PromptEntry[] = [
  { bundle: "research", name: "research", source: "bundled" },
  { bundle: "my-agent", name: "my-agent", source: "global" },
  { bundle: "coder", name: "coder", source: "bundled" },
  { bundle: "project-agent", name: "project-agent", source: "local" },
];

export const FIXTURE_SECTIONS: PromptSection[] = [
  {
    key: "identity",
    title: "Identity",
    order: 1,
    tokens: 84,
    kind: "editable",
    source_path: "bundles/agents/coder/sections/01-identity.md",
    overridden: false,
  },
  {
    key: "output-style",
    title: "Output style",
    order: 4,
    tokens: 210,
    kind: "editable",
    source_path: "bundles/agents/coder/sections/17-output.md",
    overridden: true,
  },
  {
    key: "hooks",
    title: "Hook system",
    order: 3,
    tokens: 1337,
    kind: "runtime",
    source_path: "runtime: status bar snapshot",
    overridden: true,
  },
  {
    key: "skills",
    title: "Skills",
    order: 2,
    tokens: 512,
    kind: "generated",
    source_path: "bundles/agents/coder/agent.json skills",
    overridden: false,
  },
];

/** The coder role also lists the _base sections it renders; tool-workflow has its own copy. */
export const FIXTURE_ROLE_SECTIONS: PromptSection[] = [
  ...FIXTURE_SECTIONS,
  {
    key: "tool-workflow",
    title: "Tool workflow",
    order: 5,
    tokens: 392,
    kind: "editable",
    source_path: "bundles/agents/_base/sections/protocols/tool-workflow.md",
    overridden: true,
  },
];

export const FIXTURE_LIBRARIES: PromptLibrary[] = [
  {
    name: "defaults",
    builtin: true,
    sections: [
      { key: "00-header", title: "", tokens: 49, kind: "editable", source_path: "bundles/agents/_base/sections/00-header.md", edited: false, used_by: 2 },
      { key: "tool-workflow", title: "", tokens: 392, kind: "editable", source_path: "bundles/agents/_base/sections/protocols/tool-workflow.md", edited: true, used_by: 2 },
      { key: "notifications", title: "", tokens: 168, kind: "editable", source_path: "bundles/agents/_base/sections/protocols/notifications.md", edited: false, used_by: 1 },
      { key: "01-session-context", title: "", tokens: 1477, kind: "runtime", source_path: "bundles/agents/_base/sections/01-session-context.md", edited: false, used_by: 2 },
    ],
  },
  {
    name: "local-lite",
    builtin: false,
    sections: [
      { key: "tool-workflow-lite", title: "Tool Workflow Lite", tokens: 120, kind: "editable", source_path: null, edited: false, used_by: 0 },
    ],
  },
];

export const FIXTURE_ROLES = ["default", "coder"];

export const FIXTURE_COMPOSITION: Composition = {
  variants: {
    all: { exclude: ["notifications"], add: [] },
    local: { exclude: ["tool-workflow"], add: ["local-lite/tool-workflow-lite"], providers: ["ollama"] },
  },
};

export const FIXTURE_CONTENT: Record<string, PromptSectionContent> = {
  identity: {
    content: "You are Coder, a fast implementation agent.\n",
    overridden: false,
    tokens: 10,
  },
  "output-style": {
    content: "Keep each line under about 90 characters.\n",
    overridden: true,
    tokens: 10,
  },
  skills: {
    content: "# Skills\n\nBundled: debugging, refactoring\n",
    overridden: false,
    tokens: 10,
  },
  "tool-workflow": {
    content: "Coder's own tool workflow.\n",
    overridden: true,
    tokens: 6,
  },
  hooks: {
    content: "## Hook system snapshot\n\npre_user_input -> pre_api_request\n",
    overridden: true,
    tokens: 10,
  },
};

export const FIXTURE_PREVIEW: PromptPreview = {
  rendered: "You are Coder, a fast implementation agent.\n\n# Skills\n\nBundled: debugging, refactoring\n",
  tokens: 2143,
};

/** Arranges a PromptsTransport over the fixtures and records every call. */
export function fixtureTransport() {
  const calls: { path: string; init: RequestInit }[] = [];
  const saved: Record<string, string> = {};
  const deleted: string[] = [];
  return {
    calls,
    saved,
    deleted,
    transport: {
      request: (path: string, init: RequestInit = {}): Promise<Response> => {
        calls.push({ path, init });
        const method = (init.method ?? "GET").toUpperCase();
        const sectionMatch = /^\/prompts\/([^/]+)\/sections\/([^/]+)$/.exec(path);
        const bundleMatch = /^\/prompts\/([^/]+)(\/(sections|preview))?$/.exec(path);
        const json = (body: unknown, status = 200) =>
          Promise.resolve(new Response(JSON.stringify(body), { status }));
        const libraryMatch = /^\/prompts\/libraries\/([^/]+)\/sections\/([^/]+)$/.exec(path);
        if (path === "/prompts/libraries") {
          if (method === "POST") return json({ ok: true });
          return json({ libraries: FIXTURE_LIBRARIES, roles: FIXTURE_ROLES });
        }
        if (libraryMatch) {
          const key = `library:${decodeURIComponent(libraryMatch[1])}/${decodeURIComponent(libraryMatch[2])}`;
          if (method === "PUT") {
            saved[key] = (JSON.parse(String(init.body)) as { content: string }).content;
            return json({ ok: true });
          }
          if (method === "DELETE") {
            deleted.push(key);
            delete saved[key];
            return json({ ok: true });
          }
          return json({ content: saved[key] ?? "Shared section text.\n", edited: Object.hasOwn(saved, key), tokens: 4 });
        }
        if (/^\/prompts\/[^/]+\/composition$/.test(path)) {
          if (method === "PUT") {
            saved["composition"] = String(init.body);
            return json({ ok: true });
          }
          return json(FIXTURE_COMPOSITION);
        }
        if (path === "/prompts") {
          return json({
            bundles: FIXTURE_PROMPTS.map((p) => ({
              bundle: p.bundle,
              source: p.source,
            })),
          });
        }
        if (path.startsWith("/prompts/coder/preview")) {
          return json({ prompt: FIXTURE_PREVIEW.rendered, tokens: FIXTURE_PREVIEW.tokens });
        }
        if (sectionMatch) {
          const [, bundle, key] = sectionMatch;
          const overrideKey = `${decodeURIComponent(bundle)}/${decodeURIComponent(key)}`;
          if (method === "PUT") {
            const body = JSON.parse(String(init.body)) as { content: string };
            saved[overrideKey] = body.content;
            const deletedIndex = deleted.indexOf(overrideKey);
            if (deletedIndex >= 0) deleted.splice(deletedIndex, 1);
            return json({ ok: true });
          }
          if (method === "DELETE") {
            deleted.push(overrideKey);
            delete saved[overrideKey];
            return json({ ok: true });
          }
          const section = FIXTURE_ROLE_SECTIONS.find((row) => row.key === decodeURIComponent(key));
          const content = section ? FIXTURE_CONTENT[section.key] : undefined;
          if (!section || !content) {
            return json({ detail: `Section not found: ${key}` }, 404);
          }
          const hasOverride = Object.hasOwn(saved, overrideKey);
          return json({
            ...content,
            content: saved[overrideKey] ?? content.content,
            overridden: hasOverride || (content.overridden && !deleted.includes(overrideKey)),
          });
        }
        if (bundleMatch && bundleMatch[3] === "sections") {
          const bundle = decodeURIComponent(bundleMatch[1]);
          if (bundle !== "coder") {
            return json({ sections: [] });
          }
          return json({
            sections: FIXTURE_ROLE_SECTIONS.map((section) => {
              const overrideKey = `${bundle}/${section.key}`;
              return {
                ...section,
                overridden:
                  Object.hasOwn(saved, overrideKey) ||
                  (section.overridden && !deleted.includes(overrideKey)),
              };
            }),
          });
        }
        return json({ detail: `Not found: ${path}` }, 404);
      },
    },
  };
}
