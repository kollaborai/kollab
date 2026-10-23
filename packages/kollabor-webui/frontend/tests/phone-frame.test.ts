import { describe, expect, it } from "vitest";
import {
  decodePhoneFrame,
  encodePhoneFrame,
  PHONE_FRAME_MAX_BYTES,
  PHONE_FRAME_PREFIX,
  PHONE_RELAY_MAX_CIPHERTEXT_CHARS,
  PHONE_RELAY_MAX_FRAME_BYTES,
  supportsPhoneFrameCompression,
} from "../src/phonelink/phone-frame";

async function gzip(bytes: Uint8Array) {
  const source = new Response(bytes).body;
  if (!source) throw new Error("Missing test compression stream.");
  return new Uint8Array(
    await new Response(source.pipeThrough(new CompressionStream("gzip"))).arrayBuffer(),
  );
}

describe("PhoneLink frame codec", () => {
  it("compresses repetitive JSON and roundtrips it", async () => {
    if (!supportsPhoneFrameCompression()) return;
    const original = new TextEncoder().encode(JSON.stringify({ history: "synthetic ".repeat(20_000) }));

    const encoded = await encodePhoneFrame(original);

    expect(new TextDecoder().decode(encoded.subarray(0, PHONE_FRAME_PREFIX.length)))
      .toBe(PHONE_FRAME_PREFIX);
    expect(encoded.byteLength).toBeLessThan(original.byteLength);
    expect(await decodePhoneFrame(encoded)).toEqual(original);
  });

  it("preserves legacy raw packets and rejects oversized inputs", async () => {

    expect(PHONE_RELAY_MAX_FRAME_BYTES).toBe(512 * 1024);

    expect(PHONE_RELAY_MAX_CIPHERTEXT_CHARS).toBe(448 * 1024);
    const legacy = new TextEncoder().encode('{"op":"read","seq":1}');
    expect(await encodePhoneFrame(legacy)).toEqual(legacy);
    expect(await decodePhoneFrame(legacy)).toEqual(legacy);
    await expect(encodePhoneFrame(new Uint8Array(PHONE_FRAME_MAX_BYTES + 1)))
      .rejects.toThrow("256 KiB frame limit");
  });

  it("bounds gzip expansion and rejects unnegotiated compressed packets", async () => {
    if (!supportsPhoneFrameCompression()) return;
    const prefix = new TextEncoder().encode(PHONE_FRAME_PREFIX);
    const bomb = await gzip(new Uint8Array(PHONE_FRAME_MAX_BYTES + 1).fill(0x78));
    const marked = new Uint8Array(prefix.length + bomb.length);
    marked.set(prefix);
    marked.set(bomb, prefix.length);

    await expect(decodePhoneFrame(marked)).rejects.toThrow("Decompressed PhoneLink packet");
    await expect(decodePhoneFrame(marked, false)).rejects.toThrow("not negotiated");
  });
});
