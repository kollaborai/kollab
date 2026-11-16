// Pure model for the What's New dialog: which release to announce, whether
// this browser should see it, and how the seen-version persists. No React,
// no fetch — everything here is testable with plain data.

import type { ChangeSection, WhatsNewResult } from "@/learn/changelog";

export const STORAGE_KEY = "kollab.whats-new.v1";

/** Entries the dialog lists before "And N more." */
export const MAX_LINES = 6;

/** The release a dialog would announce, built from the packaged changelog. */
export type DialogRelease = {
  version: string;
  /** Fallback for the "Updated from" subline when this browser has no record. */
  previous: string | null;
  added: string[];
  changed: string[];
  fixedCount: number;
};

/** What this browser last acknowledged, persisted per browser like the setup guide. */
export type SeenRecord = { seenVersion: string };

const VERSION_RE = /^\d+(\.\d+)*$/;

/** Numeric segment compare; non-versions fall back to string order, never NaN. */
export function compareVersions(a: string, b: string): number {
  if (!VERSION_RE.test(a) || !VERSION_RE.test(b)) {
    return a < b ? -1 : a > b ? 1 : 0;
  }
  const left = a.split(".").map(Number);
  const right = b.split(".").map(Number);
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const diff = (left[index] ?? 0) - (right[index] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

const itemsOf = (sections: ChangeSection[], category: string): string[] =>
  sections[0]?.groups.find((group) => group.category === category)?.items ?? [];

const countOf = (sections: ChangeSection[], category: string): number =>
  itemsOf(sections, category).length;

/**
 * The newest release section announces the dialog. An "Unreleased" section is
 * a dev build with no version to compare, so it announces nothing.
 */
export function buildRelease(result: WhatsNewResult): DialogRelease | null {
  if (result.status !== "ready") return null;
  const newest = result.sections[0];
  if (!newest || newest.version === "Unreleased" || !VERSION_RE.test(newest.version)) {
    return null;
  }
  const next = result.sections[1];
  const previous = next && next.version !== "Unreleased" ? next.version : null;
  return {
    version: newest.version,
    previous,
    added: itemsOf(result.sections, "Added"),
    changed: itemsOf(result.sections, "Changed"),
    fixedCount: countOf(result.sections, "Fixed"),
  };
}

/** Read persisted progress; malformed or foreign data falls back to never-seen. */
export function loadSeenRecord(raw: string | null): SeenRecord | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { seenVersion?: unknown };
    return typeof parsed.seenVersion === "string" && VERSION_RE.test(parsed.seenVersion)
      ? { seenVersion: parsed.seenVersion }
      : null;
  } catch {
    return null;
  }
}

export function saveSeenRecord(record: SeenRecord): string {
  return JSON.stringify({ seenVersion: record.seenVersion });
}

/* Persisted-record helpers that touch the browser. No-ops without a window. */

export function readWhatsNewSeenRaw(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function recordWhatsNewSeen(version: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, saveSeenRecord({ seenVersion: version }));
  } catch {
    // A private-mode browser that refuses storage still gets the dialog.
  }
}

/** Added then Changed, capped at MAX_LINES with the overflow counted. */
export function dialogLines(release: DialogRelease): {
  lines: string[];
  moreCount: number;
} {
  const all = [...release.added, ...release.changed];
  return { lines: all.slice(0, MAX_LINES), moreCount: Math.max(all.length - MAX_LINES, 0) };
}

/** "Updated from" source: what this browser saw, else the changelog's previous release. */
export function sublineVersion(release: DialogRelease, seen: SeenRecord | null): string | null {
  return seen?.seenVersion ?? release.previous;
}

/** The show/skip decision: first run, release-less, seen, and empty releases skip. */
export function decideWhatsNew(input: {
  result: WhatsNewResult;
  seenRaw: string | null;
  firstRun: boolean;
}): { show: boolean; release: DialogRelease | null } {
  const release = buildRelease(input.result);
  if (input.firstRun || !release) return { show: false, release };
  const seen = loadSeenRecord(input.seenRaw);
  if (seen && compareVersions(seen.seenVersion, release.version) >= 0) {
    return { show: false, release };
  }
  if (release.added.length + release.changed.length === 0) {
    return { show: false, release };
  }
  return { show: true, release };
}
