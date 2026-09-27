# A2A workspace receiver

This opt-in receiver uses the official `a2a-sdk` 1.1.5 and the A2A 1.0 JSON-RPC
binding. It exposes one configured local workspace through two deterministic,
provider-free skills. It does not start an LLM, interpret open-ended instructions,
or offer general shell/MCP/tool dispatch.

- `workspace.read`: read an existing UTF-8 `.txt`, `.md` or `.json` file.
- `workspace.create`: create a new file of those types. Existing files are not
  overwritten.
- Files and create content are limited to 32 KiB. Paths are relative to the
  configured workspace; hidden paths, traversal and symlinks are rejected.

The server translates each skill into the existing Kollab `file_read` or
`file_create` tool. Execution passes through `ToolExecutor`, its bundle scope,
the event bus, `PermissionHook`, `PermissionManager` and `FileOperationsExecutor`.
The dedicated runtime uses `confirm_all` and an explicit operator skill policy.
It never changes the host's global approval settings. Permission checks and
existing blocked-tool decisions remain in force.

## Start the receiver

The project declares these dependencies in its optional `a2a` extra. In an existing Kollab development environment, install the server/signing dependencies with:

```sh
uv pip install --python .venv/bin/python 'a2a-sdk[http-server,signing]==1.1.5' 'cryptography>=43' 'uvicorn>=0.30,<1'
```

