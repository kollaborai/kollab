# Voice terminal capability: frozen contract

Status: FROZEN FOR IMPLEMENTATION per Marco's locked decisions in
[#295 comment](https://github.com/kollaborai/kollab/issues/295#issuecomment-6348343853).

## PURPOSE

Expose one repository-rooted terminal capability through ordinary Kollab agent
admission and permissions, with an independent code-level deny gate and a
one-time explicit voice confirmation. Denied or unclassifiable commands never
reach permission prompting or process creation.

## SIGNATURE

```python
class VoiceTerminalPolicy(Protocol):
    def classify(
        self, command: str, repository_root: Path,
        binding: VoiceSessionBinding
    ) -> TerminalPlan: ...
    async def request_confirmation(
        self, plan: TerminalPlan
    ) -> CommandConfirmationRequest | None: ...
    async def accept_confirmation(
        self, plan: TerminalPlan, ack: CommandConfirmationAck
    ) -> VoiceCommandApproval: ...
    async def execute(
        self, plan: TerminalPlan, binding: VoiceSessionBinding,
        approval: VoiceCommandApproval
    ) -> ToolExecutionResult: ...
```

```text
TerminalPlan = {
  plan_id: UUIDv4,
  command_digest_sha256: lowercase 64-char hex,
  classification: "blocked" | "read_only" | "mutating_or_risky",
  normalized_action: string,
  destination_relative_to_root: string | null,
  canonical_cwd: Path,
  reason_code: VoiceErrorCode | null
}
VoiceCommandApproval = {
  confirmation_id: ConfirmationId | null,
  command_digest_sha256: lowercase 64-char hex,
  generation: Generation,
  voice_confirmed: boolean
}
```

`TerminalPlan` is server-side only. `CommandConfirmationRequest` is emitted as
`voice.permission` and spoken through `SpeechOutputPolicy` + the sole
`SpeechAdapter`; the terminal module does not synthesize audio itself.

## INPUTS / OUTPUTS AND ORDER

Input is one UTF-8 command (frozen max 8 KiB), authenticated
`VoiceSessionBinding`, and canonical repository root. Order is mandatory:

1. Parse/normalize the entire supported command grammar and resolve the working
   directory and destinations under the repository root.
2. Return `command_blocked` / `unsafe_syntax` immediately for blocked or
   ambiguous input. No read-back request, PermissionManager request, or process
   creation occurs.
3. For every allowed mutating/risky action, create a
   `CommandConfirmationRequest` containing the exact normalized action and
   destination, original command `TurnId`, generation, and SHA-256 digest. Emit
   it to WebUI and read it back verbatim in spoken words through the speech
   policy/adapter. The user speaks a separate final `confirm` or `deny` turn.
4. Build `CommandConfirmationAck` server-side from that final transcript and
   authenticated LiveKit participant. Validate confirmation ID, original
   command turn, new confirmation turn, owner, generation, digest, expiry, and
   one-time use. Any mismatch/ambiguity denies.
5. After voice confirmation, call the ordinary `PermissionManager` path. Voice
   confirmation is not a permission grant; configured permission mode and UI
   approval still apply.
6. Only after both gates pass call `ToolExecutor` with canonical repository
   `cwd`; sanitize/bound the result before UI/TTS.

Only exact final transcript `confirm` or `deny` is accepted for a pending spoken
confirmation. Other/uncertain text cancels the pending command with
`confirmation_mismatch`; it is never interpreted as approval. The server consumes
this confirmation turn before ordinary chat admission.

## HARD BLOCKS FROM ISSUE #295

Fail closed for `sudo`, recursive `rm`, service install/uninstall/stop/restart,
process control (`launchctl`, `killall`, `pkill`), `git push`, `git tag`,
`gh pr merge`, restart/update/reset-config flags, `kollab --hub stop`, and
`kollab --hub kill all`. A blocked command remains blocked after voice
confirmation or PermissionManager approval. Malformed shell, unsupported
compound commands, substitution, redirection, chaining, or parse ambiguity is
blocked before spawn. Classify parsed tokens/grammar; do not copy demo regexes.

`kollab --hub status` and `kollab --hub capture` may skip the additional voice
confirmation but never skip ordinary PermissionManager checks. `kollab --hub
msg` always reads recipient and full message back and requires explicit voice
confirmation even though it is not a terminal mutation.

## ERRORS

Use shared `VoiceErrorCode`: `command_blocked` / `unsafe_syntax` before any
prompt or process; `confirmation_required` is a pending `voice.permission`
event; `confirmation_expired` / `confirmation_mismatch` denies before process;
`permission_denied` is the existing PermissionManager result; `resource_shortage`
and `internal_error` are sanitized tool results. All outcomes carry a stable
reason code; no custom HTTP error namespace.

## BUDGET REF

- Frozen command input cap: 8 KiB UTF-8. Frozen output cap: 8 KiB after
  redaction; oversized output reports truncation and retains no hidden tail.
- Confirmation expires after 30 s by a host monotonic deadline; UTC expiry is
  display-only. One ack is accepted once. Timeout/cancel/replay denies.
- Reuse existing ToolExecutor timeout, default 60 s; the frozen voice maximum
  is 60 s. No process starts while read-back or confirmation is pending.

## GOTCHAS

- `PermissionManager` can auto-approve when disabled/trust-all; the independent
  code-level deny gate still runs first. Voice confirmation never bypasses it.
- Force canonical `cwd` under the repository root; reject path/symlink escape and
  shell `cd` outside root. Execute only a validated grammar; avoid `shell=True`
  for the guarded voice command path.
- The ack actor comes from the owner-bound LiveKit participant, never a caller
  field. A generation switch invalidates all pending confirmations.
- Fixed `voice_terminal_hub_v1` policy exposes only the repo-root terminal
  capability and permitted `kollab --hub status/capture/msg` operations on
  voice-originated turns. Do not expose `terminal-kill`, background session
  control, MCP, or other tools to voice turns. Non-voice chat keeps its normal
  tool registry.
- Never log full commands, message bodies, secrets, raw output, or confirmation
  transcripts. This terminal-safety contract is frozen for implementation.
