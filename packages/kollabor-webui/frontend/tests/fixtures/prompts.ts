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
  { bundle: "research", name: "Research", source: "bundled" },
  { bundle: "my-agent", name: "My Agent", source: "user" },
  { bundle: "coder", name: "Coder", source: "bundled" },
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
    kind: "editable",
    source_path: "bundles/agents/coder/sections/01-identity.md",
    overridden: false,
    override_path: null,
  },
  "output-style": {
    content: "Keep each line under about 90 characters.\n",
    kind: "editable",
    source_path: "bundles/agents/coder/sections/17-output.md",
    overridden: true,
    override_path: ".kollab/agents/coder/overrides/output-style.md",
  },
  skills: {
    content: "# Skills\n\nBundled: debugging, refactoring\n",
    kind: "generated",
    source_path: "bundles/agents/coder/agent.json skills",
    overridden: false,
    override_path: null,
  },
  hooks: {
    content: "## Hook system snapshot\n\npre_user_input -> pre_api_request\n",
    kind: "runtime",
    source_path: "runtime: status bar snapshot",
    overridden: false,
    override_path: null,
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
        if (path === "/prompts") return json({ prompts: FIXTURE_PROMPTS });
        if (path === "/prompts/coder/preview") return json(FIXTURE_PREVIEW);
        if (sectionMatch) {
          const [, bundle, key] = sectionMatch;
          if (method === "PUT") {
            const body = JSON.parse(String(init.body)) as { content: string };
            saved[`${bundle}/${key}`] = body.content;
            return json({ ok: true });
          }
          if (method === "DELETE") {
            deleted.push(`${bundle}/${key}`);
            return json({ ok: true });
          }
          const section = FIXTURE_SECTIONS.find((row) => row.key === decodeURIComponent(key));
          const content = section ? FIXTURE_CONTENT[section.key] : undefined;
          if (!section || !content) {
            return json({ detail: `Section not found: ${key}` }, 404);
          }
          return json(content);
        }
        if (bundleMatch && bundleMatch[3] === "sections") {
          const bundle = decodeURIComponent(bundleMatch[1]);
          if (bundle !== "coder") {
            return json({ sections: [] });
          }
          return json({ sections: FIXTURE_SECTIONS });
        }
        return json({ detail: `Not found: ${path}` }, 404);
      },
    },
  };
}
