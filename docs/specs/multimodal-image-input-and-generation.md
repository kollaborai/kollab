---
title: Multimodal Image Input and Generated-Image Output
created: 2026-08-24
modified: 2026-08-24
status: draft
author: maintainers
---

# Multimodal Image Input and Generated-Image Output

provider-neutral image attachments for conversational input, followed by a
separately capability-gated path for generated-image outputs. The first phase
makes it possible to ask a capable chat model about a local image or a trusted
HTTPS image URL. It does not promise that every provider, model, or OAuth
transport supports vision. Image generation is a distinct feature with distinct
wire contracts, artifact handling, and availability risks.


## problem

Kollab currently treats a conversation message as text. The terminal input path
accepts typed/pasted text, `ConversationManager.add_message()` persists
`content: str`, and the OpenAI Responses request builder forwards that value as
one `content` field. That works for text and native tool turns but cannot
represent a text-and-image user turn safely or durably.

The `openai_responses` provider can call the public `/responses` endpoint and
is also used for the ChatGPT OAuth profile (`openai-oauth`) against the Codex
backend. These are not interchangeable contracts. The current provider already
omits fields such as `store`, output-token controls, server continuation IDs,
and public cache controls for OAuth/Codex because that transport rejects them.
No evidence currently establishes whether it accepts public Responses
`input_image` content, image-generation tools, image bytes, image URLs, or
streamed generated-image blocks.

There is also no normalized output type for an image. The Responses transformer
keeps text, function calls, and reasoning; it drops unrecognized output items
and non-text message blocks. Therefore even a backend that emitted an image
would lose it before the terminal or Web UI could render it.


## goals

1. Define one provider-neutral, JSON-safe representation for text plus image
   input and for generated-image artifacts.
2. Preserve existing text-only messages, session history, tools, and provider
   behavior without migration or wire-format regression.
3. Ship local-image and HTTPS-URL input through an explicit terminal command,
   with clear validation, consent, size limits, and display feedback.
4. Translate normalized image input only at providers/models whose explicit
   capabilities allow it.
5. Treat image generation separately from image understanding. It must remain
   unavailable unless its provider/model/transport contract is verified.
6. Preserve generated images as managed artifacts with textual fallbacks rather
   than inserting raw binary into history, logs, prompts, or terminal streams.
7. Make failures actionable: say whether a source is invalid, the selected
   model lacks vision, the transport is unverified, or a backend rejected a
   feature.
8. Keep a future Web UI attachment composer possible without making it a phase
   1 prerequisite.


## non-goals

- universal vision support across providers or models;
- silently converting arbitrary pasted bytes, local paths in normal prose, or
  clipboard image data into uploads;
- OCR, image editing, image annotation, albums, camera capture, or drag/drop
  in the first release;
- a new generic blob store or a permanent remote image host;
- storing image bytes/base64 values in conversation history, JSONL logs,
  hub messages, rendered prompts, or terminal output;
- assuming ChatGPT OAuth/Codex matches the public OpenAI Responses API;
- exposing a public Images API call through OAuth without an explicitly
  verified, permitted OAuth contract;
- Web UI attachment composition in phase 1.


## terminology

- **image input**: an image supplied with a user message so a model can inspect
  it. This is also called vision or multimodal input.
- **source descriptor**: the persisted JSON-safe reference to an image. It is
  metadata, not the image bytes.
- **local source**: a regular image file selected under the allowed workspace
  policy and encoded only at the request boundary.
- **URL source**: a normalized `https://` URL sent as a remote reference when a
  provider supports URLs and policy permits the hostname.
- **provider file ID**: an opaque file/image identifier previously uploaded by
  a provider-specific implementation. It is deferred for phase 1.
- **generated-image artifact**: a managed local file containing a successful
  generated image, with durable metadata and a human-readable textual
  representation.
- **vision capability**: provider/model/transport declares it can accept a
  particular normalized image source type.
- **generation capability**: provider/model/transport declares both a supported
  request mechanism and a supported output retrieval mechanism. This is not
  implied by vision capability.
- **transport**: a concrete HTTP contract, not merely a provider label. For
  OpenAI Responses, public API and ChatGPT OAuth/Codex are separate transports.


