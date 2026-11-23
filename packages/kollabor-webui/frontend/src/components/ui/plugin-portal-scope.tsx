import { createContext, useContext } from "react";

export type PluginPortalScope = {
  scoped: boolean;
  container: HTMLElement | null;
};

export const PluginPortalScopeContext = createContext<PluginPortalScope>({
  scoped: false,
  container: null,
});

/** The active plugin mount's portal destination; ordinary app portals keep using the document body. */
export function usePluginPortalScope(): { scoped: boolean; portalContainer: HTMLElement | undefined } {
  const { scoped, container } = useContext(PluginPortalScopeContext);
  return { scoped, portalContainer: scoped ? (container ?? undefined) : undefined };
}