Provision the private directory with a pinned owner key, a human-approved paired
device membership credential, and a signed conversation grant using the
[pairing CLI walkthrough](../specs/agent-device-pairing.md#local-cli-walkthrough).
It also gives the exact state-file mapping and separate Card key setup.
The receiving directory must know the member
before a request can run. Keep signing seeds in local files readable only by the
operator. Public and private key files below contain hexadecimal Ed25519 keys;
only the public owner key is read by the receiver.

```sh
.venv/bin/python -m plugins.hub.a2a_adapter \
  --workspace /absolute/path/to/destination \
  --workspace-id lab-destination \
  --workspace-label "Lab Destination" \
  --directory-state /absolute/private/directory.json \
  --owner-public-key-file /absolute/private/owner.pub \
  --card-key-file /absolute/private/receiver.seed \
  --public-url https://agent.example.com \
  --port 8788 \
  --allow-skill workspace.read \
  --allow-skill workspace.create
```

The process binds only `127.0.0.1`. For remote access, place it behind an HTTPS
reverse proxy forwarding `/a2a`, `/kollab/` and `/.well-known/`. The public
origin is explicit configuration; requests cannot choose it or the workspace.
Plain HTTP public origins are rejected. Local integration tests send HTTP only
to an ephemeral loopback listener while retaining the configured HTTPS identity
in the signed proof. They do not constitute remote TLS deployment proof.

Only a running executable receiver should advertise the optional
`endpoints.agent_card` locator. An identity-only publisher must omit it.

The CLI also serves a thin signed locator at `/.well-known/agent-keys.json` and
its extensionless alias. Both point to the real Agent Card and use its stable
key. The persistent locator revision defaults to `<directory-state stem>.a2a-locator.json`
alongside `--directory-state`; use `--locator-state` to place it elsewhere. Locator GETs
refresh the short expiry while preserving the key and increasing the revision.

## Pair a device and list the private workspace

The new device first generates its own key and sends only its public key to the
owner through QR or another trusted channel. Create a short-lived pairing
challenge through the owner-local private-directory API/CLI, binding
`expected_device_public_key` to that exact key before starting the listener. Pass its ID with
`--pairing-challenge-id <challenge ID>`. The receiver keeps only the pinned public
owner key and signed challenge, never the owner's signing seed.

1. The new device fetches `GET /kollab/pairing/challenge` and verifies the signed
   challenge with the owner key it already trusts.
2. The device uses its locally held Ed25519 key and
   `prove_pairing(challenge, device_key, owner_public_key=...)` to prove possession.
   It submits `{"proof": "<proof JWS>"}` to `POST /kollab/pairing/proof`.
3. HTTP 202 means `pending_local_approval`. The public route only stores the
   bounded proof. It does not create membership or a grant. The owner reviews
   `pending_pairing_proofs()` locally, checks the device fingerprint, and invokes
   `approve_pairing(..., approved_by_human=True)` for that exact recorded proof.
4. The owner gives the device its signed membership credential and an explicit
   `directory.read` conversation grant. Use the same request-signing headers as
   A2A, but purpose `directory.read`, path `/kollab/directory`, and body `{}`.
5. `POST /kollab/directory` returns one authorized record containing only the
   configured opaque `workspace_id`, display `label` and canonical `agent_card`
   URL. It does not return member credentials, local paths or a public roster.

Pairing routes are absent unless a challenge ID is explicitly configured. A
challenge accepts only the previously pinned device's proof and expires. Approval
consumes it in the owner's local state; a separate receiver keeps its installed
challenge until expiry unless its own state is updated. Repeating the recorded
proof does not issue membership. There is no remotely callable approval route. Private
workspace listing requires membership, a fresh device proof and a separate
`directory.read` grant; this is deliberately a stronger policy than membership
alone. Multi-workspace catalogs are outside this first receiver.

The local operator CLI is `python -m plugins.hub.dns.private_directory`.
The device's `create-device-key` command creates its private key locally and
prints only the public key. The owner's `create-pairing --device-public-key-hex
<public key>` command binds that key. `register-challenge`, `pending`,
`export-proof`, `approve-pairing`, `import-member` and `issue-grant` handle the
signed artifact transfer and local review steps. Run each command with `--help`
for the explicit `--home`, workspace and artifact paths. Approval and grant
issuance require local human confirmation; no receiver route exposes them.

## Request contract

The public `GET /.well-known/agent-card.json` returns an A2A 1.0 Agent Card with a
standard `signatures` JWS entry. Pin its Ed25519 signing key through the separately
verified origin descriptor. Card retrieval or signature verification does not
grant membership or workspace authority.

Task RPCs are `POST /a2a`. Every RPC requires these headers:

- `Content-Type: application/json` and `A2A-Version: 1.0`
- `Authorization: Bearer <owner-signed device membership credential>`
- `X-Kollab-Grant: <owner-signed conversation grant>`
- `X-Kollab-Proof: <device-signed request proof>`
- `X-Kollab-Purpose: workspace.create` or `workspace.read`
- `X-Kollab-Conversation: <grant conversation ID>`
- `X-Kollab-Message-Id: <unique request ID>`

Use `private_directory.sign_request` to produce the proof over the exact serialized
body, HTTP method, path, exact configured HTTPS `target_uri`, workspace ID,
purpose, conversation ID and message ID. The receiver derives the target from
its configured origin, never the incoming `Host` header.
The receiver checks membership, expiry, scope, local revocations and replay before
dispatching to the SDK. Never log these credential/proof headers.
It revalidates the grant after any queued wait and local permission decision,
immediately before tool dispatch, so a locally received revocation can stop a
pending task.
All responses use `Cache-Control: no-store`. Request bodies have a ten-second
read deadline and a 64 KiB maximum; the pairing proof limit is 16 KiB and the
private-directory body limit is 1 KiB. Duplicate JSON keys, non-finite values,
ambiguous protobuf field aliases and unsupported message fields are rejected.

Example A2A 1.0 `SendMessage` body:

```json
{
  "jsonrpc": "2.0",
  "id": "request-1",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "message-1",
      "contextId": "conversation-1",
      "role": "ROLE_USER",
      "parts": [{
        "data": {
          "skill": "workspace.create",
          "path": "handoff.txt",
          "content": "Created through Kollab's permission-checked local tool.\n"
        },
        "mediaType": "application/json"
      }]
    }
  }
}
```

`messageId` and `contextId` must match the proof headers; the skill must match the
grant purpose. The only accepted create payload fields are `skill`, `path` and
`content`. A read payload has only `skill` and `path`. Extra fields such as `cwd`,
`command`, `tools` or a sender-selected workspace are rejected.

Successful execution produces an SDK Task with submitted → working → completed
status and a `workspace-tool-result` artifact containing skill, relative path,
success, tool type, output and error. A denied local tool finishes failed without
claiming execution succeeded. Grant/authentication failures are HTTP 401/403
before tool execution. Rejected paths also return 403 without tool execution.

Use `GetTask` with `params: {"id": "<task ID>"}` and a fresh request proof to read
the result. `ListTasks` uses the same authorization. The SDK task store scopes
tasks to owner, device, workspace, purpose and conversation; changing any scope
does not expose another task. This first receiver keeps at most 256 tasks in
memory and admits at most eight active tasks. It reserves capacity before SDK
dispatch; a full receiver returns HTTP 429 without running a tool. Terminal
tasks expire after 15 minutes; active tasks are not evicted. These limits are
configurable in the `A2AWorkspaceConfig` application factory. Restarting discards
task polling history. Created workspace files remain on disk. There is no durable
offline delivery guarantee.

`CancelTask` follows the SDK error lifecycle: these short atomic file operations
cannot be safely interrupted, so cancellation is rejected. Streaming, push
notifications, task continuation, task references and multiple tenants are not
supported or advertised.

## Verification and limits

Run the focused integration suite:

```sh
.venv/bin/python -m pytest tests/integration/test_hub_a2a_adapter.py -q
```

The suite starts a real Uvicorn loopback listener, sends signed HTTP JSON-RPC,
creates and reads a scratch file through the real tool pipeline, polls the Task,
and verifies the fetched Agent Card signature and thin locator aliases. It also
exercises HTTP pairing proof submission, local approval, private workspace
listing and revocation. Negative cases cover missing,
denied and revoked authorization, replay, wrong workspace/purpose, cross-device
task lookup, local permission denial, revocation during permission waiting, path
escapes, bounded bodies/files, task capacity/expiry, body deadlines, duplicate
JSON, and unsupported payloads.

The receiver assumes the local workspace is trusted. Another local process that
can replace directories during a filesystem operation is outside this profile;
the adapter's symlink/path checks are not an operating-system sandbox. A hardened
deployment should isolate the process account and filesystem. Receiver revocation
is immediate once present in its local directory; distributing revocations across
machines requires a separate authenticated sync mechanism.

This proves a deterministic A2A task/result/artifact exchange. General natural
language coding tasks, LLM provider integration, durable distributed task queues,
cross-machine deployment and NAT traversal need their own implementation and live
verification.

Primary references: [A2A 1.0 server tutorial](https://a2a-protocol.org/v1.0.0/tutorials/python/5-start-server/),
[official Python SDK](https://github.com/a2aproject/a2a-python),
[SDK package](https://pypi.org/project/a2a-sdk/1.1.5/).
