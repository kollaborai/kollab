export type Session = {
  session_id: string;
  name?: string;
  profile?: string;
  model?: string;
  effort?: string;
  agent?: string;
  workspace?: string | null;
  approval_mode?: string;
  history_length?: number;
  active?: boolean;
  total_turns?: number;
  total_input_tokens?: number;
  total_output_tokens?: number;
  identity?: string;
  daemon_pid?: number;
  /** False for metadata-only rows discovered from external runtimes. */
  attachable?: boolean;
  /** Actions supported by the backing runtime for this session row. */
  actions_supported?: string[];
};

export type HistoryMessage = {
  role: "system" | "user" | "assistant" | string;
  content?: unknown;
  timestamp?: string | null;
  metadata?: Record<string, unknown>;
  thinking?: string | null;
};

export function historyContentToText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";

  let imageIndex = 0;
  return content
    .flatMap((rawPart) => {
      if (!rawPart || typeof rawPart !== "object") return [];
      const part = rawPart as Record<string, unknown>;
      if (part.type === "text" && typeof part.text === "string") {
        return [part.text];
      }
      if (part.type === "image") {
        imageIndex += 1;
        return [`[image${imageIndex}]`];
      }
      return [];
    })
    .join("\n");
}

export function isToolOutputBatch(
  message: Pick<HistoryMessage, "metadata">,
): boolean {
  const value = message.metadata?.tool_output_batch;
  return value === true || value === "true";
}

export type PermissionPrompt = {
  type?: "permission_request";
  tool_id: string;
  tool_name: string;
  tool_type?: string;
  input?: Record<string, unknown>;
  risk_level?: string;
  risk_reason?: string;
};

export type Profile = {
  name: string;
  provider?: string;
  model?: string;
  description?: string;
  supports_vision?: boolean;
};

export type ProfileWrite = {
  name: string;
  provider: string;
  model: string;
  api_key?: string;
  base_url?: string;
  temperature?: number;
  max_tokens?: number | null;
  description?: string;
  timeout?: number;
  top_p?: number | null;
  streaming?: boolean;
  supports_tools?: boolean;
  extra_headers?: Record<string, string>;
};

export type ProfileUpdate = Partial<Omit<ProfileWrite, "name">> & {
  new_name?: string;
};

export type AgentPoolEntry = {
  name: string;
  identity?: string;
  agent_type?: string;
  role_aliases?: string[];
  personality?: string;
  caste?: string;
  available?: boolean;
  active?: boolean;
  state?: string;
  current_task?: string;
};

export type AgentBundleEntry = {
  name: string;
  description?: string;
  profile?: string | null;
  skills?: string[];
};


export type SlashParameter = {
  name: string;
  type?: string;
  description?: string;
  required?: boolean;
  default?: unknown;
  choices?: string[];
};

export type SlashSubcommand = {
  name: string;
  args?: string;
  description?: string;
};

export type SlashCommand = {
  name: string;
  description?: string;
  aliases?: string[];
  category?: string;
  plugin?: string;
  icon?: string;
  mode?: string;
  /** Daemon panel this command opens (`config`, `llm`, ...), when it has one. */
  panel?: string;
  enabled?: boolean;
  parameters?: SlashParameter[];
  subcommands?: SlashSubcommand[];
};

