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
 *   GET    /prompts/{bundle}/preview             -> { prompt, tokens }
 *
 * Overrides are per user, per bundle, and per section in
 * ~/.kollab/prompt-overrides; deleting one falls back to the shipped section.
 * The server only overrides sections a bundle already has; it cannot add keys.
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

  async preview(bundle: string): Promise<PromptPreview> {
    const { prompt, tokens } = await this.json<{ prompt: string; tokens: number }>(
      `/prompts/${encodeURIComponent(bundle)}/preview`,
    );
    return { rendered: prompt, tokens };
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