## current implementation and exact gaps

### input and persistence

- `kollabor/llm/message_handler.py`,
  `MessageHandler.handle_user_input()`, reads only `data["message"]` as a
  string and passes it to `process_user_input()`.
- `packages/kollabor-tui/src/kollabor_tui/input/paste_processor.py` handles
  pasted text; it has no attachment event or binary clipboard path.
- `packages/kollabor-tui/src/kollabor_tui/widgets/file_browser.py` is a
  reusable browser widget, but it is not connected to composition or message
  submission.
- `packages/kollabor-ai/src/kollabor_ai/conversation_manager.py`,
  `ConversationManager.add_message()`, accepts and stores `content: str`.
  `get_context_messages()` returns those message dictionaries directly.
- `packages/kollabor-ai/src/kollabor_ai/system_prompt_builder.py` supports
  configured `attachment_files`, but reads them as UTF-8 text and injects their
  contents into the system prompt. It is not a binary attachment mechanism.

### OpenAI Responses translation

- `packages/kollabor-ai/src/kollabor_ai/providers/openai_responses_provider.py`,
  `OpenAIResponsesProvider._prepare_request()`, builds user/assistant input as
  `{ "role": role, "content": msg.get("content", "") }`. It does not
  validate image types, encode local data, create `input_image` parts, or make
  a capability decision.
- Its existing OAuth guard uses `_requires_streaming` to omit public API fields
  rejected by Codex. This is the required pattern for any image capability:
  serialize only after the selected transport has an affirmative capability.
- `packages/kollabor-ai/src/kollabor_ai/oauth/openai_oauth.py` authenticates the
  ChatGPT device flow; it does not establish media upload, generation, or
  artifact-download support.

### output and rendering

- `packages/kollabor-ai/src/kollabor_ai/providers/models.py` has text, tool,
  tool-result, and thinking content blocks only; no image input/output block or
  image streaming delta exists.
- `packages/kollabor-ai/src/kollabor_ai/providers/openai_responses_transformer.py`,
  `_transform_message_item()`, concatenates only `text`/`output_text` blocks.
  `transform_response()` logs unknown top-level output types and drops them.
  `transform_streaming_chunk()` is text/function/reasoning-centric.
- `OpenAIResponsesProvider._call_via_stream()` accumulates `TextDelta` values;
  a non-text final artifact would not survive its current aggregation.
- No terminal or Web UI renderer accepts a generated-image artifact today.

### tests and docs

`tests/unit/test_openai_responses_provider.py` and
`tests/unit/test_openai_responses_transformer.py` cover text, tool calls,
reasoning, streaming, and OAuth-specific request restrictions. They have no
image URL, data URI, file ID, binary, generation, artifact, or image-streaming
coverage. `docs/providers.md`, `packages/kollabor-ai/README.md`, and the
getting-started documentation do not offer image input/generation guidance.


## product UX

### terminal phase 1: explicit image command

Use a slash command so ordinary text remains ordinary text:

```
/image <path> [prompt]
/image https://example.com/diagram.png [prompt]
```

Examples:

```
/image screenshots/error.png explain the exception and likely fix
/image https://example.com/chart.png summarize the trend
```

`/image` resolves the argument as follows:

1. `https://` is a URL source. HTTP, `file:`, `data:`, localhost/private
   network URLs, credentials in URLs, and unsupported URL forms are rejected.
2. Any other value is a local path. Expand `~` only when the permission policy
   allows it; resolve symlinks before scope checking. It must resolve to a
   readable regular file inside an allowed scope.
3. The optional prompt becomes a text part in the same user turn. When absent,
   use a stable, non-deceptive default such as `Please analyze this image.`
4. The terminal echoes a compact user-turn summary, for example
   `[image: error.png, 1280x720, 284 KiB] explain the exception...`; it never
   renders base64 or binary.
5. The input is accepted only if the active loadout reports vision capability
   for the selected source type. An unsupported provider fails before network
   I/O and suggests a vision-capable profile/model.

The command is intentionally singular in phase 1. Multiple images, attachments
on arbitrary normal messages, clipboard images, and file-browser composition
are deferred until a simple single-image contract is reliable.

