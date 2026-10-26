export const PHONE_FRAME_MAX_BYTES = 256 * 1024;
export const PHONE_FRAME_CHUNK_BYTES = 160 * 1024;

export const PHONE_RELAY_MAX_FRAME_BYTES = 512 * 1024;

export const PHONE_RELAY_MAX_CIPHERTEXT_CHARS = 448 * 1024;
export const PHONE_FRAME_PREFIX = "KLPZ1";
const MAGIC = new TextEncoder().encode(PHONE_FRAME_PREFIX);
const COMPRESSION_THRESHOLD = 512;

export function supportsPhoneFrameCompression() {
  return typeof CompressionStream !== "undefined" &&
    typeof DecompressionStream !== "undefined";
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  return copy.buffer as ArrayBuffer;
}


async function gzip(bytes: Uint8Array) {
  const source = new Response(toArrayBuffer(bytes)).body;
  if (!source) throw new Error("PhoneLink compression stream is unavailable.");
  const stream = source.pipeThrough(new CompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function startsWithMagic(bytes: Uint8Array) {
  return MAGIC.every((value, index) => bytes[index] === value);
}

function join(chunks: Uint8Array[], size: number) {
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

export async function encodePhoneFrame(bytes: Uint8Array, enabled = true) {
  if (bytes.byteLength > PHONE_FRAME_MAX_BYTES) {
    throw new Error("PhoneLink packet exceeds the 256 KiB frame limit.");
  }
  if (!enabled || bytes.byteLength < COMPRESSION_THRESHOLD || !supportsPhoneFrameCompression()) {
    return bytes;
  }
  try {
    const compressed = await gzip(bytes);
    if (compressed.byteLength + MAGIC.byteLength >= bytes.byteLength) return bytes;
    return join([MAGIC, compressed], MAGIC.byteLength + compressed.byteLength);
  } catch {
    // Raw JSON remains valid for a peer that negotiated but cannot compress.
    return bytes;
  }
}

export async function decodePhoneFrame(bytes: Uint8Array, enabled = true) {
  if (bytes.byteLength > PHONE_FRAME_MAX_BYTES + MAGIC.byteLength) {
    throw new Error("PhoneLink frame exceeds the compressed frame limit.");
  }
  if (!startsWithMagic(bytes)) {
    if (bytes.byteLength > PHONE_FRAME_MAX_BYTES) {
      throw new Error("PhoneLink packet exceeds the 256 KiB frame limit.");
    }
    return bytes;
  }
  if (!enabled || !supportsPhoneFrameCompression()) {
    throw new Error("Compressed PhoneLink frame was not negotiated or is unsupported.");
  }

  const source = new Response(toArrayBuffer(bytes.subarray(MAGIC.byteLength))).body;
  if (!source) throw new Error("PhoneLink decompression stream is unavailable.");
  const reader = source.pipeThrough(new DecompressionStream("gzip")).getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > PHONE_FRAME_MAX_BYTES) {
        await reader.cancel();
        throw new Error("Decompressed PhoneLink packet exceeds the 256 KiB limit.");
      }
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  }
  return join(chunks, size);
}