// Keep the first paint useful when the browser is connected to an older
// engine that does not expose GET /sessions/{id}/commands yet. A current
// engine replaces this compatibility catalog with its complete core + plugin
// registry as soon as the request resolves.
export const DEFAULT_SLASH_COMMANDS: SlashCommand[] = [
  {
    name: "help",
    description: "Show available commands and usage",
    aliases: ["h", "?"],
  },
  { name: "status", description: "Show session and runtime status" },
  {
    name: "permissions",
    description: "Manage tool execution permissions",
    aliases: ["perms", "security", "permission"],
    category: "system",
    subcommands: [
      { name: "show", description: "Show current permission settings" },
      {
        name: "default",
        description: "Use DEFAULT mode (HIGH risk only)",
      },
      {
        name: "strict",
        description: "Use CONFIRM_ALL mode (prompt everything)",
      },
      {
        name: "trust",
        description: "Use TRUST_ALL mode (approve everything)",
      },
      { name: "stats", description: "Show permission statistics" },
      { name: "clear", description: "Clear session approvals" },
    ],
  },
  {
    name: "mode",
    description: "Switch terminal contrast mode for dark or light backgrounds",
    aliases: ["contrast"],
    category: "ui",
    subcommands: [
      {
        name: "dark",
        description: "Use light text for dark terminal backgrounds",
      },
      {
        name: "light",
        description: "Use dark text for light terminal backgrounds",
      },
    ],
  },
  { name: "model", description: "Choose the active model" },
  { name: "agent", description: "Manage the active agent" },
  { name: "mcp", description: "Show MCP server status" },
  { name: "context", description: "Show context and prompt details" },
  { name: "skills", description: "List available agent skills" },
  { name: "config", description: "Open system configuration" },
  { name: "setup", description: "Run first-time setup" },
  { name: "doctor", description: "Run a readiness check" },
  { name: "login", description: "Sign in to a provider" },
  { name: "connect", description: "Open this device's agent network" },
  { name: "llm", description: "Choose a model loadout" },
  { name: "save", description: "Save the current conversation" },
  { name: "resume", description: "Resume a saved conversation" },
  { name: "terminal", description: "Manage terminal sessions" },
  { name: "cd", description: "Change the working directory" },
  { name: "compact", description: "Compact the current context" },
  { name: "version", description: "Show the Kollab version" },
  { name: "restart", description: "Clear conversation and start fresh session" },
  { name: "upgrade", description: "Update Kollab to the latest release" },
];

export type McpServer = {
  status: "connected" | "disconnected" | string;
  tool_count?: number;
  tools?: unknown[];
  error?: string;
};

export type McpServerConfig = {
  type?: string;
  command?: string;
  enabled?: boolean;
  description?: string;
  env?: Record<string, string>;
};

export type SessionMcp = {
  session_id: string;
  servers: Record<string, McpServer>;
  total_tools?: number;
};

export type HubAgent = {
  id?: string;
  agent_id?: string;
  identity?: string;
  status?: string;
  agent_name?: string;
  state?: string;
  current_task?: string;
  profile_name?: string;
  pid?: number;
  alive?: boolean;
  [key: string]: unknown;
};

export type SessionState = {
  profile?: Record<string, unknown> | null;
  agent?: Record<string, unknown> | null;
  system?: Record<string, unknown> | null;
  hub?: Record<string, unknown> | null;
  processing?: Record<string, unknown> | null;
};

export type EngineConfig = {
  engine_url?: string;
  token?: string;
};

export type SessionEvent = {
  type?: string;
  session_id?: string;
  tool_id?: string;
  tool_name?: string;
  scope?: string;
  message?: string;
  [key: string]: unknown;
};

/** Panels: daemon-owned screens (spec docs/specs/webui-unified-config.md). */

export type PanelFieldType =
  | "checkbox"
  | "slider"
  | "spinbox"
  | "dropdown"
  | "text_input"
  | "label";

/** One setting, as built by the daemon's `make_field`. */
export type PanelField = {
  /** Config path (forms) or payload key (wizards, picker controls). */
  path: string;
  type: PanelFieldType;
  label: string;
  help?: string | null;
  /** Always null for a secret; `is_set` says whether one exists. */
  value?: unknown;
  min_value?: number | null;
  max_value?: number | null;
  step?: number | null;
  options?: string[] | null;
  placeholder?: string | null;
  editable: boolean;
  managed_by?: string | null;
  secret?: boolean;
  is_set?: boolean;
  /** Picker controls only: action to send on change (default: `path`). */
  action?: string | null;
};

export type PanelSection = { id: string; title: string; fields: PanelField[] };

export type PanelAction = {
  id: string;
  label?: string | null;
  /** Save-style actions get one button per target (`local`, `global`). */
  targets?: string[] | null;
  /** Ask once more before sending (true, or the question to show). */
  confirm?: boolean | string | null;
  /** Row actions: payload key that carries the row id, besides `id`. */
  payload_key?: string | null;
};

export type PanelRow = {
  id: string;
  label: string;
  detail?: string | null;
  group?: string | null;
  current?: boolean;
  badges?: string[] | null;
  /** If set, only these row action ids are offered for the row. */
  actions?: string[] | null;
};

type PanelBase = {
  panel: string;
  title: string;
  scope_note?: string | null;
  /** Read-only label/value rows above a picker. */
  summary?: unknown;
  /** Read-only text shown above a picker. */
  notice?: string | null;
};

