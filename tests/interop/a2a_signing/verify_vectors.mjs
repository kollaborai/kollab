import { createPrivateKey, createPublicKey, sign, verify } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

const cardPath = process.argv[2];
const outputPath = process.argv[3];
if (!cardPath || !outputPath) {
  throw new Error("usage: node verify_vectors.mjs PYTHON_SIGNED_CARD JS_SIGNED_OUTPUT");
}

const pythonSigned = JSON.parse(readFileSync(cardPath, "utf8"));
const pythonSignature = pythonSigned.signatures?.[0];
if (!pythonSignature || pythonSigned.signatures.length !== 1) {
  throw new Error("Python vector must contain exactly one AgentCardSignature");
}

function canonicalize(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) {
      throw new Error("number outside the JCS/I-JSON range");
    }
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonicalize).join(",")}]`;
  }
  if (typeof value === "object") {
    const keys = Object.keys(value).sort(); // ECMAScript string order is UTF-16 code-unit order.
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`).join(",")}}`;
  }
  throw new Error(`unsupported JCS value type: ${typeof value}`);
}

function b64urlDecode(text) {
  if (typeof text !== "string" || !/^[A-Za-z0-9_-]+$/.test(text)) {
    throw new Error("invalid base64url value");
  }
  const raw = Buffer.from(text, "base64url");
  if (raw.toString("base64url") !== text) {
    throw new Error("noncanonical base64url value");
  }
  return raw;
}

function b64urlEncode(value) {
  return Buffer.from(value).toString("base64url");
}

const protectedBytes = b64urlDecode(pythonSignature.protected);
const protectedHeader = JSON.parse(protectedBytes.toString("utf8"));
if (canonicalize(protectedHeader) !== protectedBytes.toString("utf8")) {
  throw new Error("protected header is not canonical JCS");
}
if (protectedHeader.alg !== "EdDSA" || protectedHeader.typ !== "JOSE") {
  throw new Error("Python vector uses an unsupported protected header");
}
const publicKeyHex = protectedHeader.kid?.match(/^ed25519:([0-9a-f]{64})$/)?.[1];
if (!publicKeyHex) {
  throw new Error("Python vector kid is not the deterministic Ed25519 key id");
}
const unsigned = { ...pythonSigned };
delete unsigned.signatures;
const payload = Buffer.from(canonicalize(unsigned), "utf8");
const signingInput = Buffer.from(`${pythonSignature.protected}.${b64urlEncode(payload)}`, "ascii");
const spkiPrefix = Buffer.from("302a300506032b6570032100", "hex");
const publicKey = createPublicKey({
  key: Buffer.concat([spkiPrefix, Buffer.from(publicKeyHex, "hex")]),
  format: "der",
  type: "spki",
});
if (!verify(null, signingInput, publicKey, b64urlDecode(pythonSignature.signature))) {
  throw new Error("Node.js rejected the Python EdDSA signature");
}

// Sign the same unsigned payload in Node so Python verifies the reverse path.
const testSeed = Buffer.from(Array.from({ length: 32 }, (_, index) => index));
const pkcs8Prefix = Buffer.from("302e020100300506032b657004220420", "hex");
const privateKey = createPrivateKey({
  key: Buffer.concat([pkcs8Prefix, testSeed]),
  format: "der",
  type: "pkcs8",
});
const nodePublicKey = createPublicKey(privateKey);
const nodePublicKeyDer = nodePublicKey.export({ format: "der", type: "spki" });
const rawNodePublicKey = nodePublicKeyDer.subarray(nodePublicKeyDer.length - 32);
const nodeKid = `ed25519:${rawNodePublicKey.toString("hex")}`;
const headerBytes = Buffer.from(canonicalize({ alg: "EdDSA", kid: nodeKid, typ: "JOSE" }), "utf8");
const protectedHeaderEncoded = b64urlEncode(headerBytes);
const nodeSigningInput = Buffer.from(
  `${protectedHeaderEncoded}.${b64urlEncode(payload)}`,
  "ascii",
);
const nodeSignature = sign(null, nodeSigningInput, privateKey);
const nodeSigned = {
  ...unsigned,
  signatures: [{ protected: protectedHeaderEncoded, signature: b64urlEncode(nodeSignature) }],
};
writeFileSync(outputPath, `${JSON.stringify(nodeSigned)}\n`, { encoding: "utf8", mode: 0o600 });
process.stdout.write("Python-to-Node verified; Node-to-Python vector written.\n");
