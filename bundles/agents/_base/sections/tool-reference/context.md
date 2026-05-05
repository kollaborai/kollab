context service — tracking and curating heavy items:

your conversation history grows as you work. tool results, file reads,
and terminal output accumulate. the context service tracks every heavy
item (over 8KB) as a "ledger entry" with an ID like ctx-1, ctx-2, etc.

when the ledger gets large, the context service asks you to decide
what stays verbatim vs what gets replaced with a summary at compaction
time. you make these decisions with <curate> tags.


file read dedup:

re-reading the same file returns a short "stale hit" marker instead of
the full content. the context service remembers what you already have
in context by content hash. if a file has changed since you last read
it, you get a change summary with hash/size delta.

to force a fresh re-read (e.g. you suspect the file was silently
modified):
  <read force="true"><file>path/to/file.py</file></read>


marking an entry to keep verbatim:

  <curate id="ctx-1" decision="keep">
  explain why you need this verbatim
  </curate>

use for files you're actively editing or data you need to reference
exactly. kept entries survive compaction unchanged.


marking an entry for summary replacement:

  <curate id="ctx-2" decision="summary">
  write a compressed version here. this exact text replaces the full
  tool result at compaction time. include everything future-you needs.
  </curate>

use for material you've already extracted what you need from. your
own summary is higher quality than the generic fallback.


inspecting the ledger:

  <context/>
  <context filter="pending"/>
  <context filter="file_read"/>
  <context filter="path:plugins/hub"/>

shows your current ledger on the next turn. read-only, no side effects.


evicting an entry immediately:

  <evict id="ctx-3">
  explain why you're dropping it now
  </evict>

eviction breaks prefix cache from that message forward. only use when
the session has >=10 more turns AND the entry is >=32KB.


when to curate:
  - when the curator prompts you (automatic at 300KB threshold)
  - proactively, when you notice a heavy item you're done with
  - before ending a major work phase

changing a prior decision: emit a new <curate> tag with the same id.
last-write-wins.

free operations (no cache cost):
  <context/>   — query the ledger (read-only)
  <curate>     — update decisions (in-memory flag flip)

cache-breaking operations:
  <evict>      — marks entry as evicted, removes from ledger totals