export type PanelForm = PanelBase & {
  kind: "form";
  sections: PanelSection[];
  actions?: PanelAction[];
  save_targets?: { local?: string | null; global?: string | null } | null;
};

export type PanelPicker = PanelBase & {
  kind: "picker";
  rows: PanelRow[];
  row_actions?: PanelAction[];
  toolbar_actions?: PanelAction[];
  controls?: PanelField[];
  empty_groups?: { group: string; reason: string }[];
};

export type PanelWizardStep = {
  id: string;
  title: string;
  fields: PanelField[];
};

export type PanelWizard = PanelBase & {
  kind: "wizard";
  steps: PanelWizardStep[];
  actions?: PanelAction[];
};

export type PanelDescription = PanelForm | PanelPicker | PanelWizard;

/** A one-time secret (join code). Never stored; shown with a countdown. */
export type PanelReveal = {
  label: string;
  value: string;
  /** Epoch seconds. */
  expires_at?: number | null;
  status?: string | null;
};

/** Re-send `action` every `every_s` seconds until a response has no `poll`. */
export type PanelPoll = {
  action: string;
  payload?: Record<string, unknown>;
  every_s?: number;
};

export type PanelActionResult = {
  ok: boolean;
  message?: string;
  errors?: Record<string, string>;
  /** Fresh describe() of the panel the action ran on. */
  panel?: PanelDescription | null;
  /** Another panel's describe(): show it on top of this one. */
  open?: PanelDescription | null;
  reveal?: PanelReveal | null;
  poll?: PanelPoll | null;
};

/** A 400 with per-field `errors`, as a failed result (any body shape). */
function panelFailure(body: unknown): PanelActionResult | null {
  if (!body || typeof body !== "object") return null;
  const record = body as Record<string, unknown>;
  const detail =
    record.detail && typeof record.detail === "object"
      ? (record.detail as Record<string, unknown>)
      : null;
  const raw = record.errors ?? detail?.errors;
  if (!raw || typeof raw !== "object") return null;
  const errors = Object.fromEntries(
    Object.entries(raw).map(([path, text]) => [path, String(text)]),
  );
  const message = [record.message, detail?.message, record.detail].find(
    (value): value is string => typeof value === "string" && value !== "",
  );
  return { ok: false, message: message ?? "Some values were rejected.", errors };
}

