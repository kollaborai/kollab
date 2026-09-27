# Private device pairing and conversation grants

Status: implemented protocol primitives and local CLI. The code-entry,
delegated-agent approval and encrypted provisioning flow below is the required
product contract, added 2026-09-27 UTC; it is not implemented or released yet.

This contract provides one account-free path for an owner to enroll a new device, keep a private member roster, grant one device a narrow conversation permission, and revoke it later. Discovery only locates a peer. It does not enroll a key, authorize a message, or authorize tool execution.

## Code enrollment and delegated approval

The human experience must require no invitation-file transfer. The human tells
an already trusted Kollab agent, for example: "I'm connecting two new servers to
my family network. Give me the codes and accept those connections." The trusted
agent can approve those enrollments under that instruction. It cannot grant
itself standing authority to enroll arbitrary devices or export credentials.

`kollabor.ai` is the default discovery domain, not the identity of the private
network. A self-hosted domain uses the same flow. Each private network has its
own opaque identity and issuer policy; "family" and device names are labels.
If the human selects two networks, authorization names both network IDs and the
per-network scopes. It never silently joins all networks of the trusted device.

Required sequence:

1. The local human input path records a durable enrollment delegation before a
   model can use it: authorizing human action ID, authorized agent/session,
   issuer, network IDs, allowed configuration profile and credential categories,
   maximum new devices, expiration and revocation state. The example above
   grants two admissions, with a ten-minute default window. Vague or ambiguous
   requests require a concrete selection; model output is not authorization.
2. Under that delegation, the agent requests one single-use code per device.
   The runtime displays the codes through a private UI; model/tool history gets
   a display receipt and non-secret enrollment IDs, never the actual codes.
   One code may carry an explicitly authorized selection of networks for one
   device. Two servers use two codes; sharing a reusable group password is not
   this contract.
3. On the new server the human runs `/connect`. A private input screen shows the
   domain (default `kollabor.ai`) and asks for the code. It creates or reuses that
   server's local device key and verifies signed discovery. Codes are not slash
   command arguments, shell arguments, model input, command history, telemetry,
   or logs. Attach mode forwards a typed local-only RPC to the existing daemon;
   the viewer must not enroll a separate identity.
4. The code establishes a bounded pending enrollment, not membership. The new
   device proves possession of the code and its private key, binding both to the
   exact enrollment ID, intended issuer, requested networks, nonce and expiry.
   The issuer must authenticate through this pairing exchange; a directory or
   relay-supplied public key alone cannot replace the trusted issuer.
5. The relay delivers an encrypted enrollment notification to the trusted
   installation. Only a verified request matching an active human delegation
   may nudge its designated agent. Unknown visitors stay pending under the
   separate visitor policy and cannot cause unrestricted model wakeups.
6. The trusted agent sees bounded metadata: enrollment ID, device public-key
   fingerprint, requested profile/networks, remaining allowance and a clearly
   marked unverified device label. A message such as "this is David" is request
   data, not proof of identity or an instruction to expand access.
7. The agent proposes accept or reject through a narrow enrollment tool. The
   runtime rechecks the durable delegation, agent identity, proof, expiry,
   requested scope and revocation; atomically binds the code to the new key and
   consumes the admission allowance. Concurrent workers cannot approve a third
   device under a two-device grant. Models never receive the issuer private key.
8. After acceptance the issuer signs a device-bound membership credential and
   encrypts the authorized configuration bundle specifically for that device.
   The device verifies issuer, recipient, network audience, configuration digest,
   revision and expiry before installing it. No room capability, private roster,
   provider credential or configuration is disclosed while pending.
9. The new agent reports joined/provisioned only after private storage succeeds
   and the matching acknowledgment returns. Retry by the same device is
   idempotent; a different key cannot reuse the code or retrieve the bundle.
   Membership still does not grant permission for unsolicited conversations or
   remote workspace tools.

Code design must resist guessing and malicious bootstrap substitution. Use a
high-entropy generated code (at least 100 random bits, presented in copyable
groups), or a reviewed password-authenticated key exchange if short human PINs
are selected. Hashing a six-digit PIN and using it directly as an encryption key
is not acceptable. The service stores bounded, expiring encrypted enrollment
records, with atomic redemption across workers/hosts, connection/source limits
and admission quotas. Codes are never reusable membership credentials.

### Provisioned configuration and tokens

The bundle has an explicit, versioned allowlist: network identity and discovery
settings, selected model/profile preferences, approved agent/skill configuration
and the credentials authorized by the human's provisioning policy. Installing a
profile is distinct from executing its hooks, shell commands or downloaded
skills; enrollment must not run arbitrary configuration content.

