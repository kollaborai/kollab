/**
 * Typed client for the engine's prompt-section API behind the System Prompts
 * page. Routes follow the engine's top-level resource prefix convention
 * (`/profiles`, `/agents`, `/sessions`) — see server.py's dev proxy list:
 *
 *   GET    /prompts                              -> { bundles: [{ bundle, source }] }
 *   GET    /prompts/{bundle}/sections            -> { sections: PromptSection[] }
 *   GET    /prompts/{bundle}/sections/{key}      -> PromptSectionContent
 *   PUT    /prompts/{bundle}/sections/{key}      { content }  (upsert override)
 *   DELETE /prompts/{bundle}/sections/{key}      (remove the override)
 *   GET    /prompts/{bundle}/preview[?variant=]  -> { prompt, tokens }
 *   GET    /prompts/libraries                    -> { libraries, roles }
 *   POST   /prompts/libraries                    { name }
 *   GET|PUT|DELETE /prompts/libraries/{lib}/sections/{key}
 *   GET|PUT /prompts/{bundle}/composition        { variants }
 *
 * A role's own copy of a section lives in ~/.kollab/prompt-overrides; deleting
 * it falls back to the library text. Libraries live in ~/.kollab/prompt-libraries
 * ("defaults" edits reach every role) and each role's picks in ~/.kollab/prompt-roles.
 */
import { EngineApi } from "./api";

/** The agent tier a bundle resolves from (local overrides global overrides bundled). */
export type PromptSource = "bundled" | "global" | "local";

/** Editable sections can be overridden; generated/runtime are read-only. */
export type PromptSectionKind = "editable" | "generated" | "runtime";

export type PromptEntry = {
  bundle: string;
  name: string;
  source: PromptSource;
};

export type PromptSection = {
  key: string;
  title: string;
  order: number;
  /** Server-counted (tiktoken o200k). */
  tokens: number;
  kind: PromptSectionKind;
  source_path: string | null;
  overridden: boolean;
};

/** Raw section text; kind and source_path come from the section list row. */
export type PromptSectionContent = {
  content: string;
  overridden: boolean;
  tokens: number;
};

export type PromptPreview = {
  rendered: string;
  tokens: number;
};

export type PromptsIndex = { prompts: PromptEntry[] };

/** One section of a library; `used_by` counts roles whose All Models variant sends it. */
export type LibrarySection = {
  key: string;
  title: string;
  tokens: number;
  kind: PromptSectionKind;
  source_path: string | null;
  /** Defaults only: the shipped text has a saved edit. */
  edited: boolean;
  used_by: number;
};

/** "defaults" is the shipped _base set (builtin); the rest are the user's. */
export type PromptLibrary = {
  name: string;
  builtin: boolean;
  sections: LibrarySection[];
};

export type PromptLibraries = { libraries: PromptLibrary[]; roles: string[] };

/** A role's picks: drop _base keys, add "library/key" sections, per variant. */
export type CompositionVariant = {
  exclude: string[];
  add: string[];
  /** Providers that switch to this variant; absent on "all". */
  providers?: string[];
};

export type Composition = { variants: Record<string, CompositionVariant> };
export type PromptSections = { sections: PromptSection[] };

/** A non-2xx prompt-section response, with the server's `detail` when present. */
export class PromptsApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "PromptsApiError";
    this.status = status;
  }
}

/** Just the transport PromptsApi needs: EngineApi satisfies it in the app. */
export type PromptsTransport = {
  request: (path: string, init?: RequestInit) => Promise<Response>;
};

export class PromptsApi {
  private readonly transport: PromptsTransport;

  constructor(transport: PromptsTransport = new EngineApi()) {
    this.transport = transport;
  }

  async listPrompts(): Promise<PromptsIndex> {
    const { bundles } = await this.json<{
      bundles: { bundle: string; source: string }[];
    }>("/prompts");
    return {
      prompts: bundles.map((row) => ({
        bundle: row.bundle,
        name: row.bundle,
        source:
          row.source === "local" || row.source === "global"
            ? row.source
            : "bundled",
      })),
    };
  }