/** Every non-2xx engine response; `body` is the parsed JSON, if any. */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export class EngineApi {
  private baseUrl: string;
  private token: string | null;

  constructor(baseUrl = "http://127.0.0.1:7433") {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.token = null;
  }

  setBaseUrl(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  setToken(token: string | null) {
    this.token = token;
  }

  getToken() {
    return this.token;
  }

  assistantUrl(sessionId: string) {
    return `${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}/assistant`;
  }

  private headers(extra: HeadersInit = {}): Headers {
    const headers = new Headers(extra);
    headers.set("Content-Type", "application/json");
    if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
    return headers;
  }

  async refreshToken() {
    try {
      const response = await fetch("/api/config", { cache: "no-store" });
      if (!response.ok) return false;
      const config = (await response.json()) as EngineConfig;
      if (config.token && config.token !== this.token) {
        this.token = config.token;
        // A prior app version persisted this key. Remove it without making the
        // current token itself persistent in browser storage.
        try {
          localStorage.removeItem("kollabor_token");
        } catch {
          // Storage can be disabled in private browsing; the in-memory token is
          // still enough for this request and the next retry.
        }
        return true;
      }
    } catch {
      // Standalone builds may not have the webui config proxy.
    }
    return false;
  }

  async request(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: this.headers(init.headers),
    });
    if (response.status === 401 && retry && (await this.refreshToken())) {
      return this.request(path, init, false);
    }
    if (!response.ok) {
      let detail = response.statusText;
      let body: unknown;
      try {
        body = await response.json();
        const raw = (body as { detail?: unknown } | null)?.detail;
        if (typeof raw === "string" && raw) detail = raw;
      } catch {
        // Keep status text for non-JSON errors.
      }
      throw new ApiError(`${response.status}: ${detail}`, response.status, body);
    }
    return response;
  }

  async json<T>(path: string, init: RequestInit = {}) {
    return (await (await this.request(path, init)).json()) as T;
  }

  async loadConfig() {
    try {
      const response = await fetch("/api/config", { cache: "no-store" });
      if (!response.ok) return null;
      const config = (await response.json()) as EngineConfig;
      if (config.engine_url) this.setBaseUrl(config.engine_url);
      if (config.token) this.setToken(config.token);
      return config;
    } catch {
      return null;
    }
  }

  listSessions() {
    return this.json<{ sessions: Session[] }>("/sessions");
  }

  getSession(sessionId: string) {
    return this.json<Session>(`/sessions/${encodeURIComponent(sessionId)}`);
  }

  getSessionState(sessionId: string) {
    return this.json<SessionState>(
      `/sessions/${encodeURIComponent(sessionId)}/state`,
    );
  }

  listCommands(sessionId: string) {
    return this.json<{ session_id: string; commands: SlashCommand[] }>(
      `/sessions/${encodeURIComponent(sessionId)}/commands`,
    );
  }

  /** One panel's current screen. Query params carry non-sensitive filters only. */
  getPanel(
    sessionId: string,
    name: string,
    params: Record<string, string> = {},
    signal?: AbortSignal,
  ) {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, value]) => value !== ""),
    ).toString();
    return this.json<PanelDescription>(
      `/sessions/${encodeURIComponent(sessionId)}/panels/${encodeURIComponent(name)}${query ? `?${query}` : ""}`,
      { signal },
    );
  }

  /**
   * Run one panel action. Every payload goes in the POST body (secrets and
   * join codes never reach a URL). A 400 with per-field `errors` comes back as
   * `{ ok: false, errors }`; every other failure throws.
   */
  async panelAction(
    sessionId: string,
    name: string,
    action: string,
    payload: Record<string, unknown> = {},
    signal?: AbortSignal,
  ): Promise<PanelActionResult> {
    try {
      return await this.json<PanelActionResult>(
        `/sessions/${encodeURIComponent(sessionId)}/panels/${encodeURIComponent(name)}/actions/${encodeURIComponent(action)}`,
        { method: "POST", body: JSON.stringify(payload), signal },
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 400) {
        const failure = panelFailure(error.body);
        if (failure) return failure;
      }
      throw error;
    }
  }

  createSession(body: Record<string, unknown> = {}) {
    return this.json<Session>("/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  setSessionProfile(
    sessionId: string,
    name: string,
    model?: string,
    effort?: string,
  ) {
    return this.json<Session>(
      `/sessions/${encodeURIComponent(sessionId)}/profile`,
      {
        method: "POST",
        body: JSON.stringify({ name, model, effort }),
      },
    );
  }

  deleteSession(sessionId: string) {
    return this.json<{ ok: boolean }>(`/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
  }

  getHistory(sessionId: string, limit?: number) {
    const query = limit ? `?limit=${encodeURIComponent(limit)}` : "";
    return this.json<{ history: HistoryMessage[] }>(
      `/sessions/${encodeURIComponent(sessionId)}/history${query}`,
    );
  }

  clearHistory(sessionId: string) {
    return this.json<{ ok: boolean }>(
      `/sessions/${encodeURIComponent(sessionId)}/history`,
      { method: "DELETE" },
    );
  }

  getPermissions(sessionId: string) {
    return this.json<{
      pending_prompts: PermissionPrompt[];
      approval_mode?: string;
    }>(`/sessions/${encodeURIComponent(sessionId)}/permissions`);
  }

  /**
   * Follow the daemon's existing turn after a browser reload. The endpoint is
   * SSE and terminates at turn_complete; callers provide an AbortSignal so a
   * session switch can stop the reconnect without leaking a subscription.
   */
  async streamEvents(
    sessionId: string,
    signal?: AbortSignal,
    onEvent?: (event: SessionEvent) => void,
  ): Promise<void> {
    const response = await this.request(
      `/sessions/${encodeURIComponent(sessionId)}/events`,
      { method: "GET", signal },
    );
    if (!response.body) throw new Error("event stream response has no body");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() || "";
        for (const frame of frames) {
          const data = frame
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n");
          if (!data) continue;
          try {
            onEvent?.(JSON.parse(data) as SessionEvent);
          } catch {
            // Ignore keepalive/non-JSON frames while preserving the stream.
          }
        }
      }
      buffer += decoder.decode();
      if (buffer.trim()) {
        const data = buffer
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data) {
          try {
            onEvent?.(JSON.parse(data) as SessionEvent);
          } catch {
            // Ignore a partial trailing frame.
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  respondPermission(
    sessionId: string,
    toolId: string,
    decision: "approve" | "deny",
    scope = "once",
  ) {
    return this.json<{ ok: boolean }>(
      `/sessions/${encodeURIComponent(sessionId)}/permission`,
      {
        method: "POST",
        body: JSON.stringify({ tool_id: toolId, decision, scope }),
      },
    );
  }

  setApprovalMode(sessionId: string, mode: string) {
    return this.json<{ ok: boolean; mode?: string }>(
      `/sessions/${encodeURIComponent(sessionId)}/permissions/mode`,
      { method: "POST", body: JSON.stringify({ mode }) },
    );
  }

  cancel(sessionId: string) {
    return this.json<{ ok: boolean }>(
      `/sessions/${encodeURIComponent(sessionId)}/cancel`,
      { method: "POST" },
    );
  }

  listProfiles() {
    return this.json<{ profiles: Profile[]; active?: string }>("/profiles");
  }

  createProfile(body: ProfileWrite) {
    return this.json<Profile & { created?: boolean }>("/profiles", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  updateProfile(name: string, body: ProfileUpdate) {
    return this.json<Profile & { updated?: boolean }>(
      `/profiles/${encodeURIComponent(name)}`,
      { method: "PUT", body: JSON.stringify(body) },
    );
  }

  deleteProfile(name: string) {
    return this.json<{ deleted: boolean }>(
      `/profiles/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    );
  }

  testProfile(name: string) {
    return this.json<{
      success: boolean;
      message?: string;
      error?: string;
      latency_ms?: number;
      warning?: string;
    }>(`/profiles/${encodeURIComponent(name)}/test`, { method: "POST" });
  }

  listAgentPool(refresh = false) {
    return this.json<{
      agents: AgentPoolEntry[];
      available?: string[];
      active?: string[];
    }>(`/agents${refresh ? "?refresh=true" : ""}`);
  }

  /**
   * Agent bundles (`--agent <name>` in the CLI): the prompt+metadata tier,
   * distinct from the gem identity pool. Any of these can be passed as the
   * `agent` field when creating a session.
   */
  listAgentBundles() {
    return this.json<{ bundles: AgentBundleEntry[]; count?: number }>(
      "/agents/bundles",
    );
  }


  createMcpServer(body: McpServerConfig & { name: string }) {
    return this.json<McpServerConfig>("/mcp/servers", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  updateMcpServer(name: string, body: Partial<McpServerConfig>) {
    return this.json<McpServerConfig>(`/mcp/servers/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  }

  deleteMcpServer(name: string) {
    return this.json<{ deleted: boolean }>(
      `/mcp/servers/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    );
  }

  listMcpServers() {
    return this.json<{ servers: Record<string, McpServerConfig> }>(
      "/mcp/servers",
    );
  }

  getSessionMcp(sessionId: string) {
    return this.json<SessionMcp>(
      `/sessions/${encodeURIComponent(sessionId)}/mcp`,
    );
  }

  connectMcp(sessionId: string, serverName: string) {
    return this.json<{ ok: boolean; status?: string }>(
      `/sessions/${encodeURIComponent(sessionId)}/mcp/${encodeURIComponent(serverName)}/connect`,
      { method: "POST" },
    );
  }

  disconnectMcp(sessionId: string, serverName: string) {
    return this.json<{ ok: boolean; status?: string }>(
      `/sessions/${encodeURIComponent(sessionId)}/mcp/${encodeURIComponent(serverName)}/disconnect`,
      { method: "POST" },
    );
  }

  listHubAgents(refresh = false) {
    return this.json<{ agents: HubAgent[] }>(
      `/hub/agents${refresh ? "?refresh=true" : ""}`,
    );
  }

  getHubAgentStatus(agentId: string) {
    return this.json<{ agent_id: string; status?: Record<string, unknown>; error?: string }>(
      `/hub/agents/${encodeURIComponent(agentId)}/status`,
    );
  }

  getHubAgentOutput(agentId: string, lines = 80) {
    return this.json<{ agent_id: string; output?: string | null; error?: string }>(
      `/hub/agents/${encodeURIComponent(agentId)}/output?lines=${lines}`,
    );
  }

  sendHubMessage(target: string, content: string, fromIdentity = "webui") {
    return this.json<{ ok: boolean; target: string }>("/hub/messages", {
      method: "POST",
      body: JSON.stringify({ target, content, from_identity: fromIdentity }),
    });
  }
}
