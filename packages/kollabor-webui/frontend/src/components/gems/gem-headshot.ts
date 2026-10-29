import type { EngineApi } from "@/api";
import { GEM_PAD, type GemProps } from "./gem-engine";
import { seasonFor } from "./gem-face";

// Activity, size and live state do not define a look. A change to them must not
// overwrite the portrait or cause uploads every time an agent changes state.
export function headshotSignature(p: GemProps): string {
  const { gem, caste, color, face, hat, season, cut, hair, hairColor, fur, furColor,
    hairKnobs, furKnobs, auras, sparkles, auraColor } = p;
  // "auto" draws the season of the day, so a new season is a new look (no pumpkin in December).
  const dressed = !season || season === "auto" ? seasonFor() : season;
  return JSON.stringify({ gem, caste, color, face, hat, season: dressed, cut, hair, hairColor,
    fur, furColor, hairKnobs, furKnobs, auras, sparkles, auraColor });
}

// One portrait per look and wakefulness: a tile in its own color never replaces the agent's
// portrait, and an offline gem (drawn asleep) keeps its own. The engine hashes the key.
export function headshotKey(key: string, p: GemProps): string {
  return `${key}#${p.live === false ? "asleep" : "awake"}#${headshotSignature(p)}`;
}

type Slot = { signature: string; pending: Promise<void> };
const slots = new WeakMap<EngineApi, Map<string, Slot>>();

/** Snapshot synchronously after painting; encode and upload off the render path. */
export function captureHeadshot(api: EngineApi, key: string, props: GemProps, source: HTMLCanvasElement) {
  if (!key || !source.width || !source.height) return;
  let byKey = slots.get(api);
  if (!byKey) { byKey = new Map(); slots.set(api, byKey); }
  const signature = headshotSignature(props);
  const previous = byKey.get(key);
  if (previous?.signature === signature) return;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 256;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const side = source.width / GEM_PAD;
  const inset = (source.width - side) / 2;
  ctx.drawImage(source, inset, inset, side, side, 0, 0, 256, 256);
  const encoded = new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  const map = byKey;
  const slot: Slot = { signature, pending: Promise.resolve() };
  slot.pending = (previous?.pending ?? Promise.resolve()).then(async () => {
    const png = await encoded;
    if (!png) throw new Error("Headshot encoding failed");
    await api.saveGemHeadshot(key, png);
  }).catch(() => {
    if (map.get(key) === slot) map.delete(key);
  });
  byKey.set(key, slot);
}
