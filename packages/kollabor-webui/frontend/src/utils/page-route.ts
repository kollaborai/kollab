import { readLearnRoute, type LearnRoute } from "../learn/catalog.ts";

export type MainPageRoute =
  | { kind: "chat" }
  | { kind: "learn"; learn: LearnRoute }
  | { kind: "whats-new" }
  | { kind: "plugin"; plugin: string; page: string };

const PHONE_APP_PREFIX = "/relay/v1/phone";
// A plugin id as GET /plugins lists it: a package id, or `<id>@dev` for a development folder.
const PLUGIN_ID = /^[a-z0-9][a-z0-9-]{1,39}(?:@dev)?$/;

/** True when the page is served inside the PhoneLink phone app. */
export function inPhoneApp(pathname: string): boolean {
  return pathname === PHONE_APP_PREFIX || pathname.startsWith(`${PHONE_APP_PREFIX}/`);
}

export function pagePath(path: string, currentPath: string): string {
  return inPhoneApp(currentPath) ? `${PHONE_APP_PREFIX}${path}` : path;
}

export function readMainPageRoute(location: Pick<Location, "pathname" | "search">): MainPageRoute {
  const prefix = pagePath("", location.pathname);
  const pathname = prefix ? location.pathname.slice(prefix.length) || "/" : location.pathname;
  if (pathname === "/whats-new") return { kind: "whats-new" };
  if (pathname === "/plugin") {
    const query = new URLSearchParams(location.search);
    const plugin = query.get("id") ?? "";
    const page = query.get("page") ?? "";
    if (PLUGIN_ID.test(plugin) && page) return { kind: "plugin", plugin, page };
  }
  const learn = readLearnRoute({ pathname, search: location.search });
  return learn ? { kind: "learn", learn } : { kind: "chat" };
}

export function navigateToWhatsNew() {
  window.history.pushState(null, "", pagePath("/whats-new", window.location.pathname));
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/** A plugin's page fills the main area (`k.page`, docs/specs/plugins.md); picking a chat leaves it. */
export function navigateToPluginPage(plugin: string, page: string) {
  const query = new URLSearchParams({ id: plugin, page });
  window.history.pushState(null, "", pagePath(`/plugin?${query}`, window.location.pathname));
  window.dispatchEvent(new PopStateEvent("popstate"));
}