### URL handling

URLs are references, not local downloads, when the selected provider explicitly
supports `image_url`. Kollab must not fetch a user-provided URL merely to
validate it; this avoids SSRF and avoids altering the remote resource privacy
model. Basic syntactic parsing, HTTPS-only enforcement, credential rejection,
and an optional host allow/deny policy happen locally.

If a provider supports only encoded image bytes, the initial URL feature is
unavailable for that provider rather than fetching the URL server-side. A later
explicitly designed fetch/proxy feature requires separate SSRF, redirect,
DNS-rebinding, content-type, content-size, and provenance controls.

### Web UI

Web UI composition is optional/deferred. Phase 1 must expose artifacts and
message summaries in any existing history/inspection surface without breaking
it, but need not provide upload, drag/drop, clipboard, or image preview.

A later Web UI phase may add a clearly labelled attachment button to the
composer and reuse the same normalized source-descriptor and validation service.
It must not invent a browser-only request shape. Browser upload needs a daemon
upload endpoint with explicit size/type limits, CSRF/auth review, temporary
storage lifecycle, and a permission/consent affordance.

### generated-image UX (future, gated)

Generation must be invoked through an explicit command or an explicitly exposed
provider tool, never inferred from conversational wording. The exact surface is
deferred until a supported provider contract exists. A successful result should
show a compact message such as:

```
Generated image: sunset.png (1024x1024, PNG)
Artifact: /managed/session/path/sunset.png
```

The artifact path/identifier is selectable/copyable and any UI capable of image
preview may render it. If preview is unavailable, the textual artifact summary
remains usable. Failure must state that generation is unavailable, rejected by
the provider, or failed while safely persisting the returned artifact.


## architecture and contracts

### canonical message shape

Do not replace legacy text messages. Introduce a discriminated content value
that can be either a legacy string or a structured list of content parts:

```python
MessageContent = str | list[MessagePart]

TextPart = {
    "type": "text",
    "text": str,
}
ImagePart = {
    "type": "image",
    "source": ImageSourceDescriptor,
    "detail": "auto" | "low" | "high" | None,
}

ImageSourceDescriptor = {
    "kind": "local_path" | "url" | "provider_file",
    # local_path: canonical display path plus safe request-time locator
    # url: normalized HTTPS URL
    # provider_file: provider name + opaque file id
}
```

The exact Python implementation may use dataclasses/Pydantic/TypedDicts, but
must preserve these invariants:

- every persisted message is JSON serializable;
- legacy `str` content stays byte-for-byte compatible with existing histories;
- an image part never contains base64, raw bytes, access tokens, or a data URI;
- only a request-boundary adapter may read/encode a local file;
- a provider-specific file ID is scoped to its named provider/transport and may
  never be replayed through another provider;
- display text and source metadata are distinct so logs/history can safely show
  a filename/URL redacted as needed without exposing bytes.

For phase 1, generated assistant output uses an explicit content block and
metadata rather than making assistant `content` a raw image:

```python
GeneratedImageContent = {
    "type": "generated_image",
    "artifact_id": str,
    "media_type": "image/png" | "image/jpeg" | "image/webp",
    "path": str,                 # managed local artifact, never remote secret URL
    "width": int | None,
    "height": int | None,
    "revised_prompt": str | None,
    "provider_reference": str | None,
}
```

`UnifiedResponse.content` and streaming unions must gain a matching final image
content/event representation. An image is an atomic final artifact: streaming
may report progress, but never emit base64 or partial binary tokens through
`TextDelta`.

### attachment preparation boundary

Create one reusable attachment service at the application/agent boundary, not
inside terminal widgets and not inside every provider. Its responsibilities:

1. parse `/image` arguments and construct normalized text/image parts;
2. resolve and permission-check local paths;
3. identify media type from trusted file signature plus a conservative extension
   allowlist; do not rely only on client-declared MIME;
4. enforce configurable byte, pixel, frame-count/animation, and image-count
   limits before a provider request;
5. produce safe display/history metadata (basename, media type, bytes,
   dimensions where safely obtainable, source kind, optional digest);
6. defer local file reads/base64 encoding until the selected provider adapter
   has approved the source and is preparing the outbound request;
