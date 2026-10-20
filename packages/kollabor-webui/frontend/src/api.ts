export type PhonePeer = {
  id: string;
  name: string;
  address: string;
  public_key: string;
  created: number;
};
export type PhoneSettings = {
  endpoint: string;
  server_public_key: string;
  subnet: string;
  allowed_ips: string;
  dns: string;
  portal_url: string;
};
export type PhoneState = {
  settings: PhoneSettings;
  peers: PhonePeer[];
  apply_configured: boolean;
  can_create: boolean;
};

export type Session = {
  harness?: string;
  provider?: string;
  acp_waiting?: boolean;
  acp_usage?: { used?: number; size?: number; cost?: { amount?: string | number; currency?: string } };
  acp_plan?: { entries?: Array<{ content: string; priority?: string; status?: string }> };
  session_id: string;
  conversation_id?: string;
  name?: string;
  profile?: string;
  model?: string;
  effort?: string;
  /** Whether the session's live model accepts image input. */
  supports_vision?: boolean;
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
  /** The running agent itself: its daemon's hub presence agent id, new for every process and the same whichever way this engine met it. A relaunched agent has another; what hiding a row keys on. */
  instance?: string;
  /** Started outside this engine (a terminal, another engine): it opens on first use and Delete is not offered, since the engine only detaches from it. */
  external?: boolean;
  /** The computer an agent opened through the network runs on (its id is agent@device); empty for this one. */
  device?: string;
  /** The network the opening went through, once memberships carry it (#158). */
  network?: string;
};

/** One gem's Gem Studio picks; ids are gem-face.ts eye/hat styles. */
/** `avatar` replaces the roster's picture for that agent everywhere (a picture is an inline data URL). */
/** A coat or aura color: sRGB 0-255, or "gem" for the body's own. */
export type LookColor = number[] | "gem";
export type GemLook = {
  face?: string;
  hat?: string;
  cut?: string;
  color?: number[];
  avatar?: AvatarSpec;
  /** Hair and fur styles (gem-face.ts HAIR_STYLES, FUR_STYLES) or "none", and their colors. */
  hair?: string;
  hairColor?: LookColor;
  fur?: string;
  furColor?: LookColor;
  /** The user's settings of the hair's and fur's knobs (gem-face.ts COAT_KNOBS), over their styles' own. */
  hairKnobs?: Record<string, number>;
  furKnobs?: Record<string, number>;
  /** Auras worn (gem-face.ts AURAS), how much it sparkles, and their one color. */
  auras?: string[];
  sparkles?: string;
  auraColor?: LookColor;
};

/** The Gem Studio's saved looks (engine: ~/.kollab/hub/appearance.json). */
export type GemAppearance = {
  /** "auto" follows the calendar (Halloween in October, Christmas in December). */
  season?: string;
  /** The random eyes and hat each pool gem got the first time it came alive in `home`; the engine rolls them. */
  born?: Record<string, Omit<GemLook, "color">>;
  /** What the user dressed each agent in, over its born look; keyed by gem-look.ts lookKey. */
  gems?: Record<string, GemLook>;
  /** The engine's own folder: a bare gem name is the agent there. */
  home?: string;
};

export type UpdateRelease = { version: string; tag_name: string; name: string; url: string; is_prerelease: boolean };
export type UpdateCheckResponse = {
  current_version: string;
  available: boolean;
  release: UpdateRelease | null;
  notes: Array<{ version: string; date: string; sections: Array<{ title: string; items: string[] }> }>;
};
export type UpdateApplyResponse = { success: boolean; changed: boolean; message: string; method: string };


export type HistoryMessage = {
  role: "system" | "user" | "assistant" | string;
  content?: unknown;
  timestamp?: string | null;
  metadata?: Record<string, unknown>;
  thinking?: string | null;
};

/** Why the session's last turn failed; it goes after `history_length` messages. */
export type TurnError = { message: string; history_length: number };

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

