/**
 * Fixtures for the System Prompts page tests: the same shapes the engine's
 * prompt-section API returns (see src/prompts-api.ts for the contract).
 */
import type {
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
    overridden: false,
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
  hooks: {
    content: "## Hook system snapshot\n\npre_user_input -> pre_api_request\n",
    overridden: false,
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
        if (path === "/prompts") {
          return json({
            bundles: FIXTURE_PROMPTS.map((p) => ({
              bundle: p.bundle,
              source: p.source,
            })),
          });
        }
        if (path === "/prompts/coder/preview") {
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
          const section = FIXTURE_SECTIONS.find((row) => row.key === decodeURIComponent(key));
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
            sections: FIXTURE_SECTIONS.map((section) => {
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