7. return typed, actionable errors rather than silently converting an image to
   prompt text.

The service is also the intended seam for a future Web UI upload flow. It must
not depend on a terminal-specific event type.

### conversation and context handling

`ConversationManager` must accept `MessageContent` while maintaining old
sessions. Context windows, compaction, logging, session save/restore, and
history endpoints must understand structured content. Token budgeting must use
an image-aware estimate/capability signal; it must not stringify an image source
into an accidental prompt or silently drop the part during trimming.

A context reducer that cannot retain an image part must make the loss visible:
replace it with a bounded explanatory text/reference, preserve descriptor
metadata where privacy policy permits, and never claim the model still sees the
image. Initial phase behavior may conservatively retain the complete turn or
reject submissions that cannot fit the selected model's input budget.

### provider capability registry

Capability is evaluated at `provider + transport + model`, not only provider.
Use a typed capability record owned by the provider/model registry, for example:

```python
MultimodalCapabilities(
    image_input_url: bool,
    image_input_data: bool,
    image_input_file_id: bool,
    image_input_max_bytes: int | None,
    image_generation: bool,
    image_generation_transport: Literal[
        "none", "responses_tool", "images_api", "provider_native"
    ],
    generated_image_delivery: Literal[
        "none", "url", "base64_final", "file_id", "artifact_download"
    ],
    generated_image_streaming: bool,
)
```

Unknown is represented as false/`none`, never as optimistic support. Registry
entries should be static, versioned configuration until a provider offers a
safe discovery API; dynamic capability probing must not run on normal message
submission.

The active profile/loadout surface should expose a concise read-only capability
summary so `/image` can explain why it is unavailable. Capability data is
configuration/metadata, not an OAuth credential.

### OpenAI public Responses mapping

Only when a public OpenAI Responses model has a verified capability:

- translate a `TextPart` to the standard text input part;
- translate a local image at request time into the documented image data form;
- translate a URL source to the documented remote image form;
- translate a provider file ID only when its provider/transport scope matches;
- retain the existing legacy string request serialization unchanged when no
  structured parts are present.

The mapper belongs in `OpenAIResponsesProvider._prepare_request()` or a small
provider-local serializer it calls. The canonical models and attachment service
must not embed OpenAI wire names such as `input_image`.

### OpenAI OAuth/Codex safety boundary

For `openai-oauth`, the capability baseline is:

```text
image_input_url: false (unverified)
image_input_data: false (unverified)
image_input_file_id: false (unverified)
image_generation: false (unverified)
generated_image_delivery: none (unverified)
generated_image_streaming: false (unverified)
```

This is deliberately conservative. The use of the `openai_responses` provider
does not prove that the ChatGPT/Codex backend implements the public Responses
multimodal contract. Existing code proves the opposite principle: it requires a
special OAuth wire path and has observed rejection of public fields.

Before enabling any OAuth image capability, a maintainer must run a credentialed,
non-production contract probe against the exact active backend/model, capture
redacted request/response evidence, and add a regression fixture. The probe
must separately test:

1. text + HTTPS image URL;
2. text + supported encoded/local image form, if documented;
3. accepted/declined `image_generation` tool/request form;
4. final generated-image delivery shape (URL, file ID, base64, etc.);
5. SSE sequence for generation, including any output-item/delta/done events;
6. error responses for unsupported models and oversized sources.

An OAuth enablement must be narrowly scoped to verified model family and
transport version. A 4xx response disables the capability for the request and
produces an explicit error; no fallback to an undocumented Images API or a
public API endpoint is permitted.

### generated-image artifact boundary

Generation has two independently verified stages: request acceptance and output
retrieval. Once a provider transformer receives a complete generated-image
reference/value, a shared artifact service must:

1. validate the delivery form against the capability record;
2. retrieve/decode exactly once, with strict response-size, MIME/signature,
   pixel, redirect, and timeout limits where network retrieval is necessary;
3. write a managed session-scoped artifact with private directory/file modes and
   collision-resistant names;
4. record digest, byte count, media type, dimensions, provider/model, creation
   time, source delivery kind, and safe provider reference in metadata;