/**
 * The daemon prepends pending agent status (vault, hub) to a user turn as an
 * `<agent_hud>` block (kollabor/llm/agent_hud.py). That is model context, not
 * what the user typed; this returns the text after it.
 */
export function stripAgentHud(text: string): string {
  return text.replace(/^\s*<agent_hud>[\s\S]*?<\/agent_hud>\s*/, "");
}

export type PermissionPrompt = {
  type?: "permission_request";
  tool_id: string;
  tool_name: string;
  tool_type?: string;
  input?: Record<string, unknown>;
  risk_level?: string;
  risk_reason?: string;
  details?: Record<string, unknown> & {
    source?: string;
    acp_options?: Array<{ optionId: string; name: string; kind: string }>;
  };
};

/** Reasoning effort levels (kollabor_ai EFFORT_LEVELS, cheapest first).
 * Empty/undefined means the model default (no effort is sent). */
export const EFFORT_LEVELS = [
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
  "ultra",
] as const;
export type Effort = (typeof EFFORT_LEVELS)[number];

export type Profile = {
  name: string;
  provider?: string;
  model?: string;
  description?: string;
  base_url?: string;
  base_url_has_hidden_data?: boolean;
  acp_adapter?: string;
  extra_header_names?: string[];
  temperature?: number;
  effort?: string | null;
  streaming?: boolean;
  supports_tools?: boolean;
  supports_vision?: boolean;
};

export type ProfileWrite = {
  name: string;
  provider?: string;
  model?: string;
  /** Copy an eligible stored profile by name; hidden fields remain server-side. */
  copy_from?: string;
  api_key?: string;
  base_url?: string;
  temperature?: number;
  max_tokens?: number | null;
  description?: string;
  timeout?: number;
  top_p?: number | null;
  /** Reasoning effort pin; "" / undefined = model default. */
  effort?: string;
  streaming?: boolean;
  supports_tools?: boolean;
  extra_headers?: Record<string, string>;
};

export type ProfileUpdate = Partial<Omit<ProfileWrite, "name" | "copy_from">> & {
  new_name?: string;
};

/** How an agent is drawn: the 3D gem, or a flat tile (docs/specs/rosters-and-teams.md, Avatars). */
export type AvatarSpec =
  | { kind: "gem" }
  | { kind: "initials" }
  | { kind: "emoji"; value: string }
  /** `url` is a path the engine serves (it needs the bearer token) or an https address. */
  | { kind: "image"; url: string };

export type AgentPoolEntry = {
  name: string;
  identity?: string;
  agent_type?: string;
  /** The agent bundle it runs (the same word as agent_type); empty is the default one. */
  agent?: string;
  /** The roster the name belongs to ("" for the koordinator). */
  roster?: string;
  /** What a UI calls it: "Maya Lindqvist"; a gem's is its name. */
  display_name?: string;
  avatar?: AvatarSpec;
  role_aliases?: string[];
  personality?: string;
  caste?: string;
  available?: boolean;
  active?: boolean;
  state?: string;
  current_task?: string;
  /** Pool gem color [r, g, b], shared with the TUI; null for an agent the pool does not name. */
  color?: number[] | null;
  /** Live hub agent id; empty when nothing runs this identity. */
  agent_id?: string;
  /** Live but off the hub mesh: peers do not see it (Properties → Chat). */
  solo?: boolean;
  /** The folder the live agent runs in, on this computer. */
  project?: string;
  /** When it last ran in the asked folder (epoch seconds, its vault stream); null if never. */
  last_ran?: number | null;
  /** False for a live agent the pool does not name (lapis-2): listed, never launched as. Koordinator is launchable (first in the list). */
  pool?: boolean;
};

/** A persona as GET /rosters and GET /teams send it. */
export type Persona = {
  name: string;
  roster: string;
  display_name: string;
  avatar: AvatarSpec;
  color: number[];
  /** The agent bundle it runs; empty is the default one. */
  agent: string;
  role: string;
  personality: string;
  skills: string[];
  profile: string;
  prompt: string;
  caste: string;
  role_aliases: string[];
};

