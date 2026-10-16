/**
 * Typed client for the engine's prompt-section API behind the System Prompts
 * page. Routes follow the engine's top-level resource prefix convention
 * (`/profiles`, `/agents`, `/sessions`) — see server.py's dev proxy list:
 *
 *   GET    /prompts                              -> { prompts: PromptEntry[] }
 *   GET    /prompts/{bundle}/sections            -> { sections: PromptSection[] }
 *   GET    /prompts/{bundle}/sections/{key}      -> PromptSectionContent
 *   PUT    /prompts/{bundle}/sections/{key}      { content }  (upsert override)
 *   DELETE /prompts/{bundle}/sections/{key}      (remove the override)
 *   GET    /prompts/{bundle}/preview             -> { rendered, tokens }
 *
 * Overrides are per bundle per section: writing one stores an override file
 * next to the bundle; deleting it falls back to the shipped section.
 */
import { EngineApi } from "./api";

export type PromptSource = "bundled" | "user";

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
  source_path: string;
  overridden: boolean;
};

export type PromptSectionContent = {
  content: string;
  kind: PromptSectionKind;
  source_path: string;
  overridden: boolean;
  /** Where the override lives, set when `overridden`. */
  override_path?: string | null;
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

  listPrompts(): Promise<PromptsIndex> {
    return this.json("/prompts");
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

  preview(bundle: string): Promise<PromptPreview> {
    return this.json(`/prompts/${encodeURIComponent(bundle)}/preview`);
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