5. expose `GeneratedImageContent` to renderers only after artifact persistence
   succeeds;
6. preserve a bounded textual failure/result record if persistence fails.

Base64 delivery belongs only inside this artifact boundary. It is decoded and
written as a file; it must not become conversation text, a stream delta, or a
logger payload. A remote generated URL is treated as a short-lived provider
reference and fetched under the same controlled boundary; it is not exposed as
a durable public artifact URL unless policy explicitly allows it.


## validation, privacy, and persistence

### input validation

Initial defaults should be conservative and configurable, for example:

- formats: PNG, JPEG, WEBP; GIF/animated formats disabled unless frame policy is
  deliberately implemented;
- one image per `/image` turn;
- byte limit and decoded-pixel limit set below all enabled provider limits;
- reject zero-byte, malformed, non-regular, inaccessible, or unsupported files;
- image metadata parsing must be bounded and protected against decompression
  bombs; avoid decoding full pixel data before the size/pixel guard;
- resolve symlinks before workspace/scope checks and re-check at read time to
  reduce time-of-check/time-of-use exposure;
- reject remote URLs with userinfo, non-HTTPS scheme, oversized textual form,
  or policy-denied hosts.

Provider limits may be lower than global limits. The effective allowed size is
the minimum of policy, attachment-service, and selected-capability limits.

### privacy and consent

An image may contain credentials, source code, faces, location data, or other
sensitive data. Before its first external transmission in a session, show a
clear terminal confirmation unless the user has configured an explicit trusted
policy. The confirmation identifies the selected provider/profile and whether
the source is local or remote. It does not print the entire path when that path
may be sensitive; basename is enough by default.

Local images are read only when the user invokes `/image`; no background folder
scan, thumbnail generation, or clipboard surveillance is allowed. For URLs,
Kollab should tell the user that the provider may fetch the remote URL directly.

EXIF and other metadata should not be sent unless the provider requires original
bytes and the user chose that behavior. The default local preparation should
strip metadata by re-encoding only if doing so remains within acceptable image
fidelity/cost boundaries; otherwise, prominently disclose that original metadata
may be transmitted and require an opt-in. This tradeoff requires an explicit
implementation decision, not an accidental library default.

### persistence and retention

Conversation history stores the structured part descriptor and safe metadata,
not image bytes/data URIs. For a local path, retention policy must decide whether
the reference is a path-only pointer, a content digest, or both; paths can leak
local structure, so exported/saved conversations should support redaction.

A restored conversation may display that an image was previously attached but
must not automatically re-read/re-send the local file. On a resend/retry, it
must revalidate path scope, existence, size, and consent. If unavailable, the
turn remains readable but reports that the original image cannot be resent.

Generated image artifacts use a session-scoped managed directory and follow the
same private-permission discipline as tool-output artifacts. Define retention,
manual export, cleanup, and deleted-session behavior before enabling generation.
Conversation logs record artifact metadata and digest, never the binary/base64.


## errors and user feedback

All errors must include a code suitable for tests/logging and a user action.
Representative messages:

- `image_source_not_found`: `Cannot attach image: file does not exist or is not
  readable. Choose a readable PNG, JPEG, or WEBP inside the allowed scope.`
- `image_source_out_of_scope`: `That image is outside the permitted file scope.
  Move it into the project or grant the required scope.`
- `image_type_unsupported`: `This file is not a supported image format. Use
  PNG, JPEG, or WEBP.`
- `image_too_large`: `Image is 14.2 MiB; the active model allows 8 MiB. Resize
  it or select a different profile.`
- `image_input_unsupported`: `The active profile <name>/<model> does not have
  verified image-input support. Switch to a vision-capable public profile.`
- `image_oauth_unverified`: `Image input is not enabled for openai-oauth because
  the ChatGPT/Codex media contract is unverified. Use a verified profile.`
- `image_generation_unsupported`: `The active profile does not have verified
  image-generation support.`
- `image_generation_transport_rejected`: `The provider rejected image
  generation for this transport/model. No image was generated; capability has
  not been assumed.`
- `generated_image_artifact_failed`: `The provider returned an image but Kollab
  could not persist it safely. The image data was not added to history.`

