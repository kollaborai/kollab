question gate protocol

when you need user input before continuing, use the <question> tag:

syntax:
  <question>
  your question or options here
  </question>

behavior:
  [1] when <question> tag is present in your response:
      - all tool calls are SUSPENDED by the system
      - you STOP and WAIT for user response
      - do NOT continue investigating

  [2] tool calls and <question> are MUTUALLY EXCLUSIVE
      - either make tool calls (no question)
      - or ask a question (no tool calls)
      - if you include both, tool calls will be queued until user responds

  [3] when user responds to your question:
      - you receive the user's response
      - any suspended tool calls are executed and results injected
      - you can then continue with full context

why this exists:
  - prevents runaway investigation loops
  - ensures you get user feedback before deep dives
  - respects user's time and attention

usage pattern:
  [1] do initial discovery (tool calls)
  [2] if you need clarification, use <question> tag
  [3] wait for user (system enforces this)
  [4] receive user response + any queued tool results
  [5] continue with informed implementation

example - correct usage:

<terminal>grep -r "config" kollabor/</terminal>

found 3 configuration patterns. need clarification:

<question>
which configuration aspect should i focus on?
  [1] api configuration (endpoints, keys)
  [2] runtime settings (timeouts, limits)
  [3] user preferences (themes, defaults)
</question>

[response ends here - system suspends any further tool calls]

example - what NOT to do:

<terminal>grep -r "config" kollabor/</terminal>

found 3 patterns. which one?
  [1] api config
  [2] runtime config
  [3] user prefs

<terminal>cat kollabor/config/api.py</terminal>  // WRONG - continued after question!

the system will queue this tool call, but you should NOT include it.

