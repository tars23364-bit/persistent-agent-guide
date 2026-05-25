# Autonomy & Authority

A persistent agent that only ever waits for explicit instructions is a fancy
autocomplete. The whole point of a long-running agent is that it can act while
you're asleep, away, or busy — start a job, watch a log, make a small call —
and tell you what it did when you get back. But unbounded autonomy ("just do
whatever you think is best") is how you end up with a force-pushed `main`, a
deleted dataset, or an email sent to the wrong person.

This chapter covers three patterns that together let an agent act on its own
*within explicit, operator-granted limits* and report back without nagging you:

1. **Standing Orders + authority tiers** — operator-granted, scoped authority
   delivered through a state file the agent cannot write.
2. **The Async-Commitment protocol** — a rule that prevents the agent from
   making promises it has no mechanism to keep.
3. **The Board** — an async, non-interrupting surface for decisions, questions,
   and status the operator should see but that don't warrant a push.

They're a family because they answer the three questions of autonomy: *what am
I allowed to do?* (Standing Orders), *did I actually arrange to do the thing I
said I would?* (Async-Commitment), and *how do I tell you what I did without
interrupting you?* (the Board).

## The Problem

There's a spectrum between two bad extremes:

- **Fully supervised.** Every action waits for a confirmation. Safe, but the
  agent is useless when you're not watching. A six-hour job that needs a babysit
  at hour three is no better than a manual job.
- **Fully autonomous ("yolo mode").** The agent does whatever it infers you'd
  want. One bad inference — and inference is probabilistic — and you've lost
  data or sent something irreversible.

Neither is what you want. What you want is **explicit, scoped, operator-granted
authority**: "you may act freely on *this project*, at *this risk level*, until
I say otherwise." The grant is narrow, legible, and revocable. The agent knows
exactly where the edges are, and so do you.

The key design principle running through all of this: **the agent never grants
itself authority.** Authority flows one way — from operator to agent — through
a channel the agent can read but cannot write. Everything else is built on top
of that boundary.

## Architecture

```
                         AUTHORITY (one-way: operator → agent)
   ┌────────────────────────────────────────────────────────────────┐
   │                                                                  │
   │   Operator-only UI ──writes──► ~/.agent/state/standing-orders.json
   │   (a GUI, a guarded                       │
   │    CLI — NOT the agent)                    │ read-only
   │                                            ▼
   │                              UserPromptSubmit hook injects:
   │                              [STANDING ORDERS ACTIVE: proj-a (standard),
   │                                                       proj-b (elevated)]
   │                                            │
   └────────────────────────────────────────────┼─────────────────────┘
                                                ▼
                                       Agent acts within tier
                                       (Execute → Verify → Report)
                                                │
            ┌───────────────────────────────────┼───────────────────────┐
            ▼                                   ▼                         ▼
   Async-Commitment check          ~/.agent/board/*.json         Push / escalation
   (every future-action claim      (non-interrupting surface,    (failures, hard
    needs an attached mechanism)    operator reads when free)     stops — Ch. 6)
```

The state file is the trust anchor. The hook reads it and tells the agent what
it's currently allowed to do. The agent operates inside those limits, never
edits the file, and reports through low-friction channels (the Board) reserving
interrupts (push notifications) for genuine exceptions.

## Pattern 1: Standing Orders + Authority Tiers

A **standing order** is a delegation: the operator grants the agent operational
authority over a named scope (a project, a workstream) at a named risk tier.
While the order is active, the agent acts within that scope without asking. When
it's inactive, the agent falls back to default-cautious mode — it can discuss
and plan, but not execute.

### The Grant Channel: A State File the Agent Cannot Write

The grant lives in a JSON state file:

```json
{
  "active": [
    {
      "project": "data-pipeline",
      "authority": "standard",
      "granted": "2026-05-20",
      "last_reviewed": "2026-05-20"
    },
    {
      "project": "site-rebuild",
      "authority": "elevated",
      "granted": "2026-05-22",
      "last_reviewed": "2026-05-22"
    }
  ]
}
```

This file is written **only by an operator-controlled surface** — a small GUI, a
guarded CLI, anything the human drives. The agent reads it; the agent never
writes it. This is not a soft convention you hope the model respects. Enforce it:

- Put the rule in the agent's always-loaded instructions ("you never write the
  standing-orders state file; only the operator's UI does").
- If you want a hard guarantee, make the file operator-owned and not
  agent-writable at the OS level, or have a `PreToolUse` hook reject any write
  whose path matches the state file (see [hooks in Chapter 8](08-safety.md)).

Why so strict? Because if the agent can edit its own authority grant, the grant
means nothing — a single bad inference ("I think the operator would want me to
have elevated authority here") collapses the entire safety model. Authority
direction is one-way by construction, not by good behavior.

### How the Grant Reaches the Agent

A `UserPromptSubmit` hook reads the state file every turn and, if anything is
active, injects a single line into the context:

```
[STANDING ORDERS ACTIVE: data-pipeline (standard), site-rebuild (elevated)]
```

No line means no active orders means default-cautious mode. The agent checks for
this line the way it checks for any other startup context. The injection is
cheap — one line — and it's just-in-time: the agent does **not** preload every
project's detailed standing-order file at session start. It reads the
project-specific order (`~/.agent/standing-orders/<project>.md`) only when it's
about to act on that project.

A minimal version of the hook:

```python
#!/usr/bin/env python3
"""standing-orders-inject.py — UserPromptSubmit hook."""
import json
from datetime import date, datetime
from pathlib import Path

STATE = Path.home() / ".agent" / "state" / "standing-orders.json"
STALE_DAYS = 30


def main():
    if not STATE.exists():
        return  # no orders, inject nothing
    data = json.loads(STATE.read_text())
    active = data.get("active", [])
    if not active:
        return

    parts = []
    today = date.today()
    for order in active:
        label = f"{order['project']} ({order['authority']})"
        reviewed = order.get("last_reviewed")
        if reviewed:
            age = (today - datetime.strptime(reviewed, "%Y-%m-%d").date()).days
            if age > STALE_DAYS:
                label += " ⚠stale"
        parts.append(label)

    print(f"[STANDING ORDERS ACTIVE: {', '.join(parts)}]")


if __name__ == "__main__":
    main()
```

The `⚠stale` marker is a small but valuable touch: standing orders accumulate
stale pointers (a project's "active phase," old job references, trigger hints
that point at finished work). When `last_reviewed` is older than 30 days, the
agent surfaces it — "It's been 40 days since we reviewed the data-pipeline
order; want to skim it?" — and re-stamps the date after review. It never
silently updates the date; staleness is surfaced to the operator, not dismissed.

### The Three Authority Tiers

A standing order grants authority at one of three tiers. The tiers map risk to
required ceremony:

| Tier | Scope (examples) | Action |
|------|------------------|--------|
| **Act freely** | Short runs (under a couple hours), pilot runs, monitoring, status checks, data prep, jobs on a shared cluster you don't own outright | Execute immediately. No confirmation, no notification. |
| **Act and notify** | Medium runs (a few hours) on a machine you do own, file operations in shared directories, feature-branch commits, config changes | Execute, then send a push notification with rationale. |
| **State and wait** | Long runs (over half a day), destructive operations, pushes to `main`, external communications, anything that blocks a shared machine | State the decision clearly. Do **not** start. Pivot to other work until the operator responds. |

The boundaries are deliberately concrete. "Under a couple hours / a few hours /
over half a day" is a duration ladder you tune to your own infrastructure — the
point is that *time-to-irreversibility* and *blast radius* go up as you descend
the tiers, and the required ceremony goes up with them. Set the actual
thresholds to match your machines: a long training run on a GPU box and a
five-minute lint pass deserve different tiers.

### The Authority Modifier

Each standing order's `authority` field shifts the whole tier mapping:

- `standard` → tiers exactly as defined above.
- `elevated` → **one step looser.** What would normally be "state and wait"
  becomes "act and notify"; "act and notify" becomes "act freely." Use this for
  weekends, overnight unattended periods, or a project where the operator wants
  to grant more rope temporarily.
- `restricted` → **read-only / monitoring only.** No execution at all, at any
  tier. Use this when you want the agent watching a project and reporting, but
  not touching it.

So a project at `elevated` lets the agent commit to a feature branch and run a
medium job without a notification, while the same project at `restricted` lets
it do nothing but watch and report. One field, three postures.

### Universal Constraints (Apply Regardless of Tier)

Some actions are dangerous enough that no tier or modifier unlocks them without
explicit, in-the-moment confirmation. These are floors, not ceilings:

- **Never delete training data, checkpoints, or results** without confirmation.
- **Never push to `main`/`master`** on any repo without confirmation.
- **Never send external communications** (outside a small trusted circle)
  without confirmation.
- **Notify on any failure.** Silent failure is worse than no commitment — the
  operator will eventually notice the missing result and won't know why.

Even an `elevated` order does not override these. They're the irreducible safety
core (see [Chapter 8](08-safety.md) for the broader safety model).

### The Execute–Verify–Report Pattern

Every action taken under a standing order follows the same three-beat loop:

1. **Execute** — do the work. Not "I'll do that," not "I would do X" — actually
   do it. The whole point of the grant is to remove the confirmation round-trip.
2. **Verify** — confirm the result against an external check. The file exists;
   the job shows as running in the scheduler; the row count matches; the commit
   landed on the right branch. Don't trust your own assertion that it worked
   (this is the same verification discipline from [Chapter 9](09-task-management.md)).
3. **Report** — log what was done, what was verified, and any anomalies. For
   "act freely" this can be a quiet log line or a Board update; for "act and
   notify" it's a push notification.

The Verify step is what separates a standing order from blind autonomy. Granted
authority is permission to act *and confirm*, not permission to assume.

### The Counter-Gating Discipline

There's a subtle failure mode when an agent helps draft its own standing orders:
it over-gates. Left to its own defaults, an agent asked to propose authority
limits will pile on restrictions — which is exactly backwards, because the
purpose of the system is to *unlock* action, not lock it down.

A useful counterweight: for every restriction proposed, require naming both —

1. A **blocked-case**: a specific action the restriction catches that you *want*
   it to catch.
2. A **permitted-case**: a specific action the restriction does *not* catch that
   you still want permitted.

If you can't name both, the restriction is over-engineering. Drop it or reshape
it until both hold. This is mechanical, not a matter of taste — it structurally
counter-weights the reflex to over-restrict.

### Authority Graduation

Actions that start at "act and notify" or "state and wait" often prove safe with
repetition. Rather than rewriting the whole order each time, keep a lightweight
promotion log in the order file:

```markdown
## Authority Graduation Log
| Action | Original gate | Current gate | Promoted | Notes |
|---|---|---|---|---|
| Restart the data-ingest worker after a crash | act and notify | act freely | 2026-05-21 | Done 3× without incident; bounded + low-risk |
```

Promote only when the action was performed several times under the old gate
without incident (or is clearly bounded and low-risk and the operator signs off
explicitly), the pattern is stable, and the operator approves in the moment.
Append a row; keep the original gate table intact so the history stays visible.

### One-Way Authority, Restated

It bears repeating because it's the whole game: **only the operator activates,
deactivates, or re-tiers a standing order.** If the agent thinks an order should
change — looser, tighter, expanded scope — it *says so and waits*. It does not
edit the state file and does not recommend a command that would. The moment the
agent can move its own gates, the gates are decorative.

## Pattern 2: The Async-Commitment Protocol

Here's a bug that looks like a feature. The agent says, helpfully:

> "I'll check back in 10 minutes to see if the job finished."

It sounds responsible. It is, in fact, a lie — not a malicious one, but a
structural one. **Between your messages, the agent is not running.** There is no
background thread counting down ten minutes. When you send your next message
(in ten minutes, or two hours, or tomorrow), the agent wakes up with no memory
that it promised anything and no trigger that fired. The "check back" never
happens, and you only notice when the result you were promised never arrives.

The rule that fixes this:

> **Any statement about future action is only literally true if a mechanism to
> perform that action is attached in the same turn.**

Bare words are not a mechanism. If the agent catches itself typing "I'll
follow up later" with no tool call in the same turn, it must either attach a
mechanism or change the statement.

### The Two Failure Modes

Future-action claims come in two shapes, and each needs a different mechanism:

- **Timed commitments** — "check back at 11:30," "remind you tomorrow morning,"
  "retry in an hour." These need a **scheduler**: something that will actively
  wake the agent at the named time.
- **Continuous-monitoring claims** — "I'll keep an eye on the training log,"
  "watch for the deploy to finish." These need a **running watcher**: either a
  session-scoped watch attached this turn, or a background worker that is
  *already running* and *already covering the thing in question*. Otherwise the
  claim is impossible to fulfill.

### Valid Wake-Up Mechanisms

These are Claude Code–flavored examples; your harness may differ. The principle
is what matters — *something external to the conversation must carry the future
action.* Adapt the specific tools to your setup.

1. **A one-shot, self-rescheduling cron from the main session.** Fires once at a
   future time, does its work, and (if recurring) schedules its next fire. In
   Claude Code this is a `CronCreate` issued from the interactive session — it
   fires at REPL idle so it can't collide with you typing. Good for periodic
   checks and for user-visible reminders that must survive a crash (make those
   durable).

2. **A backgrounded `claude -p` subprocess** — the "spawn a worker" pattern.
   Launch a detached process (`run_in_background`) that runs an instruction,
   sleeps as a real OS process, and delivers via file drop + push notification
   when done. It survives a main-session restart because it's a separate
   process.

3. **A dedicated background worker** (e.g. a launchd/systemd service). Always-on
   infrastructure that survives the agent being dead entirely. Delivers via
   queue file, push, or tmux injection. Use this for permanent monitoring, not
   one-off follow-ups — and don't repurpose an existing health-check worker as a
   general scheduler; give task-specific follow-ups their own worker.

4. **The SessionStart hook** — for crash recovery. If a schedule *should* have
   been durable but the process died, the startup hook can re-install it. (See
   [Chapter 3](03-context-management.md) on durable background tasks.)

5. **A session-scoped watch** — for "watch X for events" *within the current
   session*. In Claude Code this is the `Monitor` tool: you pass a filter
   command that emits one line per event of interest, and each line becomes a
   chat notification. Great for tailing a training log for epoch boundaries or
   errors. It dies on restart — if the watch must survive session boundaries,
   use a worker (mechanism 3) instead.

Mechanisms 3 and 5 cover the same conceptual space ("keep an eye on Y") at
different scales: a session-scoped watch is lightweight and ad-hoc; a worker is
persistent infrastructure.

### Invalid Mechanisms

- **Bare words.** "I'll check back in N minutes" with no tool call this turn.
  The default failure mode. Stop and add a mechanism, or don't make the claim.
- **A scheduler call issued from a subagent / `claude -p` subprocess.** This one
  is sneaky because it *looks* like a valid mechanism. It isn't: a `-p`
  subprocess never enters an idle REPL, so an idle-fired, "durable" cron created
  inside it never fires. Subagents deliver async results through file drop +
  push + tmux injection, never by scheduling work back into the main session.
- **`sleep` loops.** Blocking the session on a long `sleep` is fragile and
  usually disallowed by the harness anyway. It also doesn't survive a restart.

### Plan Statements vs. Commitments

Not every future-tense sentence needs a scheduler. The distinction:

- A **commitment** is a timed or monitoring claim with *no externally visible
  trigger*. "I'll check at 11:30" — nothing surfaces this but the agent's own
  (nonexistent) timer. Needs a mechanism.
- A **plan statement** is bounded by a trigger *both parties can observe*. "I'll
  handle the deploy after the build finishes," "once you're back at your desk,
  we'll go over it." The trigger is visible to the operator and will surface
  naturally — when the build finishes or you return, the conversation resumes.
  No mechanism required.

The rule applies to commitments, not plan statements. Don't bolt a cron onto
every "later"; do bolt one onto every unobservable "later."

### Handling Silent Mechanism Failure

A mechanism can fail quietly — a cron gets evicted, a worker dies, a subprocess
hangs. When you attach a mechanism that *could* fail silently, attach a
failure-notification path in the same turn, usually a push notification with
`FAILED` in the title. Silent failure is the cardinal sin here, for the same
reason it is under standing orders: the operator notices the *absence* of a
promised result and has no idea why.

### The Bright-Line Pre-Send Check

Before ending any turn that contains a future-action claim, the agent verifies:

1. Did I actually call a tool to attach a mechanism *this turn*?
2. Will that mechanism fire on the timeline I promised?
3. If it fails silently, will the operator or I notice?

**Any "no" is a blocker.** Remove the claim or fix the mechanism. This is the
temporal counterpart to the delegation discipline in
[Chapter 3](03-context-management.md): delegation manages work across *space*
(parallel subagents in their own contexts); the async-commitment protocol
manages work across *time* (time-shifted self-delegation). Both fail the same
way — by assuming work happens that nothing was actually arranged to do.

> **Note.** This is hard to enforce by instruction alone, because it asks the
> agent to pattern-match its own output on every turn under context pressure.
> The robust fix is a `Stop` hook that scans the outgoing response for
> timed-commitment phrasing and blocks if no scheduler/worker call appeared in
> the same turn. Treat the instruction-level version as a stopgap until you
> build the hook.

## Pattern 3: The Board

The agent now acts autonomously (Standing Orders) and keeps its promises
(Async-Commitment). The last piece is reporting. When the agent makes an
autonomous decision or hits a non-blocking question, how does it tell you
*without* interrupting you?

Push notifications, texts, and voice are **interrupts** — they pull your
attention now, so you reserve them for things that genuinely can't wait. But
most of what an autonomous agent produces is "you should see this when you get a
chance," not "drop everything." That's what the **Board** is for: an async,
non-interrupting surface the operator reads on their own schedule.

Think of it as the agent's bulletin board. It renders wherever you glance — a
dashboard tile, a status pane, a `--list` command — and accumulates items the
agent has decided, is wondering about, or wants to flag.

### Item Types

| Type | Meaning | Clickable? |
|------|---------|-----------|
| `decision` | An autonomous call the agent made that's worth flagging | No |
| `update` | Status the operator should know when they glance over | No |
| `question` | Something the agent can't resolve alone but that isn't blocking work | Yes → opens a reply |
| `blocked` | The agent has stopped and needs the operator's hands or call | Yes → opens a reply |

`question` and `blocked` are actionable, so they're clickable — clicking opens a
compose surface pre-filled with a reply prefix. `decision` and `update` are
informational.

### A Generic Interface

The Board is just a directory of JSON files — same philosophy as the message
queue in [Chapter 6](06-async-relay.md). One file per item:

```
~/.agent/board/
├── 20260524T091500_decision.json
├── 20260524T101200_question.json
└── 20260524T143000_blocked.json
```

```json
{
  "type": "decision",
  "priority": "normal",
  "text": "Killed the overnight job — loss went NaN at step 5000.",
  "context": "Restarting from the step-4000 checkpoint with lr halved.",
  "source": "agent",
  "created": "2026-05-24T09:15:00",
  "status": "pending"
}
```

A small post script keeps the agent's interface clean:

```bash
#!/bin/bash
# board-post.sh — Post an item to the agent's board.
#
# Usage:
#   board-post.sh --type question "Warm-start the next run, or cold?"
#   board-post.sh --type decision --priority high "Killed job — NaN at step 5000"
#   board-post.sh --type update "Phase 2 complete, metrics look healthy"
#   board-post.sh --type blocked "Need cluster credentials refreshed"

BOARD_DIR="$HOME/.agent/board"
TYPE="update"; PRIORITY="normal"; SOURCE="agent"; CONTEXT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --type)     TYPE="$2"; shift 2 ;;
        --priority) PRIORITY="$2"; shift 2 ;;
        --source)   SOURCE="$2"; shift 2 ;;
        --context)  CONTEXT="$2"; shift 2 ;;
        *)          TEXT="$1"; shift ;;
    esac
done

if [ -z "$TEXT" ]; then
    echo "Usage: board-post.sh --type {question|decision|update|blocked} [--priority normal|high] [--context '...'] 'message'" >&2
    exit 1
fi

mkdir -p "$BOARD_DIR"
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%S%z)
FILENAME=$(date +%Y%m%dT%H%M%S)_${TYPE}.json

python3 -c "
import json, sys
item = {
    'type': sys.argv[1], 'priority': sys.argv[2], 'text': sys.argv[3],
    'context': sys.argv[4] or None, 'source': sys.argv[5],
    'created': sys.argv[6], 'status': 'pending',
}
item = {k: v for k, v in item.items() if v is not None}
print(json.dumps(item, indent=2))
" "$TYPE" "$PRIORITY" "$TEXT" "$CONTEXT" "$SOURCE" "$TIMESTAMP" > "$BOARD_DIR/$FILENAME"

echo "$BOARD_DIR/$FILENAME"
```

The `--priority high` flag paints the row visually (e.g. red) — reserve it for
items that are genuinely time-sensitive but still not interrupt-worthy. If
something truly can't wait, it's not a Board item; it's a push (see
[Chapter 6](06-async-relay.md)).

### When to Post

- A **decision** you made autonomously that's worth surfacing (you killed a job,
  picked branch A over B, restarted a worker).
