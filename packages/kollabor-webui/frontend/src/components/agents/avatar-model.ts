/**
 * What an agent's avatar is made of, apart from React: the letters of a flat
 * tile, its ink, and where an image avatar is fetched from. Pure, so
 * tests/avatar-model.test.ts runs it under node.
 */

import type { AvatarSpec } from "../../api.ts";

type Rgb = readonly [number, number, number];

/** Tile colors for an agent the roster gave none: stable per name, and distinct enough to tell apart. */
const FALLBACK_TINTS: readonly Rgb[] = [
  [92, 138, 214],
  [214, 124, 52],
  [64, 168, 120],
  [176, 96, 178],
  [196, 160, 64],
  [226, 98, 110],
  [46, 125, 107],
  [120, 110, 190],
];

/** The tile color: the persona's own, else one picked from its name. */
export function tileColor(color: readonly number[] | null | undefined, name: string): Rgb {
  if (color?.length === 3 && color.every((channel) => Number.isFinite(channel))) {
    return [color[0], color[1], color[2]];
  }
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return FALLBACK_TINTS[hash % FALLBACK_TINTS.length];
}

/** Light or dark ink, whichever reads on `rgb` (WCAG relative luminance). */
export function inkFor(rgb: Rgb): "#ffffff" | "#16181d" {
  const [r, g, b] = rgb.map((channel) => {
    const c = Math.min(255, Math.max(0, channel)) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.4 ? "#16181d" : "#ffffff";
}

/** "Maya Lindqvist" is ML, "Lapis" is L, "lapis-2" is L2: the first letters of its first and last word. */
export function initialsOf(displayName: string | null | undefined, name: string): string {
  const words = (displayName?.trim() || name).split(/[\s_-]+/).filter(Boolean);
  if (!words.length) return "?";
  const first = [...words[0]][0];
  const last = words.length > 1 ? [...words[words.length - 1]][0] : "";
  return (first + last).toUpperCase();
}

/**
 * Where an image avatar comes from. A served file (`/rosters/<roster>/avatars/...`)
 * needs the bearer token, which an <img> cannot send: it is fetched and shown
 * from a blob. An https address is shown as it is. A project roster's file is
 * found through the folder it belongs to.
 */
export function imageSource(
  avatar: Extract<AvatarSpec, { kind: "image" }>,
  folder = "",
): { direct: string } | { fetch: string } {
  if (/^https?:\/\//i.test(avatar.url)) return { direct: avatar.url };
  if (!folder) return { fetch: avatar.url };
  return { fetch: `${avatar.url}${avatar.url.includes("?") ? "&" : "?"}workspace=${encodeURIComponent(folder)}` };
}

/** A gem, or no avatar at all, is drawn as the 3D gem; any other avatar is a flat tile with no gem look to dress. */
export function isGemAvatar(avatar: AvatarSpec | null | undefined): avatar is { kind: "gem" } | null | undefined {
  return !avatar || avatar.kind === "gem";
}

/** The identity a New Session or a launch sends: a plain name, or `roster/name` for a roster the folder does not draw from. */
export function launchRef(persona: { name: string; roster?: string }, folderRoster: string): string {
  return persona.roster && persona.roster !== folderRoster ? `${persona.roster}/${persona.name}` : persona.name;
}
