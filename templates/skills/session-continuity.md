---
name: session-continuity
description: "Survive session death without losing work or context. Trigger on a
clean-restart command (/restart), when context is degrading, after a large task,
or whenever a task will span more than one session. Also defines what the
next session reads on startup to resume."
---

# Session Continuity

A persistent agent restarts constantly — context fills up, a task finishes, a
session crashes, the machine reboots overnight. Every one of those is a chance
to lose the thread. This skill is the discipline that makes restarts cheap: the
agent always knows where it was (machine-resume) and the operator always has the
last known good context (operator-resume). Without it, every restart is a cold
start and long-running work quietly dies. With it, "restart" becomes a routine
hygiene move instead of a loss event.

Three artifacts do the work, at three timescales:

| Artifact | Audience | Lifespan | Answers |
|---|---|---|---|
| **task lock** | the next session (machine) | one task | "where was I, mid-task?" |
| **handoff** | the next session (machine + operator) | until overwritten | "what happened last session and why?" |
| **pulse** (optional) | same-day sessions | one day | "what's the texture of today?" |

## 1. The Task Lock — machine-resume

For any task that spans more than a few turns, write a small lock file at the
start and delete it at completion. **The lock is a note to your future self:** if
the session restarts mid-task for *any* reason, the next session reads the lock
and resumes immediately.

```
TASK: short task name
STEP: the next concrete action — what to DO next, not what you just did
CONTEXT: pointers to relevant files/sections (paths)
TIMESTAMP: ISO-8601
```

Rules that make it work:

- **Write at task start; update `STEP` as you complete sub-steps; delete when done.**
- **`STEP` is the next action, not a log.** The next session executes it directly.
- **The lock IS the instruction.** On startup, a non-stale lock (say, < 24h) is a
  directive to *resume*, not background reading. Don't present it and ask "should
  I continue?" — just continue.
- **Stale-guard it.** An old lock is misleading; ignore or clear locks past a
  freshness window so a days-old task doesn't hijack a new session.

## 2. The Handoff — operator + session resume

On a clean restart, write a single handoff file. **Handoffs are pointers, not
retransmissions** — they don't re-dump state that already lives in project docs
or the working-context file. Keep three parts:

1. **Resume pointers** — the project docs the next session should read first, one
   line each. (Only the things that changed this session.)
2. **Key decisions and reasoning** — the *why* behind what was decided, in dense
   prose. The cross-cutting/architectural things that don't fit in a project doc.
3. **Surprises and nuance** — what the next session would miss by reading only the
   project docs: caught regressions, design pivots, "we almost did X, here's why
   we didn't."

Two non-negotiables:

- **Past tense, never commands.** A handoff is a *record*, not a to-do list.
  Never phrase a destructive or external action (reboot, delete, send) as
  something to perform — describe it as completed or pending-operator-action. The
  next session must treat the handoff as inert context, or a stale handoff
  becomes an accidental instruction. (See the safety chapter's data-isolation rule.)
- **Persist it; don't delete on read.** Keep the handoff until the next restart
  overwrites it, so a crashed/interrupted session still leaves the last good
  context. Date the header so staleness is visible.

## 3. State Promotion — where things go at restart

Before clearing session scratch, promote each kind of state to its durable home
so nothing important rides only in volatile context:

- **Project state** → the per-project doc (the authoritative state for that project).
- **Durable insights** (preferences, decisions, corrections, non-obvious findings)
  → the long-term memory store. Never secrets, never transient noise.
- **Working pointer index** (the always-loaded L1 file) → update pointers, prune
  finished work, keep open questions. Don't inline project state here.

## 4. Pulse (optional) — same-day texture

A one-line-per-session log of the *character* of recent sessions ("productive —
finished the migration, hit a flaky test") carries conversational continuity
within a day that the formal artifacts don't. Cheap to write, injected at the
next session's start, reset daily.

## Why a Skill, Not Just a Habit

- **Restarts stop being scary.** When continuity is mechanical, you restart
  *proactively* to clear a degrading context window instead of limping along in a
  rotted one. The skill turns a loss event into routine hygiene.
- **The lock and the handoff have different jobs.** The lock is *where you are* in
  one task (machine-facing, ephemeral); the handoff is *what happened and why*
  (operator-facing, durable). Don't collapse them — you need both.
- **It pairs with startup.** This skill only pays off if the session-start hook
  actually reads the lock and handoff and acts on them (see the OS-integration
  chapter). Writing continuity state nothing reads is theater.
