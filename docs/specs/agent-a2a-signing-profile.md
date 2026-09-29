# Kollab A2A Agent Card signing profile

Status: signing and resolver wiring included in Kollab 0.9.0; A2A service validation is tracked in the [implementation ledger](agent-network-simple-flow.md).

This profile lets Kollab point from its signed domain-discovery descriptor to
the standard A2A Agent Card path and verify that Card with the descriptor's
origin-pinned Ed25519 key. It does not turn a discovery publisher into an A2A
service. A publisher with no agent endpoint omits `endpoints.agent_card` and
continues to report identity only.

## Locator contract

The optional `endpoints.agent_card` value identifies the standard A2A Card at
`https://<same-origin>/.well-known/agent-card.json`.

- If the field is absent, there is no Agent Card. Do not infer an A2A service
  from the descriptor's registry or control fields.
- If present, the URL must use the exact HTTPS origin pinned by discovery and
  exactly `/.well-known/agent-card.json`. Alternate paths, redirects, queries,
  fragments, escaped paths, credentials, and cross-origin URLs are rejected.
- The Card fetcher must request that exact URL over HTTPS, refuse redirects,
  cap the response at 64 KiB, and enforce a short timeout. The signing helper
  exposes `decode_agent_card()` for bounded JSON decoding; transport remains
  owned by the resolver/service wiring.

The optional locator is a Kollab extension. The object at the URL remains an
A2A Agent Card; it is not a renamed Kollab descriptor.

## Signature profile

The profile uses the A2A `AgentCard.signatures` JWS array and the A2A JCS
payload. It narrows the general A2A signature options as follows:

- The card has exactly one signature.
- The protected JWS header contains exactly `alg`, `kid`, and `typ`:
  `alg` is `EdDSA`, `typ` is `JOSE`, and `kid` is
  `ed25519:<64 lowercase hex characters>` for the raw 32-byte Ed25519 public
  key.
- The key is the public key already pinned for this HTTPS origin by successful
  Kollab discovery. `kid` must match that key exactly. The Card cannot supply a
  new trusted key.
- JWS unprotected headers and protected key-selection headers such as `jku`
  are rejected. No network key fetch occurs during Card verification. This
  prevents a Card from redirecting trust to another key source, including a
  same-origin but unpinned key.
- The signing input is
  `BASE64URL(protected-header-UTF8) + "." + BASE64URL(JCS(card-without-signatures))`.
  Both base64url fields are unpadded and canonical. The protected header is
  serialized as JCS by this profile.
- The caller must provide the A2A ProtoJSON field-presence representation that
  it will serve. Required fields and explicitly present optional fields remain
  represented; fields omitted by the A2A SDK's presence-aware conversion stay
  omitted. The helper removes only the top-level `signatures` field before
  JCS canonicalization; it does not guess which schema defaults a protobuf
  conversion intended to omit.

The A2A specification makes Card signatures optional and permits multiple
signatures and `jku`; Kollab requires this narrower profile. See [A2A Agent
Card signing](https://a2a-protocol.org/v1.0.0/specification/#agent-card-signing)
and [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785).

## Rotation and trust state

Profile v1 defines no automatic key transition. A different descriptor key,
Card `kid`, or signature fails verification against the existing origin pin.
Rotation requires explicit human- or operator-authorized repinning after
independent verification. A higher revision or a self-signed replacement is
not sufficient. The discovery store remains the owner of durable origin pins
and its key-change rejection behavior; this helper neither writes pins nor
admits an A2A peer to the Hub.

The descriptor's HTTPS-origin proof and first-use pin are separate from A2A
Card signature verification. A valid Card signature proves that the pinned
key signed the supplied Card; it does not prove family/company membership,
owner identity, current reachability, communication authorization, or tool
permission.

## Python API

`plugins.hub.dns.a2a_signing` provides:

- `resolve_agent_card_url(origin, advertised_url)` — returns `None` for an
  identity-only descriptor or the validated canonical Card URL.
- `decode_agent_card(raw)` — bounded UTF-8 JSON object decoding with duplicate
  keys and non-finite numbers rejected.
- `sign_agent_card(card, private_key, *, kid=None)` — copies an unsigned Card
  and appends the profile signature. The key may be a PyNaCl `SigningKey`, a
  32-byte seed, a 64-byte libsodium secret key, or its hex encoding.
- `verify_agent_card(card, *, origin, pinned_public_key)` — verifies the
  signature and returns origin, deterministic `kid`, public-key fingerprint,
  and canonical payload digest.

The module uses the repo's existing `rfc8785` and PyNaCl dependencies. It does
not import or install the A2A SDK. Callers should construct a presence-aware
A2A v1 Card using the SDK, convert it once to the wire dictionary, sign that
dictionary, then serve the same signed dictionary without another
protobuf/dictionary conversion.

## Evidence boundary

The implementation and unit tests cover this signing profile only. The
`endpoints.agent_card` descriptor field, HTTPS Card fetch, A2A server routes,
durable pin integration, human repinning UI, enrollment, and agent
authorization remain outside this module. `tests/interop/a2a_signing/run_interop.py`
checks bidirectional Python/PyNaCl and Node.js built-in Ed25519 signatures
over Unicode, numeric, and key-order vectors. `official_sdk_check.py` also
checks both directions against the official Python SDK 1.1.5 signer and
verifier after protobuf JSON conversion. The official JavaScript SDK was not
exercised, so Node runtime success is not a claim of JavaScript SDK
conformance.

## Primary references

- [A2A v1.0 Agent Card and signature specification](https://a2a-protocol.org/v1.0.0/specification/)
- [A2A Python SDK](https://github.com/a2aproject/a2a-python)
- [RFC 8037 EdDSA for JOSE](https://www.rfc-editor.org/rfc/rfc8037)
- [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785)