Provider raw errors may be retained in debug logs with standard secret/redaction
rules; user-facing strings must not expose OAuth tokens, signed URLs, base64, or
private local paths unnecessarily.


## phased implementation plan

### phase 0: contracts and capability plumbing

1. Define the normalized part/source/generated-artifact types in
   `packages/kollabor-ai/src/kollabor_ai/providers/models.py` or its canonical
   message-model layer.
2. Add typed capability records to the provider/model/loadout registry.
3. Mark all unknown transports false, including `openai-oauth`; add no optimistic
   fallback.
4. Widen conversation/history serialization to preserve legacy string content
   and structured content safely.
5. Add tests proving old saved sessions and all text-only provider payloads are
   unchanged.

Exit criterion: capability lookup and schema round trips work, but no UI command
sends image input yet.

### phase 1: public provider image input

1. Build the shared attachment validation/preparation service.
2. Add `/image <path-or-https-url> [prompt]` through the existing slash-command
   registry and user-input pipeline.
3. Add an affirmative, model-specific public Responses capability only after
   official wire contract validation.
4. Implement provider-local text/image-part serialization in
   `OpenAIResponsesProvider._prepare_request()` while keeping legacy string
   payloads identical.
5. Add compact terminal attachment echo/display and structured history handling.
6. Implement consent, policy configuration, size/type/scope guards, and clear
   errors.
7. Document usage, supported models, limits, and privacy behavior.

Exit criterion: a supported public profile can answer a text-plus-local-image
and text-plus-HTTPS-image turn; unsupported/OAuth profiles reject locally before
media leaves the machine.

### phase 2: output model and artifact rendering foundation

1. Add generated-image content/event types to unified response and streaming
   contracts.
2. Update the Responses transformer to preserve documented image outputs rather
   than dropping them.
3. Add the managed artifact writer/metadata/persistence contract.
4. Teach terminal history/display to render artifact summaries and safe paths.
5. Add artifact lifecycle/retention controls and tests.

Exit criterion: a fixture representing a supported provider result produces a
managed artifact and textual fallback with no base64/binary leakage.

### phase 3: image generation, provider by provider

1. Select one non-OAuth provider/model with a documented generation mechanism.
2. Verify request and output delivery against a real test account without
   credentials in fixtures/logs.
3. Add a narrowly scoped generation capability, explicit invocation UX, request
   serializer, transformer, and artifact path.
4. Add end-to-end tests and a manual smoke test.
5. Keep generation disabled for every other transport, especially OAuth/Codex.

Exit criterion: the selected provider can generate a safe managed artifact and
all unsupported profiles fail explicitly.

### phase 4: OAuth/Codex decision and optional Web UI

OAuth enablement is contingent, not scheduled. Run the contract probe described
above. If any required stage is undocumented/rejected/unstable, retain false
capabilities and document that limitation. If all stages are proven, implement a
separate OAuth serializer/event parser and tests rather than broadening the
public-path code with assumptions.

Web UI attachment composition can be proposed after terminal and canonical
contracts are stable. It must reuse phase 1 services and complete a dedicated
browser upload security review.


## test plan

### schema and persistence

- legacy string messages serialize/restore exactly as before;
- text-plus-image descriptors round-trip as JSON without bytes/base64;
- session/history export redaction removes sensitive local path details when
  configured;
- restore never performs a local read or automatic resend;
- context trim either retains a supported multimodal turn or makes its loss
  explicit; it never silently stringifies/drops the image.

### attachment validation and command

- accepted PNG/JPEG/WEBP local files and accepted HTTPS URL;
- absent, directory, FIFO/device, unreadable, symlink-out-of-scope, malformed,
  zero-byte, oversized, excessive-pixel, and unsupported-format inputs;
- path scope checked both at selection and request-time read;
- URL rejects HTTP, file/data schemes, userinfo, and policy-denied hosts;
- `/image` produces one user turn with ordered text/image parts;
- confirmation allowed/denied behavior and no provider call on denial;
- ordinary pasted text and normal path-like chat text retain current behavior.

### provider serialization

