# Private device pairing and conversation grants

Status: implemented protocol primitives and local CLI; HTTP transport and automatic cross-device synchronization are adapter responsibilities.

This contract provides one account-free path for an owner to enroll a new device, keep a private member roster, grant one device a narrow conversation permission, and revoke it later. Discovery only locates a peer. It does not enroll a key, authorize a message, or authorize tool execution.

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