export type Roster = {
  name: string;
  display_name: string;
  description: string;
  avatar: AvatarSpec;
  source: "bundled" | "user" | "project";
  path: string;
  personas: Persona[];
};

/** A roster or team file the engine could not use, and why. */
export type LibraryProblem = { file: string; message: string };

/** GET /rosters: every roster, and the one this folder draws its agent names from. */
export type RostersResponse = {
  workspace: string;
  default: string;
  rosters: Roster[];
  problems: LibraryProblem[];
};

export type TeamMember = {
  persona: Persona;
  role: string;
  agent: string;
  profile: string;
  /** The team's instructions for this seat. */
  prompt: string;
  reports_to: string | null;
};

export type Team = {
  name: string;
  description: string;
  roster: string;
  lead: string;
  source: "bundled" | "user" | "project";
  path: string;
  members: TeamMember[];
};

export type TeamsResponse = { teams: Team[]; problems: LibraryProblem[] };

/** POST /teams/{name}/launch: who started, who was already running in the folder, who failed. */
export type TeamLaunchResult = {
  team: string;
  workspace: string;
  started: string[];
  skipped: { persona: string; reason: string }[];
  failed: { persona: string; error: string }[];
  sessions: Session[];
};

/** One running hub agent (a presence row). A name can run in several folders at once, so only `agent_id` tells two apart: it is a session's `instance`. */
export type LiveAgent = {
  name: string;
  agent_id: string;
  state: string;
  current_task: string;
  /** The tool it runs now (bare name and type, no arguments); null between tools. */
  current_tool?: { name: string; type: string } | null;
  /** Live but off the hub mesh: peers do not see it. */
  solo: boolean;
  /** The folder it runs in. */
  project: string;
};

/** GET /agents: the pool, as one folder (or the whole computer) sees it. */
export type AgentPoolResponse = {
  /** One entry per name; where several folders run a name, this folder's agent speaks for it. */
  agents: AgentPoolEntry[];
  available?: string[];
  active?: string[];
  /** Every running agent in that scope, one per process (`agents` has one per name). */
  live?: LiveAgent[];
};

export type AgentBundleEntry = {
  name: string;
  description?: string;
  profile?: string | null;
  skills?: string[];
};


/** A gem on another computer in the agent network (the relay directory). */
export type NetworkAgent = {
  name: string;
  device: string;
  handle?: string;
  state?: string;
  is_coordinator?: boolean;
  harness?: string;
  acp_waiting?: boolean;
  /** The tool it runs now (bare name and type, no arguments), from its device's directory; null between tools. */
  current_tool?: { name: string; type: string } | null;
  /** The workspace (folder label) the agent runs in, once memberships carry it (#158). */
  workspace?: string;
  /** The network the agent runs on, once memberships carry it (#158). */
  network?: string;
  /** When the network last listed it (epoch seconds); state "offline" once it left. */
  last_seen?: number;
};

/** An accepted knock contact (#158): a peer device this one linked across rooms.
 * Nothing is permitted with one until `/connect allow` — the section is
 * display-only in the web UI. */
export type ContactPeer = { key: string; label: string; device?: string; trust?: string };

/** One workspace's agents on one device, one network (#158). */
export type NetworkWorkspaceGroup = { label: string; agents: NetworkAgent[] };

/** One device's workspaces on one network (#158). */
export type NetworkDeviceGroup = { device: string; workspaces: NetworkWorkspaceGroup[] };

/** One network this device belongs to (#158): its devices grouped by workspace.
 * `live: false`: no agent here carries it now, so its rows are the last roster
 * seen and opening one first starts an agent in `folder`. */
export type NetworkGroup = {
  id: string;
  name: string;
  trust?: string;
  live?: boolean;
  folder?: string;
  devices: NetworkDeviceGroup[];
};

