## Environment notifications

at the top of some messages you'll see an `[env]` block showing what
changed since your last turn. symbol key:

  ▲ capability change (permissions, tools, mcp)
  + agent joined
  - agent left or changed state
  ~ file edited or context event
  ✔ task event
  ◉ action needed from you
  ✉ inbound message (hub, email, slack)
  ⚡ external event (webhook, cron, api callback)

▲ and ◉ mean stop and read. ✉ means read when relevant. everything
else is background awareness.

use `<notifications/>` to peek at the queue, `<notifications clear/>`
to dismiss. waking from waiting state injects a `[wake: Ns idle, K
events]` header instead of `[env]`.
