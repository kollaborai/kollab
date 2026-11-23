import type { PanelActionResult, PanelField, SlashCommand } from "@/api";

export type SettingsTab =
  | "session"
  | "configuration"
  | "loadouts"
  | "model"
  | "setup"
  | "network"
  | "phonelink"
  | "voice"
  | "plugins"
  | "prompts";
export type PanelTab = Exclude<SettingsTab, "session" | "phonelink" | "plugins">;

/** A step to run once the tab is ready: `/connect code`, `/connect knocks`. */
export type PanelIntent = "new_code" | "knocks" | "halt";
export type PanelOpenRequest = { tab: SettingsTab; intent?: PanelIntent };

/** Tab order, labels and the scope line shown under the title. */
export const SETTINGS_TABS: readonly {
  id: SettingsTab;
  label: string;
  scope: string;
}[] = [
  { id: "session", label: "Session", scope: "This session only, not saved" },
  {
    id: "configuration",
    label: "Configuration",
    scope: "Saved to the Project or Global config file",
  },
  {
    id: "loadouts",
    label: "Loadouts",
    scope: "Saved to the provider profile; applies to every session using it",
  },
  {
    id: "model",
    label: "Model",
    scope:
      "Saved to the active provider profile; applies to every session using it",
  },
  {
    id: "setup",
    label: "Setup",
    scope: "Adds a provider profile and makes it the active one",
  },
  { id: "network", label: "Network", scope: "This device's agent network" },
  { id: "phonelink", label: "PhoneLink", scope: "Open this computer’s Kollab on your phone" },
  { id: "voice", label: "Voice", scope: "Saved for every chat on the device running Kollab" },
  { id: "plugins", label: "Plugins", scope: "Plugins installed on this computer" },
  {
    id: "prompts",
    label: "Prompts",
    scope: "Shared libraries and each role's picks, saved in ~/.kollab",
  },
];

/** The daemon panel behind each non-session tab (key == slash command). */
export const PANEL_NAME: Record<PanelTab, string> = {
  configuration: "config",
  loadouts: "llm",
  model: "model",
  setup: "setup",
  network: "connect",
  voice: "voice",
  prompts: "prompts",
};

const TAB_FOR_PANEL: Record<string, PanelTab> = {
  config: "configuration",
  llm: "loadouts",
  model: "model",
  setup: "setup",
  connect: "network",
  voice: "voice",
  prompts: "prompts",
};

const BARE_COMMAND = /^\/([A-Za-z0-9][A-Za-z0-9_-]*)(?:[ \t]+([A-Za-z]+))?$/;

/**
 * The panel a composer line opens, or null when it is an ordinary message.
 * Only a bare command opens one (`/config`, `/m`), plus `/connect code` and
 * `/connect knocks`. Anything with other arguments goes to the daemon as typed.
 */
export function panelCommandRequest(
  text: string,
  commands: readonly SlashCommand[],
): PanelOpenRequest | null {
  const match = BARE_COMMAND.exec(text.trim());
  if (!match) return null;
  const word = match[1].toLowerCase();
  const arg = match[2]?.toLowerCase();
  const known = commands.find(
    (command) =>
      command.name.toLowerCase() === word ||
      command.aliases?.some((alias) => alias.toLowerCase() === word),
  );
  const panel = known?.panel || known?.name.toLowerCase() || word;
  const tab = TAB_FOR_PANEL[panel];
  if (!tab) return null;
  // /voice alone toggles listening; only /voice config opens the settings.
  if (tab === "voice") return arg === "config" ? { tab } : null;
  if (!arg) return { tab };
  if (tab === "network" && arg === "code") return { tab, intent: "new_code" };
  if (tab === "network" && arg === "knocks") return { tab, intent: "knocks" };
  return null;
}