- An **update** the operator should know at a glance (a phase completed, metrics
  shifted, a long run is healthy at the halfway mark).
- A **question** you can't resolve without the operator but that isn't blocking
  your other work.
- A **blocked** item — you've stopped and need their hands or their call.

### When NOT to Post

- **Active conversation context.** If you're mid-conversation, that's just your
  response stream — don't also post it to the Board.
- **Routine task completion.** Git commits, finished trivial tasks, normal
  pulse — those are already captured elsewhere. The Board is for things that
  warrant a second look, not a changelog.
- **Anything time-sensitive.** Use a push or a text. The Board is explicitly the
  *non-urgent* channel; putting urgent items there buries them.
- **Anything a peer agent already posted.** In a multi-agent setup
  ([Chapter 12](12-multi-agent.md)), don't double-post. One item, one source.

### Cadence

The Board's value is signal density. **A board of 1–3 active items has signal; a
board of 12 is noise.** If you find the agent posting freely, tighten the
"when not to post" rules — most of what an agent wants to say belongs in the
conversation or a log, not on a surface meant for the operator's scarce glances.

Items don't need active cleanup when they self-resolve (a watcher confirms the
thing, status changes). Let the operator dismiss them. But if the Board is
filling up faster than it's being read, that's a tuning problem, not a feature
request.