  listSections(bundle: string): Promise<PromptSections> {
    return this.json(`/prompts/${encodeURIComponent(bundle)}/sections`);
  }

  getSection(bundle: string, key: string): Promise<PromptSectionContent> {
    return this.json(
      `/prompts/${encodeURIComponent(bundle)}/sections/${encodeURIComponent(key)}`,
    );
  }

  /** Upserts this bundle's override for the section key. */
  async saveSection(
    bundle: string,
    key: string,
    content: string,
  ): Promise<void> {
    await this.check(
      await this.transport.request(
        `/prompts/${encodeURIComponent(bundle)}/sections/${encodeURIComponent(key)}`,
        { method: "PUT", body: JSON.stringify({ content }) },
      ),
    );
  }

  /** Deletes this bundle's override; the shipped section shows through. */
  async deleteOverride(bundle: string, key: string): Promise<void> {
    await this.check(
      await this.transport.request(
        `/prompts/${encodeURIComponent(bundle)}/sections/${encodeURIComponent(key)}`,
        { method: "DELETE" },
      ),
    );
  }

  async preview(bundle: string, variant?: string): Promise<PromptPreview> {
    const query = variant ? `?variant=${encodeURIComponent(variant)}` : "";
    const { prompt, tokens } = await this.json<{ prompt: string; tokens: number }>(
      `/prompts/${encodeURIComponent(bundle)}/preview${query}`,
    );
    return { rendered: prompt, tokens };
  }

  listLibraries(): Promise<PromptLibraries> {
    return this.json("/prompts/libraries");
  }

  async createLibrary(name: string): Promise<void> {
    await this.check(
      await this.transport.request("/prompts/libraries", {
        method: "POST",
        body: JSON.stringify({ name }),
      }),
    );
  }

  getLibrarySection(
    library: string,
    key: string,
  ): Promise<{ content: string; edited: boolean; tokens: number }> {
    return this.json(this.libraryPath(library, key));
  }

  /** Defaults: an edit every role gets. A user library: adds or replaces the section. */
  async saveLibrarySection(library: string, key: string, content: string): Promise<void> {
    await this.check(
      await this.transport.request(this.libraryPath(library, key), {
        method: "PUT",
        body: JSON.stringify({ content }),
      }),
    );
  }

  /** Defaults: back to the shipped text. A user library: deletes the section. */
  async deleteLibrarySection(library: string, key: string): Promise<void> {
    await this.check(
      await this.transport.request(this.libraryPath(library, key), { method: "DELETE" }),
    );
  }

  async getComposition(bundle: string): Promise<Composition> {
    const { variants } = await this.json<Composition>(
      `/prompts/${encodeURIComponent(bundle)}/composition`,
    );
    return { variants };
  }

  async saveComposition(bundle: string, composition: Composition): Promise<Composition> {
    const response = await this.transport.request(
      `/prompts/${encodeURIComponent(bundle)}/composition`,
      { method: "PUT", body: JSON.stringify(composition) },
    );
    await this.check(response);
    const { variants } = (await response.json()) as Composition;
    return { variants };
  }

  private libraryPath(library: string, key: string): string {
    return `/prompts/libraries/${encodeURIComponent(library)}/sections/${encodeURIComponent(key)}`;
  }

  private async json<T>(path: string): Promise<T> {
    const response = await this.transport.request(path);
    await this.check(response);
    return (await response.json()) as T;
  }

  /** Turns a non-2xx response into a PromptsApiError, like EngineApi does. */
  private async check(response: Response): Promise<void> {
    if (response.ok) return;
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown } | null;
      if (typeof body?.detail === "string" && body.detail) detail = body.detail;
    } catch {
      // Keep the status text for non-JSON errors.
    }
    throw new PromptsApiError(`${response.status}: ${detail}`, response.status);
  }
}