- public Responses legacy strings generate byte-equivalent existing input items;
- public Responses text plus URL/local image maps to the documented wire shape;
- provider-specific file IDs cannot cross providers/transports;
- source/size/capability validation happens before HTTP request;
- unsupported model/provider rejects locally;
- OAuth/Codex requests contain no image fields while capability is false;
- every known OAuth field restriction remains preserved (`store`, output budget,
  continuations, public cache controls).

### output and artifacts

- text/tool/reasoning transformer regression tests remain green;
- fixture image output produces `GeneratedImageContent`, not an empty text block;
- output-item and SSE variants preserve a final artifact event; progress does
  not leak binary/base64 through text deltas;
- URL/base64/file-id delivery validates type/size/signature and writes private
  session artifact files;
- artifact write/download/decode failures leave a bounded error record and no
  orphan raw data;
- terminal and history show safe artifact summaries and preserve metadata.

### live probes and regression

- maintain redacted, fixture-based unit tests for every enabled capability;
- before each new OpenAI OAuth capability, run a manual credentialed probe
  against the exact Codex endpoint/model and capture only redacted structural
  evidence;
- run focused provider/transformer/conversation/command tests, then relevant
  TUI/Web UI tests and the broader suite affected by changed message models.


## acceptance criteria

### phase 1 image input

- [ ] `/image <path> [prompt]` accepts one validated local PNG/JPEG/WEBP only
      under the permitted scope and with explicit transmission consent.
- [ ] `/image https://... [prompt]` works only where the active capability
      permits URL input; unsafe URL forms are rejected locally.
- [ ] Text-only input, existing saved conversations, tool calls, and legacy
      provider request payloads remain unchanged.
- [ ] Structured image turns persist descriptors/metadata without bytes/base64
      and are never automatically re-read on restore.
- [ ] A public, verified Responses profile serializes documented image parts.
- [ ] `openai-oauth` rejects image input locally as unverified unless a
      model/transport-specific probe and test explicitly enable it.
- [ ] Every validation/capability failure is actionable and makes no network
      request or secret/media leak.

### phase 2/3 generated images

- [ ] Generation is disabled unless an affirmative provider/model/transport
      capability declares both request and delivery support.
- [ ] Transformer and streaming code retain a final generated-image artifact
      without treating binary or base64 as text tokens.
- [ ] A generated image is persisted in private managed storage with digest,
      MIME, dimensions where known, and safe provenance metadata.
- [ ] Terminal/UI surfaces render a textual artifact fallback even where image
      preview is unavailable.
- [ ] Provider rejection, download/decode failure, and artifact-write failure
      are visible and do not corrupt conversation history.
- [ ] `openai-oauth` generation remains disabled until real contract evidence
      covers tool/request acceptance, binary/final delivery, and SSE behavior.


## rollout, observability, and rollback

### rollout

Ship behind a disabled-by-default multimodal feature setting and capability
registry. Enable phase 1 only for a narrowly selected public model/profile after
unit tests plus a manual smoke test. Do not use an OAuth account as the first
launch target. Enable generation independently after phase 2/3 acceptance.

Use staged rollout flags at the provider/model/transport level, not a global
`images_enabled` boolean. This prevents a verified public API capability from
accidentally enabling the separate OAuth/Codex transport.

### observability

Emit structured, redacted events/counters for:

- image command attempted/accepted/rejected, with source kind and rejection
  reason but no raw path, URL query, bytes, or tokens;
- selected provider/model/transport capability decision;
- validation byte/pixel buckets and request-time encoding duration;
- provider image-input request success/failure status;
- generation request/delivery kind, artifact write success/failure, and artifact
  byte-size bucket;
- unknown image output item/SSE event types, sampled and redacted, to identify
  contract drift without recording image payloads.

Logs must never contain image data, base64, signed generated-image URLs, OAuth
credentials, or unredacted local paths by default. Capability probe records must
be stored outside ordinary session logs and scrubbed before sharing.

### rollback

Disable the relevant capability entry first; `/image` then returns the local
unsupported-capability error and no media request is made. Revert the affected
provider serializer/transformer/artifact feature independently where possible,
while preserving backward-compatible reading of already persisted structured
message descriptors and generated artifact metadata. Do not delete user
artifacts automatically as part of a code rollback.