/** This computer's relay name and the agents on other computers; `remote` is empty off a network.
 * `live: false`: no agent here is on the network, so `remote` is the last roster seen and opening
 * one first starts an agent in `folder` to carry the network.
 * Multi-network (#158): `networks` groups those rows per membership
 * (NETWORKS > DEVICES > WORKSPACES > AGENTS) and `contacts` lists accepted
 * knock contacts; an engine without them falls back to the flat fields. */
export type AgentNetwork = {
  device: string;
  remote: NetworkAgent[];
  live?: boolean;
  folder?: string;
  networks?: NetworkGroup[];
  contacts?: ContactPeer[];
};

/** A folder a session can run in; `live` counts the agents running there. */
export type WorkspaceEntry = { path: string; name: string; live: number };

/** An agent that ran in the engine's folder and is not running: opening it starts it again. */
/** An agent that ran here and is not running. Its persona's look (name, picture, color) comes with it when it has one, whichever roster it is from. */
export type OfflineAgent = Pick<AgentPoolEntry, "display_name" | "avatar" | "color" | "caste"> & {
  identity: string;
  workspace: string;
  last_ran: number;
};

/** A sidebar row the user hid, for every client of this engine (engine: hidden_agents.py). `key` is hidden-agents.ts's; a running agent's entry names its `instance` and lasts as long as that process. */
export type HiddenAgent = { key: string; label: string; detail: string; instance?: string };

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
  server_version?: string;
  engine_version?: string;
  kollab_version?: string;
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
  /** Dropdown only: option -> the text to show; the option is still what is saved. */
  option_labels?: Record<string, string> | null;
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
  /** Opaque state the daemon wants back unchanged in every form action. */
  context?: Record<string, unknown> | null;
};

export type PanelForm = PanelBase & {
  kind: "form";
  sections: PanelSection[];
  actions?: PanelAction[];
  save_targets?: Record<string, string | null> | null;
};

export type PanelPicker = PanelBase & {
  kind: "picker";
  rows: PanelRow[];
  row_actions?: PanelAction[];
  toolbar_actions?: PanelAction[];
  controls?: PanelField[];
  empty_groups?: { group: string; reason: string }[];
};


export type AppRowState = "available" | "added" | "off" | "unavailable";

export type AppRow = PanelRow & {
  kind: "mcp" | "cli" | "skill";
  state: AppRowState;
  publisher?: string | null;
  homepage?: string | null;
  icon?: { path: string; hex: string } | null;
  tags?: string[] | null;
  note?: string | null;
  preview?: string | null;
};

export type AppsSection = { id: string; title: string; ids: string[] };

export type AppsPicker = Omit<PanelPicker, "rows"> & {
  rows: AppRow[];
  sections?: AppsSection[] | null;
};