- Device identity seeds and owner signing keys stay on their original machines.
- Prefer individually scoped, expiring and revocable device credentials. Neither
  the LLM nor relay needs plaintext credentials; a local credential broker reads,
  seals, imports and reports success without returning secret values to tools.
- Provider credentials require an explicit policy for that provider/profile.
  Network enrollment alone does not authorize copying an entire credential store,
  `.env`, SSH keys, cloud keys or account login caches.
- A provider's reusable API key or OAuth refresh token is not a new per-device
  credential merely because it is encrypted. If explicitly shared, the receiving
  machine obtains its actual provider privileges. Removing network membership
  does not revoke a copied provider token; provider-side rotation/revocation is
  required. Claim per-device revocation only when the provider or an authorized
  credential service actually enforces it. Do not invent provider delegation.
- Private storage is user-owned with restrictive permissions or supported secure
  storage. Workspace configuration contains references, not plaintext secrets.
  Imported credentials must not overwrite unrelated existing credentials.

### Failure and acceptance requirements

Retain the current network and local configuration when a code, discovery,
approval or provisioning step fails. A pending request expires if the approving
agent is unavailable; it never becomes approved by elapsed time or reconnect.
Bound notification retries and suppress duplicate model turns. Revocation or
expiry during an approval wait blocks credential issuance and delivery. Keep a
secret-free audit of the human action, decision, device key, granted scopes,
bundle digest, installation acknowledgment and rejection reason.

Acceptance must cover the actual TUI and attach/daemon path, both network hosts,
real trusted-agent notification and tool decision, atomic two-device allowance,
wrong/reused/expired codes, code theft without destination-key possession after
binding, malicious identity labels, relay key substitution, issuer offline/restart,
concurrent approval/replay, revocation during provisioning, partial installation,
and absence of codes/tokens from model requests, tool responses, histories, logs,
events and shared project files. Provider access must be exercised before calling
a provisioned profile usable. These are required gates, not completed checks.

## Keys and authority

- The owner has one stable Ed25519 signing key. Each device generates and retains its own distinct Ed25519 signing key. `owner.key` and `device.key` are separate 32-byte seeds stored locally with mode `0600` beneath a mode `0700` private home. The module never copies, exports, or includes either private key in a token.
- A public principal ID is `ed25519:` followed by the lowercase hex encoding of the raw 32-byte Ed25519 public key. The JOSE protected header uses `alg: EdDSA`, `typ: JWT`, and `kid` set to that public principal ID.
- Owner-signed device credentials use standard JWT claims (`iss`, `sub`, `aud`, `iat`, `nbf`, `exp`, `jti`) and a `scope` list. `iss` identifies the owner; `sub` identifies the device public key; `aud` is the owner's private-directory audience. The owner key remains the authority for enrollment and grants. A device proves possession by signing each request with its own key.
- A device credential is not a conversation grant. An owner-signed grant binds the credential ID and device ID to one opaque recipient workspace ID, one purpose, one conversation ID, and a short expiry. A receiver also checks that the credential remains in its local private roster and has no matching local revocation.
- The boolean `approved_by_human=True` in the Python API is a caller assertion, not proof of human action. Only local operator code should call approval/signing methods. The CLI requires an interactive terminal confirmation for pair approval, grant issuance, and revocation; those commands are never exposed as remote routes.

Credentials and grants are signed, not encrypted. Treat token files as sensitive. A copied device credential or grant is insufficient to forge the request proof without the device private key, but token disclosure can reveal public key IDs, scope, workspace IDs, and conversation IDs.

## Pairing protocol

1. The new device creates a fresh device key and provides only its public key (or fingerprint) to the owner over a trusted channel.
2. The owner creates a short-lived signed challenge with `expected_device_id` fixed to that public key. This pin is mandatory: a proof from any other key is rejected before it can occupy the receiver's one-proof inbox.
3. The intended device signs a proof binding its key to the challenge ID, challenge digest, owner, nonce, and expiry. It sends that proof to the receiver.
4. The receiver validates the pinned challenge and proof, then records the proof as pending. This does not add a member or grant any access. The receiver can expose only a locally pre-registered challenge ID; it cannot create or approve one on behalf of the owner.
5. The owner reviews the expected device ID and proof locally, then approves it. The owner key signs an expiring device credential. The credential is transferred to the receiver through a trusted private channel, which imports it into its private roster.

The receiver's public challenge route must serve a challenge installed with `register_pairing_challenge`. Its proof-submission route may call `record_pairing_proof`. Neither route may call `begin_pairing`, `approve_pairing`, or `issue_conversation_grant`.

