import { readLearnRoute, type LearnRoute } from "../learn/catalog.ts";

export type MainPageRoute =
  | { kind: "chat" }
  | { kind: "learn"; learn: LearnRoute }
  | { kind: "whats-new" };

const PHONE_APP_PREFIX = "/relay/v1/phone";

export function pagePath(path: string, currentPath: string): string {
  return currentPath === PHONE_APP_PREFIX || currentPath.startsWith(`${PHONE_APP_PREFIX}/`)
    ? `${PHONE_APP_PREFIX}${path}`
    : path;
}

export function readMainPageRoute(location: Pick<Location, "pathname" | "search">): MainPageRoute {
  const prefix = pagePath("", location.pathname);
  const pathname = prefix ? location.pathname.slice(prefix.length) || "/" : location.pathname;
  if (pathname === "/whats-new") return { kind: "whats-new" };
  const learn = readLearnRoute({ pathname, search: location.search });
  return learn ? { kind: "learn", learn } : { kind: "chat" };
}

export function navigateToWhatsNew() {
  window.history.pushState(null, "", pagePath("/whats-new", window.location.pathname));
  window.dispatchEvent(new PopStateEvent("popstate"));
}
