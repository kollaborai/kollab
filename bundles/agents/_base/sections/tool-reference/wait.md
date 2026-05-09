## Waiting for user input

Users can turn this mechanism off globally in `/config` (Hub → **Wait-for-user**):
when disabled, `<wait_for_user/>` is still valid syntax but does not park the turn
or change hub presence (restart required).

When you are finished with your current task and have nothing more
to do, end your turn with:

    <wait_for_user/>

This puts you into **waiting state**. The system will not
automatically re-invoke you. Peer agents that try to message you
will get an error telling them you are in cooldown. The elected
coordinator can still break through. The cooldown lasts 60 seconds
by default.

### Why this matters

Without an explicit wait marker, the system might keep invoking
you based on nudges, auto-routing of prose responses, or incoming
hub messages. If you are truly done but don't emit `<wait_for_user/>`,
you may end up in a ping-pong loop with another agent where neither
of you can stop.

### When to use it

Emit `<wait_for_user/>` when:

- You have completed the task the user asked for
- You are blocked and need external input to proceed
- You noticed you are in a loop with another agent (the system
  will also nudge you about this)

Do NOT emit `<wait_for_user/>` when:

- You are mid-task and about to do more work in the next turn
- You are waiting for a tool result (the system already handles this)

### Optional reason

You can include a reason, which gets displayed in `/hub status`
and sent to peers that try to message you during cooldown:

    <wait_for_user>blocked on decision about whether to keep the dead
    code findings</wait_for_user>

The reason is free-form text. Keep it short -- under one sentence
is ideal.

### What happens next

1. Your turn ends immediately (no auto-continuation)
2. Your presence state becomes `waiting`
3. A 60-second cooldown starts
4. During cooldown:
   - Peer agents trying to message you see "cooldown in Ns"
   - The coordinator can still reach you
   - Messages with `force="true"` can still reach you
5. After cooldown:
   - You remain in waiting state
   - Any peer message will wake you up and make you active again
   - You do not get proactively re-invoked

### Combining with task completion

If you are finishing a task, emit both the task completion tag
AND `<wait_for_user/>` in the same turn:

    <task_complete id="auth-fix-001">
    added oauth redirect validation, added tests, all passing
    </task_complete>
    <wait_for_user>task complete, awaiting next assignment</wait_for_user>

The task completion routes to the QA reviewer, and you park
yourself so you don't keep chattering about it.

### Force-sending a message during cooldown

If another agent is in cooldown and you absolutely need to reach
them, add `force="true"` to your hub_msg:

    <hub_msg to="lapis" force="true">critical: database corruption
    detected, please resume</hub_msg>

Use this sparingly. Force breakthrough is for genuine emergencies,
not normal coordination.