export type AppsRegistryResult = AppsPicker & {
  query?: string | null;
  note?: string | null;
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

/** A failure as a sentence for the page: no HTTP status, capitalized. */
export function errorText(reason: unknown): string {
  const text = (reason instanceof Error ? reason.message : String(reason)).replace(/^\d{3}: /, "");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export class EngineApi {
  private baseUrl: string;
  private token: string | null;
  private config: Promise<EngineConfig | null> | null = null;

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
      if (response.status === 404) return false;
      if (!response.ok) {
        let detail = response.statusText || "WebUI authentication refresh failed.";
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
    } catch (error) {
      if (error instanceof ApiError) throw error;
      // Standalone builds may not have the WebUI config proxy.
    }
    return false;
  }

  async request(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
    // Requests fired on mount (the session poll) would otherwise go to the
    // default URL without a token before the config arrives.
    await this.loadConfig();
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

  /** Load the engine URL/token; report explicit WebUI access denials to the page. */
  loadConfig() {
    if (this.config) return this.config;
    const pending = (async () => {
      try {
        const response = await fetch("/api/config", { cache: "no-store" });
        if (response.status === 404) return null;
        if (!response.ok) {
          let detail = response.statusText || "WebUI configuration is unavailable.";
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
        const config = (await response.json()) as EngineConfig;
        if (config.engine_url) this.setBaseUrl(config.engine_url);
        if (config.token) this.setToken(config.token);
        return config;
      } catch (error) {
        if (error instanceof ApiError) throw error;
        // Standalone builds may not have the WebUI config proxy.
        return null;
      }
    })();
    this.config = pending.catch((error) => {
      this.config = null;
      throw error;
    });
    return this.config;
  }

  listSessions() {
    return this.json<{
      sessions: Session[];
      offline?: OfflineAgent[];
      network?: AgentNetwork;
      hidden?: HiddenAgent[];
      order?: string[];
      order_revision?: number;
      order_warning?: string | null;
    }>("/sessions");
  }

  phoneState() {
    return this.json<PhoneState>("/phone", { cache: "no-store" });
  }

  savePhoneSettings(settings: Partial<PhoneSettings>) {
    return this.json<PhoneState>("/phone/settings", {
      method: "PUT",
      body: JSON.stringify(settings),
      cache: "no-store",
    });
  }

  /** The QR in the answer is the only delivery of the phone's private key. */
  addPhone(name: string) {
    return this.json<{ peer: PhonePeer; qr_svg: string; applied: boolean }>("/phone/peers", {
      method: "POST",
      body: JSON.stringify({ name }),
      cache: "no-store",
    });
  }

  revokePhone(id: string) {
    return this.json<{ peers: PhonePeer[]; applied: boolean }>(
      `/phone/peers/${encodeURIComponent(id)}`,
      { method: "DELETE", cache: "no-store" },
    );
  }

  /** Hide a sidebar row for every client of this engine; resolves to the rows hidden now. */
  hideAgent(entry: HiddenAgent) {
    return this.json<{ hidden: HiddenAgent[] }>("/sidebar/hide", { method: "POST", body: JSON.stringify(entry) });
  }

  /** Read the engine-owned sidebar order and any recovery warning. */
  getSidebarOrder() {
    return this.json<{ order: string[]; revision: number; warning: string | null }>("/sidebar/order");
  }

  /** Save an order only when the engine revision still matches this client. */
  saveSidebarOrder(order: string[], revision: number) {
    return this.json<{ order: string[]; revision: number; warning: string | null }>("/sidebar/order", {
      method: "POST",
      body: JSON.stringify({ order, revision }),
    });
  }

  /** Show a hidden row again. */
  showAgent(key: string) {
    return this.json<{ hidden: HiddenAgent[] }>("/sidebar/unhide", { method: "POST", body: JSON.stringify({ key }) });
  }

  showAllAgents() {
    return this.json<{ hidden: HiddenAgent[] }>("/sidebar/unhide", { method: "POST", body: JSON.stringify({ all: true }) });
  }


  async checkForUpdate() {
    const response = await fetch("/api/update/check", { cache: "no-store" });
    if (!response.ok) throw new ApiError(`Update check failed: ${response.status}`, response.status, undefined);
    return (await response.json()) as UpdateCheckResponse;
  }

  async applyUpdate() {
    const response = await fetch("/api/update/apply", { method: "POST" });
    if (!response.ok) throw new ApiError(`Update failed: ${response.status}`, response.status, undefined);
    return (await response.json()) as UpdateApplyResponse;
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
    return this.json<{
      history: HistoryMessage[];
      last_turn_error?: TurnError | null;
      /** Where the history came from: the engine's mirror or this disk. */
      source?: "mirror" | "disk";
      /** True when a background reconcile is still refreshing it. */
      stale?: boolean;
    }>(`/sessions/${encodeURIComponent(sessionId)}/history${query}`);
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
   * onOpen runs once the engine holds the subscription (it subscribes before
   * it answers): an event that fired earlier is gone, so a caller that may
   * have missed one reloads then.
   */
  async streamEvents(
    sessionId: string,
    signal?: AbortSignal,
    onEvent?: (event: SessionEvent) => void,
    onOpen?: () => void,
  ): Promise<void> {
    const response = await this.request(
      `/sessions/${encodeURIComponent(sessionId)}/events`,
      { method: "GET", signal },
    );
    if (!response.body) throw new Error("event stream response has no body");
    onOpen?.();
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

  /** Puts the session's agent on the hub mesh, or takes it off, live. */
  setSessionHub(sessionId: string, enabled: boolean) {
    return this.json<{ ok: boolean; hub?: boolean }>(`/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      body: JSON.stringify({ hub: enabled }),
    });
  }

  cancel(sessionId: string) {
    return this.json<{ ok: boolean }>(
      `/sessions/${encodeURIComponent(sessionId)}/cancel`,
      { method: "POST" },
    );
  }

  cancelOwned(sessionId: string, requestId: string) {
    return this.json<{ ok: boolean; cancelled: boolean }>(
      `/sessions/${encodeURIComponent(sessionId)}/cancel-owned`,
      { method: "POST", body: JSON.stringify({ request_id: requestId }) },
    );
  }

  listProfiles() {
    return this.json<{
      profiles: Profile[];
      active?: string;
      /** Every provider the engine can run, for the profile editor. */
      providers?: string[];
      /** Invalid user profiles skipped while loading; names map to safe diagnostics. */
      invalid_profiles?: Record<string, string>;
    }>("/profiles");
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

  /** Every gem's live state on this computer, whatever folder it runs in: the sidebar's view. */
  listAgentPool(refresh = false) {
    return this.json<AgentPoolResponse>(`/agents?scope=computer${refresh ? "&refresh=true" : ""}`);
  }

  /** What New Session may start in one folder (empty: the engine's own).
   * `available` / `active` are that folder's only: a gem running in another folder is free here. */
  listLaunchPool(workspace = "") {
    const query = new URLSearchParams({ refresh: "true" });
    if (workspace) query.set("workspace", workspace);
    return this.json<AgentPoolResponse>(`/agents?${query}`);
  }

  /** Every roster the folder can draw from (its own project rosters included), and files that failed to load. */
  listRosters(workspace = "") {
    const query = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
    return this.json<RostersResponse>(`/rosters${query}`);
  }

  listTeams(workspace = "") {
    const query = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
    return this.json<TeamsResponse>(`/teams${query}`);
  }

  /** Start every member of a team that is not already running in the folder, lead first. */
  launchTeam(name: string, workspace: string, profile?: string) {
    return this.json<TeamLaunchResult>(`/teams/${encodeURIComponent(name)}/launch`, {
      method: "POST",
      body: JSON.stringify({ workspace, ...(profile ? { profile } : {}) }),
    });
  }

  getGemAppearance() {
    return this.json<GemAppearance>("/agents/appearance");
  }

  saveGemAppearance(appearance: GemAppearance) {
    return this.json<GemAppearance>("/agents/appearance", {
      method: "PUT",
      body: JSON.stringify(appearance),
    });
  }

  /** The engine's folder (New Session's default) and the recent ones, with their live agents. */
  listWorkspaces() {
    return this.json<{ home: WorkspaceEntry; recent: WorkspaceEntry[] }>("/workspaces");
  }

  /** A folder's subfolders (empty path: the home folder). */
  browseWorkspace(path = "") {
    return this.json<{ path: string; name: string; parent: string | null; dirs: string[] }>(
      `/workspaces/browse?path=${encodeURIComponent(path)}`,
    );
  }

  /**
   * Agent bundles (`--agent <name>` in the CLI): the prompt+metadata tier,
   * distinct from the gem identity pool. Any of these can be passed as the
   * `agent` field when creating a session in the same folder: `workspace`
   * (empty: the engine's own) adds that folder's own `.kollab/agents`.
   */
  listAgentBundles(workspace = "") {
    const query = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
    return this.json<{ bundles: AgentBundleEntry[]; count?: number }>(
      `/agents/bundles${query}`,
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

  sendHubMessage(target: string, content: string, fromIdentity = "webui") {
    return this.json<{ ok: boolean; target: string }>("/hub/messages", {
      method: "POST",
      body: JSON.stringify({ target, content, from_identity: fromIdentity }),
    });
  }
}
