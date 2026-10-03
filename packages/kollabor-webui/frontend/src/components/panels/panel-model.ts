import type { SlashCommand } from "@/api";

export type SettingsTab =
  | "session"
  | "configuration"
  | "loadouts"
  | "model"
  | "setup"
  | "network";
export type PanelTab = Exclude<SettingsTab, "session">;

/** A step to run once the tab is ready: `/connect code`, `/connect knocks`. */
export type PanelIntent = "new_code" | "knocks";
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
];

/** The daemon panel behind each non-session tab (key == slash command). */
export const PANEL_NAME: Record<PanelTab, string> = {
  configuration: "config",
  loadouts: "llm",
  model: "model",
  setup: "setup",
  network: "connect",
};

const TAB_FOR_PANEL: Record<string, PanelTab> = {
  config: "configuration",
  llm: "loadouts",
  model: "model",
  setup: "setup",
  connect: "network",
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

/** Summary rows as label/value pairs, whichever of the usual shapes arrives. */
export function summaryRows(
  summary: unknown,
): { label: string; value: string }[] {
  const text = (value: unknown) =>
    value === null || value === undefined ? "" : String(value);
  if (Array.isArray(summary)) {
    return summary.flatMap((item) => {
      if (Array.isArray(item)) {
        return [{ label: text(item[0]), value: text(item[1]) }];
      }
      if (item && typeof item === "object") {
        const row = item as Record<string, unknown>;
        return [{ label: text(row.label), value: text(row.value) }];
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
