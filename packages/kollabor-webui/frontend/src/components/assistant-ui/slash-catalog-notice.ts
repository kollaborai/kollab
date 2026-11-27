export type SlashCatalogStatus = "loading" | "ready" | "empty" | "unavailable" | "fallback";

/**
 * The line under the slash palette about the engine's command catalog, or null
 * when there is nothing to say. An empty catalog says nothing while plugin
 * commands are listed, because the palette is not empty then.
 */
export function slashCatalogNotice(status: SlashCatalogStatus, hasPluginCommands: boolean): string | null {
  switch (status) {
    case "ready":
      return null;
    case "loading":
      return "Loading commands for this session.";
    case "empty":
      return hasPluginCommands
        ? null
        : "No commands are available for this session. You can still type a slash command.";
    case "fallback":
      return "This engine does not provide a live command catalog. Showing built-in commands as a compatibility fallback.";
    case "unavailable":
      return "Commands couldn't be loaded for this session. Your draft is preserved.";
  }
}