export function titleCase(id: string): string {
  return id
    .replace(/[-_]+/g, " ")
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

/** An agent bundle's name for people ("creative-writer" reads "Creative Writer"); none is the default one. The API keeps the id. */
export function bundleName(bundle?: string | null): string {
  return titleCase(bundle || "default");
}

/** An online agent's state for people ("idle" reads "Idle"); one that reports none is Online. Every list of agents reads its status here. */
export function agentStatus(state?: string | null): string {
  return state ? titleCase(state) : "Online";
}

/** What a dropdown option reads as: the label the daemon gave it, else the option itself. */
export function optionLabel(
  field: { option_labels?: Record<string, string> | null },
  option: string,
): string {
  return field.option_labels?.[option] ?? option;
}

/** Route text is an action payload, so an incomplete keystroke must stay local. */
export function pickerControlNeedsApply(panel: string, field: Pick<PanelField, "type">): boolean {
  return panel === "connect-knocks" && field.type === "text_input";
}

export type PanelControlSubmission = { path: string; value: unknown };

/** Settle just the submitted control, retaining other drafts and useful field errors. */
export function settleControlSubmission(
  current: { draft: Record<string, unknown>; errors: Record<string, string> },
  submitted: PanelControlSubmission,
  result: Pick<PanelActionResult, "ok" | "errors" | "message">,
): { draft: Record<string, unknown>; errors: Record<string, string> } {
  const errors = { ...current.errors, ...result.errors };
  if (!result.ok) {
    errors[submitted.path] = result.errors?.[submitted.path] || result.message || "That did not work.";
    return { draft: current.draft, errors };
  }
  delete errors[submitted.path];
  // A late response must not erase a newer edit to the same control.
  if (!(submitted.path in current.draft) || !Object.is(current.draft[submitted.path], submitted.value)) {
    return { draft: current.draft, errors };
  }
  const draft = { ...current.draft };
  delete draft[submitted.path];
  return { draft, errors };
}

export type PanelSummaryRow = {
  label: string;
  value: string;
  /** Only an explicit daemon opt-in on a nonempty string enables copying. */
  copyable?: true;
};

/** Summary rows, preserving the existing tuple and object-map contracts. */
export function summaryRows(
  summary: unknown,
): PanelSummaryRow[] {
  const text = (value: unknown) =>
    value === null || value === undefined ? "" : String(value);
  if (Array.isArray(summary)) {
    return summary.flatMap((item) => {
      if (Array.isArray(item)) {
        return [{ label: text(item[0]), value: text(item[1]) }];
      }
      if (item && typeof item === "object") {
        const row = item as Record<string, unknown>;
        const value = text(row.value);
        return [{
          label: text(row.label),
          value,
          ...(row.copyable === true && typeof row.value === "string" && value.trim()
            ? { copyable: true as const }
            : {}),
        }];
      }
      return [];
    });
  }
  if (summary && typeof summary === "object") {
    return Object.entries(summary).map(([label, value]) => ({
      label,
      value: text(value),
    }));
  }
  return [];
}

/** The address must come from the owning daemon, never from browser identity. */
export function networkSummaryRows(summary: unknown): PanelSummaryRow[] {
  const rows = summaryRows(summary);
  let hasAddress = false;
  const shown = rows.map((row) => {
    if (row.label.trim().toLowerCase() !== "knocks address") return row;
    hasAddress = true;
    return !row.value.trim() || row.value.trim().toLowerCase() === "unavailable"
      ? { label: row.label, value: "Unavailable" }
      : row;
  });
  return hasAddress ? shown : [...shown, { label: "Knocks Address", value: "Unavailable" }];
}

/**
 * `expires_at` as epoch milliseconds. The daemon sends epoch seconds; an ISO
 * string or a millisecond value is tolerated too.
 */
// ponytail: assumes the browser and daemon clocks agree (both on loopback);
// switch to server-sent remaining seconds if the engine ever serves remote browsers.
export function parseExpiry(expiresAt: unknown): number | null {
  if (typeof expiresAt === "number" && Number.isFinite(expiresAt)) {
    return expiresAt < 1e12 ? expiresAt * 1000 : expiresAt;
  }
  if (typeof expiresAt === "string" && expiresAt) {
    const parsed = Date.parse(expiresAt);
    return Number.isNaN(parsed) ? null : parsed;
  }
  return null;
}

// Where a save writes: each changed field's own file, else the panel's save target (#412).
export function savePaths(fields: Pick<PanelField, "path" | "save_path">[], changed: Record<string, unknown>, fallback: string): string[] {
  const files = [...new Set(fields.filter((f) => f.path in changed).map((f) => f.save_path || fallback))];
  return files.length ? files : [fallback];
}

/** A session-shaped object with only what the Settings host decision reads. */
export type HostSessionLike = { session_id: string; halted?: string; workspace?: string | null };

/** How App mounts the Settings host: a live session, the halt waiter, or sessionless setup. */
export type PanelHostMount = {
  mount: boolean;
  key: string;
  session: HostSessionLike | null;
};

/**
 * Whether and how App shows PanelHost. A live, unhalted session owns the
 * dialog; with none, an open Settings still mounts it - over a halted
 * folder as the halt waiter, otherwise sessionless (#1114/#1115: the setup
 * guide's Provider and Network steps open Settings before any session).
 */
export function panelHostMount(
  activeSession: HostSessionLike | null | undefined,
  settingsOpen: boolean,
  haltFolder: string,
): PanelHostMount {
  if (activeSession && !activeSession.halted) {
    return { mount: true, key: activeSession.session_id, session: activeSession };
  }
  if (!settingsOpen) return { mount: false, key: "", session: null };
  if (haltFolder) {
    return { mount: true, key: "halt", session: { session_id: "", workspace: haltFolder } };
  }
  return { mount: true, key: "setup", session: { session_id: "", workspace: "" } };
}

/**
 * The panel a sessionless Settings mount serves for one tab. A halted folder
 * opens on Halt and Resume; the setup mount serves the tab's own daemon
 * panel through the engine's sessionless routes (#1114/#1115).
 */
export function sessionlessPanelName(tab: PanelTab, haltFolder: string): string {
  return haltFolder ? "connect-halt" : PANEL_NAME[tab];
}

/** The tabs a sessionless Settings mount offers. */
export function sessionlessTabs(haltedFolder: boolean): readonly SettingsTab[] {
  return haltedFolder ? ["network"] : ["setup", "network"];
}
