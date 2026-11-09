# Usage ledger

The usage ledger records token counts and estimated cost for every LLM turn,
per agent, per lane, per model, per PR and per day. It exists so the fleet can be
right-sized (which agent or lane needs the expensive model) from measured data.

It is **off by default** and **local only**. Nothing is sent anywhere.

Implementation: `packages/kollabor-ai/src/kollabor_ai/usage_ledger.py` (pricing,
records, storage, rollups, alerts) and `plugins/usage_ledger_plugin.py` (hooks and
`/usage`). The turn-level payload comes from `LLM_REQUEST_POST` in
`packages/kollabor-agent/src/kollabor_agent/queue_processor.py`.

## Enable

```json
{
  "plugins": {
    "usage_ledger": {
      "enabled": true
    }
  }
}
```

Set it in `~/.kollab/config.json` or the project's `.kollab/config.json`, then
restart the agent. Turning it on takes effect at startup, because the hooks and
storage are set up then. Turning it off stops recording at once, since every
write checks the flag.

## Configuration

| Key | Default | Meaning |
| --- | --- | --- |
| `plugins.usage_ledger.enabled` | `false` | Master switch. Off registers no hooks and writes nothing. |
| `plugins.usage_ledger.retention_days` | `30` | Rows older than this are pruned (hourly, and at startup). |
| `plugins.usage_ledger.price_table` | `{}` | Per-model rates. Keys are `provider/model` or `model`. |
| `plugins.usage_ledger.budget_alerts` | `[]` | Threshold rules (see below). |
| `plugins.usage_ledger.lane` | `""` | Lane label for this agent's turns. |
| `plugins.usage_ledger.pr` | `""` | PR label for this agent's turns. |

`KOLLAB_USAGE_LANE` and `KOLLAB_USAGE_PR` override `lane` and `pr` for one
process. This is how a lane attributes a run without editing config:

```bash
KOLLAB_USAGE_LANE=issue-723 KOLLAB_USAGE_PR=742 kollab --agent coder --detached
```

### Price table

Rates are per million tokens, in USD:

```json
{
  "plugins": {
    "usage_ledger": {
      "price_table": {
        "anthropic/claude-opus-5-5": {
          "prompt_per_million": 15.0,
          "completion_per_million": 75.0,
          "cache_read_per_million": 1.5
        }
      }
    }
  }
}
```

`cache_read_per_million` is optional and defaults to 10% of the prompt rate. A
price table entry wins over the built-in rates. An invalid entry is ignored with a
log line. Without a price, the cost reads as **unpriced**. It is never guessed
or shown as zero.

### Budget alerts

```json
{
  "plugins": {
    "usage_ledger": {
      "budget_alerts": [
        {"scope": "lane", "name": "issue-723", "threshold_usd": 5.0, "window_hours": 24}
      ]
    }
  }
}
```

- `scope` is `lane` or `agent`. `name` is the lane or agent it watches.
- The window is aligned to the epoch (for example, each UTC day for 24 hours).
- Each rule fires **at most once per window**. The alert is written to the ledger
  as a `budget_alert` row, which both suppresses repeats after a restart and gives
  the audit trail.
- Unpriced turns add no cost and are counted in the alert's `unpriced_turns`.

## Reading the ledger

In the agent, `/usage` prints a summary (by agent, by lane and agent, by model, by
PR). `/usage json` prints the full rollup, including by day and by lane, agent and
model together.

When the ledger is empty, `/usage` says so. When it is off, `/usage` says that and
names the key to set.

The ledger file is `~/.kollab/projects/<encoded-project>/usage/ledger.jsonl`. It is
owner-only (0600). Each line is one JSON object:

```json
{"kind":"turn","ts":1793800000.123,"day":"2026-11-04","agent":"coder","lane":"issue-723",
 "pr":"742","provider":"anthropic","model":"claude-opus-5-5",
 "model_key":"anthropic/claude-opus-5-5","usage_source":"provider",
 "tokens_in":12840,"tokens_out":912,"cache_read_tokens":10400,"cache_write_tokens":0,
 "wall_ms":8421,"cost_usd":0.0412,"cost_status":"priced"}
```

## Reading missing data

The ledger distinguishes "no data" from zero:

- `usage_source: "provider"` means the provider reported counts.
- `usage_source: "estimated"` means some counts were estimated (for example, a
  stream cut short). They are kept and marked.
- `usage_source: "none"` means the provider reported nothing. Token fields and cost
  are `null`, and `turns_without_usage` counts the gap.

`cost_status` is `priced`, `unpriced` (no known rate) or `no_usage`.

## What is recorded, and what is not

Recorded: agent name, lane, PR label, provider, model, token counts, cache counts,
wall time from the first request of a turn to the final response, and cost.

Never recorded: prompts, outputs, tool arguments, or credentials.

`wall_ms` covers the model requests of the turn, not the time tools run between
them. A turn that ends in a provider error writes no row.

## Limits

- The hub and web UI do not display these figures yet. Read `/usage` or the JSONL
  file.
- Lane and PR labels are per process. Nothing infers them from git branches.
- Turning the feature on, and changing `retention_days`, require a restart.
  `price_table`, `budget_alerts`, `lane` and `pr` apply from the next turn.