## Request authorization

Before conversation handling, the receiver verifies all of the following:

- the owner signature and expiry on the device credential and conversation grant;
- the sender is a nonexpired, nonrevoked member in this receiver's local roster;
- the grant is bound to that device credential, the receiver's configured opaque workspace ID, the requested purpose, and the exact conversation;
- the enrolled device signed a fresh request proof bound to the exact raw body SHA-256, HTTP method, request path, configured canonical HTTPS target URI, workspace audience, purpose, conversation ID, and message ID;
- the proof `jti` and the `(device, conversation, message ID)` replay key have not been consumed and fit within the replay ledger bounds.

The caller must supply `target_uri` from trusted endpoint configuration, never from the inbound `Host` header. `target_uri` is an exact canonical HTTPS URI whose path and query match `path`; it prevents a valid proof for one origin from being redirected to another origin that reuses a workspace ID. Workspace IDs are opaque identifiers, not filesystem paths. The receiver maps them to its local workspace and continues to enforce its normal local tool permissions.

The request proof is a custom compact EdDSA JWS profile. It reuses JOSE/JWT signing and claims framing, but it is **not RFC 9421 HTTP Message Signatures**, does not implement RFC 9530 `Content-Digest`, and is not DPoP. The body hash is a custom JWS claim. Do not advertise standards compliance or generic HTTP-signature interoperability for this profile.

`authorize_request(...)` returns an `AuthorizedPrincipal` suitable for the adapter's trusted task owner. A JSON-RPC request ID is not identity. Use the persisted conversation/context ID for `conversation_id`, and a unique inbound turn ID for `message_id`. Consume the proof before queueing; call `revalidate(principal)` again immediately before executing queued conversation or tool work so a locally applied revocation or expiry blocks work that waited in a queue. Downstream tools still use the receiving agent's ordinary permissions.

The implementation signs the proof over the raw HTTP request body. If an SDK transforms or reserializes the JSON body, the adapter must sign the exact bytes it sends on the wire. Each poll or message needs a fresh proof `jti` and nonce; reuse of the proof or message ID is rejected.

## Bounded persistent state

The private directory is local JSON state written atomically with mode `0600`, protected by its own `<state-file>.lock` advisory lock, and limited to 4 MiB before read and write. It bounds members (2,048), pending pairings (256), issued and accepted grants (256 each), active request proofs (4,096), active message replay keys (4,096), and permanent revocations (4,096). A full ledger fails closed; live replay markers are never evicted to make room.

Expired pairing entries, credentials, grants, and replay markers are pruned during a successful mutation before capacity checks. Revocations are never pruned because doing so could restore a revoked credential. If the revocation limit or overall state-file limit is reached, new state is rejected until an operator handles the condition; existing revocations remain intact.

A failed mutation is not written. In particular, capacity rejection does not consume a request proof/message ID, evict a still-valid replay marker, or partially consume a pairing challenge. Cleanup and an accepted change are persisted together by the next successful atomic state write.

The owner can sign a revocation for a device, credential, or grant and transfer it to receivers for local application. Revocation is effective at a receiver only after that receiver has received and persisted the signed record. Offline receivers can therefore retain stale authorization until synchronization. This slice does not implement revocation gossip, directory replication, or online status checks.

## Local CLI walkthrough

The module is runnable without a Kollab account, paid service, HTTP listener, or LLM request:

