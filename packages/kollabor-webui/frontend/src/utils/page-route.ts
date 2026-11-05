import { readLearnRoute, type LearnRoute } from "../learn/catalog.ts";

export type MainPageRoute =
  | { kind: "chat" }
  | { kind: "learn"; learn: LearnRoute }
  | { kind: "whats-new" };

const PHONE_APP_PREFIX = "/relay/v1/phone";

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
  const learn = readLearnRoute({ pathname, search: location.search });
  return learn ? { kind: "learn", learn } : { kind: "chat" };
}

export function navigateToWhatsNew() {
  window.history.pushState(null, "", pagePath("/whats-new", window.location.pathname));
  window.dispatchEvent(new PopStateEvent("popstate"));
}
