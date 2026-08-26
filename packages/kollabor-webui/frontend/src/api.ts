export type Session = {
  session_id: string;
  name?: string;
  profile?: string;
  model?: string;
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
};

export type HistoryMessage = {
  role: "system" | "user" | "assistant" | string;
  content?: string | null;
  timestamp?: string | null;
  metadata?: Record<string, unknown>;
  thinking?: string | null;
};

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
      try {
        const payload = (await response.json()) as { detail?: string };
        detail = payload.detail || detail;
      } catch {
        // Keep status text for non-JSON errors.
      }
      throw new Error(`${response.status}: ${detail}`);
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

  listAgentPool(refresh = false) {
    return this.json<{
      agents: AgentPoolEntry[];
      available?: string[];
      active?: string[];
    }>(`/agents${refresh ? "?refresh=true" : ""}`);
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