```sh
# On the owner machine
python -m plugins.hub.dns.private_directory init-owner --home ~/.kollab/private-net

# On the new device: retain this device.key locally; send only the public key to the owner.
python -m plugins.hub.dns.private_directory create-device-key --home ~/.kollab/device-net

# On the owner: replace HEX with device_public_key_hex printed above.
python -m plugins.hub.dns.private_directory create-pairing \
  --home ~/.kollab/private-net --device-public-key-hex HEX --out /tmp/pairing.jwt

# On the receiver: install the signed challenge with the pinned owner public key.
python -m plugins.hub.dns.private_directory register-challenge \
  --home ~/.kollab/receiver-net --owner-public-key-hex OWNER_PUBLIC_HEX \
  --challenge-file /tmp/pairing.jwt

# On the new device: create a proof with its own device.key.
python -m plugins.hub.dns.private_directory prove-pairing \
  --home ~/.kollab/device-net --owner-public-key-hex OWNER_PUBLIC_HEX \
  --challenge-file /tmp/pairing.jwt --proof-out /tmp/device-proof.jwt

# Deliver the proof to the receiver's proof route, or transfer it privately and record it locally.
python -m plugins.hub.dns.private_directory record-proof \
  --home ~/.kollab/receiver-net --owner-public-key-hex OWNER_PUBLIC_HEX \
  --challenge-file /tmp/pairing.jwt --proof-file /tmp/device-proof.jwt

# Bring the proof back to the owner over a trusted private channel, then locally record and approve it.
python -m plugins.hub.dns.private_directory record-proof \
  --home ~/.kollab/private-net --challenge-file /tmp/pairing.jwt \
  --proof-file /tmp/device-proof.jwt
python -m plugins.hub.dns.private_directory pending --home ~/.kollab/private-net
python -m plugins.hub.dns.private_directory approve-pairing \
  --home ~/.kollab/private-net --challenge-id CHALLENGE_ID \
  --credential-out /tmp/device-credential.jwt

# Transfer the signed credential privately to the receiver and import it.
python -m plugins.hub.dns.private_directory import-member \
  --home ~/.kollab/receiver-net --owner-public-key-hex OWNER_PUBLIC_HEX \
  --workspace-id WORKSPACE_ID --credential-file /tmp/device-credential.jwt

# The owner issues one narrowly scoped grant after an interactive local confirmation.
python -m plugins.hub.dns.private_directory issue-grant \
  --home ~/.kollab/private-net --credential-file /tmp/device-credential.jwt \
  --workspace-id WORKSPACE_ID --purpose workspace.read \
  --conversation-id CONVERSATION_ID --out /tmp/conversation-grant.jwt
```

`init-owner` creates `owner.key` and `private-directory.json` beneath the
owner's private `--home`; it prints `owner_public_key_hex`, which is the only
owner key material copied to a receiver. Save those 64 hex characters as
`owner.pub` on the receiving host with restrictive file permissions (this is a
public verification key, not the owner's seed). For example, provision a
private receiver directory with mode `0700`, write the public hex line to
`owner.pub` with mode `0600`, and create the Agent Card signing seed separately:

```sh
install -d -m 700 /absolute/private/receiver-net
printf '%s\n' OWNER_PUBLIC_HEX > /absolute/private/receiver-net/owner.pub
chmod 600 /absolute/private/receiver-net/owner.pub
.venv/bin/python - <<'PY'
import os
from nacl.signing import SigningKey

path = "/absolute/private/receiver-net/receiver.seed"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write(bytes(SigningKey.generate()).hex() + "\n")
    stream.flush()
    os.fsync(stream.fileno())
PY
```

Create this Card key once. Exclusive creation refuses to overwrite an existing
identity; retain the same seed and locator revision state across restarts.

The receiver-side `--home ~/.kollab/receiver-net` above maps to
`~/.kollab/receiver-net/private-directory.json`; pass that exact file to
`a2a_adapter --directory-state`. The adapter reads `owner.pub` through
`--owner-public-key-file` and the independent Card signing seed through
`--card-key-file`. Its optional pairing route uses the challenge ID printed
by `register-challenge` via `--pairing-challenge-id`. The full receiver command
and HTTPS proxy boundary are in the [A2A workspace receiver guide](../operations/agent-a2a-workspace.md#start-the-receiver).

The device credential, grant, and signed revocation must be delivered to the relevant device/receiver by a trusted private transfer or a separately implemented secure channel. The CLI does not claim those files are encrypted in transit. `list-members` prints only active device and credential IDs plus expiry; it does not publish a workspace path.

To revoke, the owner runs `revoke --target-type device --target-id ed25519:... --out device-revocation.jwt` and confirms locally; the receiver runs `apply-revocation` with the signed artifact. For a credential or grant, use its corresponding ID. Remote callers cannot approve pairing, issue grants, or sign revocations.

## API and integration boundary

Implementation: `plugins/hub/dns/private_directory.py`. Tests: `tests/unit/test_private_directory.py`.

The adapter-facing APIs are:

```python
directory.register_pairing_challenge(challenge_token)
directory.record_pairing_proof(challenge_token, proof_token)
directory.authorize_request(
    credential_jws,
    grant_jws,
    proof_jws,
    body=raw_body,
    method="POST",
    path="/a2a",
    target_uri="https://configured.example/a2a",
    recipient_workspace_id=workspace_id,
    purpose=purpose,
    conversation_id=context_id,
    message_id=turn_id,
)
directory.revalidate(principal)
```

`verify_pairing_proof(...)` is a pure validator for the public proof-submission route; it has no approval side effect. The module does not implement authenticated transport, encrypted peer sessions, forwarding, offline queues, NAT traversal, DHT or other decentralized discovery, private roster HTTP endpoints, or automatic membership/revocation propagation. Those remain separate contracts and must not be inferred from successful local pairing tests.
