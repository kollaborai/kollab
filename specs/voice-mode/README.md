# Voice mode design

Start with [Device voice service: current state, gaps, and replacement spec](device-service-redesign.md).

That document incorporates Marco's September 26, 2026 direction: automatic setup,
Whisper base, one shared device service, timestamped transcription, an AI consumer,
a shared sentence playback queue, and visible operating state. It distinguishes
the historical audit from the replacement contract. The user requested its
implementation; [the evidence ledger](implementation-status.md) records what has
been implemented and checked. [Usage](../../docs/features/voice-mode.md) describes
the current command and service behavior.

The other files in this directory are historical proposals and contracts. Their
model choices, per-session engine ownership, setup wizard, gate implementation,
and first-sentence-only output policy are not requirements for the replacement.
Keep them as provenance after retirement; do not combine
their conflicting requirements into another implementation.

The old core, gate, wizard and synthetic conformance implementation have been
retired. Recovery locations and verification limits are listed in the ledger.

The [classifier extension](classifier-providers.md) makes Laya the default and
keeps the original active-provider classifier selectable. It supersedes the
provider-only choice in the original audit.

The [spoken response contract](spoken-response-review.md) separates screen text
from narration, adds outgoing style review and permits one bounded correction.
It supersedes any earlier policy that reads all visible assistant prose aloud.