## Common Mistakes & Trade-offs

**The agent edits its own authority.** The single most important boundary in
this chapter. If the model can write the standing-orders state file, the entire
tier system is theater. Make the file operator-writable only — by instruction,
and ideally by a `PreToolUse` hook or filesystem ownership that rejects
agent writes. Authority is one-way: operator → agent, never back.

**Scope creep beyond the grant.** A standing order for "data-pipeline" is *not*
authority to refactor the deploy script because you happened to notice it's
ugly. When an action isn't covered by the active order, the agent states it and
waits — it does not extrapolate. "I have authority on X, and Y is adjacent to X"
is exactly the inference that gets datasets deleted.

**Treating elevated as a free pass.** `elevated` loosens the tier mapping by one
step; it does not touch the universal constraints. No modifier unlocks deleting
results, force-pushing `main`, or emailing an outsider without confirmation.
Those are floors.

**Bare promises.** The most common async bug, and the most invisible. "I'll
check back" with nothing attached is a guaranteed broken promise, because the
agent doesn't run between your messages. Run the bright-line check before every
turn that contains a future-tense action claim.

**Silent failure.** Across all three patterns, the worst outcome is the agent
quietly not doing something it implied it would. A failed standing-order step, a
dead scheduler, a hung subprocess — all of them must surface a `FAILED` push.
The operator noticing a missing result with no explanation erodes trust faster
than an honest "I couldn't do this."

**The Board becoming a changelog.** A Board that lists every commit and every
finished task trains the operator to ignore it, which defeats the purpose. Keep
it to decisions, real questions, blocks, and glance-worthy status. When in
doubt, leave it off the Board.

**Over-gating your own orders.** When you let the agent help draft standing
orders, it will reach for restrictions. Apply the counter-gating check — name a
blocked-case *and* a permitted-case for each restriction — to keep the orders
permissive enough to actually be useful. The system exists to unlock action;
don't let the drafting process quietly re-lock it.

---

These three patterns compose into a single posture: the agent acts inside an
explicit, revocable grant; it never promises work it hasn't arranged a mechanism
to do; and it reports through the lowest-friction channel that fits the urgency.
The operator stays in control of *what's allowed* and *what interrupts them*,
while the agent gets to be genuinely useful in the gaps — which is the entire
reason to run a persistent agent in the first place.
