# Protocols

## Session Startup

The `SessionStart` hook gathers all startup context and injects it as
`additionalContext` — no tool calls needed at the start of a session.

The hook provides (in priority order):
- Cold boot recovery signal (if the machine just rebooted)
- Task lock resume directive (if a task was in progress)
- Durable background task restoration (crons/monitors to re-create)
- System state (toggle flags, usage)
- Handoff from previous session (if present)
- Today's pulse (same-day session continuity)
- Task index
- Graph memory recall (cold start only)

On every new session:
1. Read the injected context — it's already there, no commands to run.
2. If task.lock is present, start working on STEP immediately. The lock is
   the instruction — do not present it and ask what to do.
3. If a `## Restore Background Tasks` section appears, re-create each listed
   cron/monitor and update the background-tasks registry with the new IDs.
4. Respond conversationally — you have everything you need.

## Task Lock

For any substantive task that spans more than a few turns, write
`~/.agent/state/task.lock` at the start and delete it at completion. This
is a note to future-you: if the session restarts mid-task, the next session
reads the lock and resumes immediately.

Format (simple key-value, one per line):
```
TASK: Short task name
STEP: Next concrete action (what to DO, not what you did)
CONTEXT: Pointers to relevant docs (paths, sections)
TIMESTAMP: ISO-8601
```

Rules:
- Write at task start (TASK, STEP, CONTEXT, TIMESTAMP)
- Update STEP as you complete sub-steps
- Delete when the task is finished or abandoned
- Stale locks (> 24hr by mtime) are auto-deleted by the startup hook
- "Substantive task" = anything that would get a handoff mention

The lock IS the instruction. On startup with an active lock, start working
immediately — don't present the lock and ask.

## Durable Background Tasks

When creating a cron/monitor that should survive session restarts, register
it in `~/.agent/state/background-tasks.json`:

```json
{
  "crons": [
    {
      "label": "daily-health-check",
      "cron": "0 9 * * *",
      "prompt": "Run the daily health check.",
      "durable": true
    }
  ],
  "monitors": [
    {
      "label": "watch-build-output",
      "command": "tail -f ~/.agent/logs/build.log | grep -E 'ERROR|DONE'",
      "durable": true
    }
  ]
}
```

Session-scoped entries use `"session_scoped": true` instead — these are
auto-purged on the next session start. The startup hook reads this file,
purges stale entries, and injects a restore directive for durable ones.

## Memory

**Recall** — before responding to questions involving past work:
Run a graph memory query. Craft focused, keyword-rich queries.

**Remember** — after responding:
Evaluate whether new durable knowledge was created.
If yes, store it: preferences, decisions, system facts.
Never store secrets, passwords, or transient noise.

## Restart / Shutdown

Three distinct modes — use the right one:

**Session refresh** (agent's call, no notification needed):
Triggers: context rot, stuck state, tool failure, end of large task.
Flow: update task.lock STEP, write pulse entry, exit.
The restart loop and task.lock carry continuity.

**Operator shutdown** (operator requests, or scheduled nightly):
Full flow: write handoff, run summarizer, run reflection, backup memory, shutdown.
Never skip the handoff — it's the bridge the next session will see.

**Machine restart** (operator request or system-level problem):
Requires: operator request, or real system problems (memory pressure, USB
failures, init system failures). "Session complete" is NOT a reason.
Flow: notify operator first, write handoff, log the reason, restart.

Don't restart as a first resort — troubleshoot first, restart when it's
the right tool.

## Async Commitments

Any statement about future action is only literally true if a mechanism to
perform that action is attached in the same turn. Between user messages the
agent is inactive — bare words ("I'll check back in 10 min") are
unenforceable.

**Valid mechanisms** (attach in the same turn):
1. **CronCreate** — one-shot, self-rescheduling for periodic checks; register
   durable ones in background-tasks.json. Fires only at REPL idle.
2. **Background subprocess** (`claude -p`) — separate OS process, survives
   session restart; delivers via file drop + push notification.
3. **Dedicated launchd workers** — always-on, survive the agent being dead.
   Deliver via tmux inject or push notification. Do NOT repurpose the health
   check worker as a general scheduler.
4. **Monitor tool** — session-scoped event watch; dies on session restart.
   Register as durable in background-tasks.json if it must survive.

**Invalid mechanisms:**
- Bare words with no tool call
- Sub-agent-launched CronCreate (`claude -p` never enters idle REPL)
- Bash sleep loops

If a mechanism can fail silently, attach a failure notification path in the
same turn. Silent failure is worse than no commitment.

## Delegation Bias

Delegate to subagents earlier than efficiency suggests. Two reasons:
1. Presence over throughput — keep the main session conversational.
2. Context protection — subagents work in their own context windows and
   return tight summaries; tool-result noise stays out of the main session.

If a task is self-contained enough that a subagent *could* handle it, default
to delegating — even when inline would be slightly more token-efficient.
Tasks touching 5+ independent files: strongly prefer parallel subagents.
