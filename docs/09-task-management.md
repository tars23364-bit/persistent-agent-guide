# Task Management

A persistent agent that runs across sessions needs to track commitments. Not
everything belongs in memory or git issues — some things are promises the agent
made to the operator, with due dates, priorities, and external visibility. This
chapter covers a file-based task system that bridges the agent's internal state
with the operator's existing tools.

## Why File-Based Tasks

You might wonder why not just use GitHub Issues, Jira, or Apple Reminders
directly as the task store. Three reasons:

1. **Context richness.** A task file can hold arbitrary markdown — commands to
   run, partial outputs, links to relevant files, decision history. External
   systems have structured fields that don't accommodate this.
2. **Offline availability.** Files on disk are always accessible. No API calls,
   no auth tokens, no rate limits. The agent can read its task list during
   startup in milliseconds.
3. **Source of truth clarity.** When two systems both claim to be authoritative,
   you get drift. Pick one source of truth (files) and treat everything else as
   a display layer.

The trade-off is that you need reconciliation with external systems — but that
turns out to be simpler than bidirectional sync.

## Architecture

```
~/.agent/tasks/           # Source of truth — one file per task
    deploy-monitoring.md
    upgrade-dependencies.md
    fix-serial-timeout.md

~/.agent/state/task.lock  # Mid-task resume state (one active task at a time)

scripts/task-index.py     # Builds startup index + reconciles with external system

External system           # Display layer (Apple Reminders, Todoist, etc.)
    (operator sees tasks on phone/watch)
```

The data flow is:

1. Agent creates a task file and optionally mirrors it to the external system.
2. On each session startup, `task-index.py` reads all task files, builds a slim
   index, and checks the external system for discrepancies.
3. The index (plus any active task lock) is injected into the session as startup
   context.
4. The agent works from the index during the session, updating files as needed.

## Task File Format

Each task is a markdown file with YAML frontmatter followed by freeform
markdown body -- context, steps, commands, notes:

```markdown
---
title: Fix serial port timeout on reconnect
status: active
priority: high
due: 2026-04-15
reminder_id: ABC123-DEF456
created: 2026-04-10
---

## Context

The USB-serial adapter drops connection after ~4 hours. Current workaround
is manual `screen` reconnect. Need to add auto-retry logic to the monitor
daemon.

## Steps

1. Add exponential backoff to serial_monitor.py
2. Test with forced disconnect (unplug/replug)
3. Verify log output captures reconnection events

## Notes

- 2026-04-10: Initial investigation. Timeout is in the pyserial layer.
- 2026-04-12: Backoff logic drafted, needs testing on hardware.
```

### Field Reference

| Field | Values | Notes |
|-------|--------|-------|
| `title` | Free text | Short, descriptive — this shows in the startup index |
| `status` | `backlog`, `pending`, `active`, `blocked`, `completed`, `cancelled` | See lifecycle below |
| `priority` | `high`, `medium`, `low`, `none` | Affects sort order in index |
| `due` | `YYYY-MM-DD` or `none` | Optional — only set when there's a real deadline |
| `reminder_id` | External system ID or `none` | Links to the display layer |
| `created` | `YYYY-MM-DD` | When the task was created |

### Filenames

Use slugs: lowercase, hyphenated, descriptive.

```
fix-serial-timeout.md       # Good
upgrade-dependencies.md     # Good
task-47.md                  # Bad — meaningless
TODO.md                     # Bad — not a slug
```

The filename is the stable identifier. Don't rename task files after creation —
other systems may reference the slug.

## Status Lifecycle

```
backlog ──→ pending ──→ active ──→ completed
                │          │
                │          └──→ blocked ──→ active
                │
                └──→ cancelled
```

- **backlog** -- No timeline. "Build this someday." Stays out of the priority
  sort until promoted.
- **pending** -- Planned. Has a date or a trigger condition. Shows in the startup
  index.
- **active** -- Currently being worked on. Only a few tasks should be active at
  once.
- **blocked** -- Waiting on something external (hardware delivery, API access,
  another person). Include what it's blocked on in the file body.
- **completed** -- Done. Keep the file for one reflection cycle, then archive or
  delete.
- **cancelled** -- Dropped. Same retention as completed.

### When to Create Tasks

Not everything is a task. Use this heuristic:

- **Create a task** for commitments that span multiple sessions, have due dates,
  or the operator would want to see on their phone or watch.
- **Don't create tasks** for single-session work, trivial fixes, or things
  tracked elsewhere (like git issues for code work).

The goal is a task list that's useful, not comprehensive. If it'll be done
this session and doesn't need external visibility, it's not a task. It's
just work. Use the task lock (below) for mid-session state instead.

## The Startup Index

Loading every task file into context at startup would be wasteful. Instead, a
script reads the files and produces a slim index — one line per active task,
sorted by urgency.

### Index Script

```python
#!/usr/bin/env python3
"""task-index.py — Build task index for startup injection."""

import re
from datetime import date, datetime
from pathlib import Path

TASKS_DIR = Path.home() / ".agent" / "tasks"


def parse_task_file(path: Path) -> dict | None:
    """Parse a task markdown file with YAML-ish frontmatter."""
    try:
        text = path.read_text()
    except Exception:
        return None

    match = re.match(r"^---\n(.+?)\n---", text, re.DOTALL)
    if not match:
        return None

    meta = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()

    if not meta.get("title"):
        return None

    meta["filename"] = path.stem
    meta["path"] = str(path)
    return meta


def load_tasks() -> list[dict]:
    """Load all task files."""
    if not TASKS_DIR.exists():
        return []
    tasks = []
    for f in sorted(TASKS_DIR.glob("*.md")):
        task = parse_task_file(f)
        if task:
            tasks.append(task)
    return tasks


def format_index(tasks: list[dict]) -> str:
    """Format slim task index for startup injection."""
    today = date.today().isoformat()
    priority_order = {"high": 0, "medium": 1, "low": 2, "none": 3}

    def sort_key(t):
        status = t.get("status", "backlog")
        if status in ("completed", "cancelled"):
            return (3, 9, "")
        due = t.get("due", "none")
        has_due = due not in ("none", "")
        pri = priority_order.get(t.get("priority", "none"), 3)
        return (0 if has_due else 1, pri, due or "9999")

    active = [
        t for t in tasks if t.get("status") not in ("completed", "cancelled")
    ]
    active.sort(key=sort_key)

    if not active:
        return "No active tasks."

    lines = []
    for t in active:
        parts = []
        status = t.get("status", "pending")
        priority = t.get("priority", "none")
        due = t.get("due", "none")

        if status == "backlog":
            parts.append("[backlog]")
        elif priority in ("high", "medium"):
            parts.append(f"[{priority}]")

        parts.append(t["title"])

        if due not in ("none", ""):
            due_date = due[:10]
            if due_date == today:
                parts.append("(due today)")
            elif due_date < today:
                parts.append(f"(overdue: {due_date})")
            else:
                try:
                    d = datetime.strptime(due_date, "%Y-%m-%d").date()
                    delta = (d - date.today()).days
                    if delta == 1:
                        parts.append("(due tomorrow)")
                    elif delta <= 7:
                        parts.append(f"(due in {delta}d)")
                    else:
                        parts.append(f"(due {due_date})")
                except ValueError:
                    parts.append(f"(due {due})")

        lines.append("- " + " ".join(parts))

    return "\n".join(lines)
```

### What the Agent Sees

At session startup, the index is injected as context. It looks like this:

```
**Tasks:**
- [high] Fix serial port timeout on reconnect (due in 3d)
- [medium] Upgrade monitoring dependencies (due 2026-04-20)
- Deploy staging environment
- [backlog] Evaluate alternative TTS providers
```

This is enough for the agent to know what's on the plate without loading full
task files. When it needs detail on a specific task, it reads the individual
file.

### Calling from the Startup Hook

In your `session-startup.py` (or equivalent), call the index script and include
its output in the injected context. If there is an active task lock, inject it
too -- the agent needs both the broad task picture and the specific resume point:

```python
import subprocess
from pathlib import Path

def get_task_index():
    result = subprocess.run(
        ["python3", "scripts/task-index.py"],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None

def get_task_lock():
    lock = Path.home() / ".agent" / "state" / "task.lock"
    if lock.exists():
        return lock.read_text().strip()
    return None

# In your startup context builder:
task_index = get_task_index()
if task_index:
    context_parts.append(f"**Tasks:**\n{task_index}")

task_lock = get_task_lock()
if task_lock:
    context_parts.append(f"## RESUME TASK\nStart working on STEP immediately.\n\n{task_lock}")
```

## Reconciliation with External Systems

The most useful pattern is **file-authoritative, one-way sync with
reconciliation**. The agent's task files are the source of truth. The external
system (Apple Reminders, Todoist, a shared calendar) is a display layer for
the operator -- they see tasks on their phone or watch, and can mark them done
or delete them there.

### Why Not Bidirectional Sync

Bidirectional sync between an AI agent's files and an external task manager
sounds elegant but creates nightmares:

- Conflict resolution requires heuristics that break in edge cases.
- The external system has its own data model (subtasks, tags, recurrence) that
  doesn't map cleanly to your file format.
- Debugging sync issues burns more time than the sync saves.

Instead: the agent writes to files and pushes to the external system. The
operator can *read* tasks in the external system and *complete* or *delete* them
there. The reconciliation step catches these actions and asks the agent what to
do.

### Reconciliation Logic

On each startup, compare task files against the external system:

```python
def reconcile(tasks, reminders):
    """Compare task files against external system, return discrepancies."""
    issues = []
    reminder_by_id = {r["id"]: r for r in reminders}
    tracked_ids = set()

    for task in tasks:
        rid = task.get("reminder_id", "none")
        if rid == "none" or not rid:
            continue
        tracked_ids.add(rid)

        reminder = reminder_by_id.get(rid)
        if not reminder:
            # Operator deleted the reminder but task is still active
            if task.get("status") not in ("completed", "cancelled"):
                issues.append(
                    f'"{task["title"]}" — reminder deleted, still active in tasks?'
                )
        elif reminder.get("completed") and task.get("status") not in (
            "completed", "cancelled"
        ):
            # Operator marked it done in the external system
            issues.append(
                f'"{task["title"]}" — marked done externally, close the task?'
            )

    # External items with no matching task file
    for reminder in reminders:
        if reminder["id"] not in tracked_ids and not reminder.get("completed"):
            issues.append(
                f'"{reminder["title"]}" — exists externally, no task file. Track it?'
            )

    return issues
```

### Key Principle: Never Auto-Resolve

Reconciliation surfaces discrepancies as questions, never as actions. The agent
should present them to the operator:

```
[reconcile] "Fix serial timeout" — marked done in Reminders. Should I close it?
[reconcile] "Buy new calipers" — exists in Reminders, no task file. Want me to track it?
```

Why? Because the operator might have completed the reminder for a reason the
agent doesn't know about, or they might have created a reminder that doesn't
need agent tracking. Auto-resolving would either lose tasks or create noise.

This applies symmetrically in both directions:
- Reminder deleted but task file still active → ask, don't auto-cancel
- Reminder marked complete but task file active → ask, don't auto-complete
- Reminder exists with no task file → offer to track, don't assume

## Creating Tasks

When the agent creates a new task:

1. Write the task file to `~/.agent/tasks/<slug>.md`.
2. If the task has a due date or the operator would benefit from seeing it on
   their phone, create a matching entry in the external system.
3. Store the external system's ID in the `reminder_id` field.

The agent writes the task file (markdown with frontmatter), then optionally
creates a matching entry in the external system and stores the returned ID in
the `reminder_id` field.

## Completing Tasks

When completing a task, update both sides:

1. Set `status: completed` in the task file's frontmatter.
2. Mark the external system entry as done (via CLI/API).
3. The file stays on disk until the next reflection cycle archives it.

## The Task Lock: Crash Recovery for In-Flight Work

Task files track *commitments* — multi-session work with due dates and external
visibility. They are deliberately not the right tool for "I am three steps into a
refactor right now and the session might restart." That mid-task state is too
granular and too short-lived for a task file, but losing it to a restart is
exactly the kind of thing that wastes a fresh session re-deriving where it was.

The **task lock** fills that gap. It is a single file that holds the *next
concrete action* for whatever substantive task is currently in flight. Write it
when you start such a task, update it as you progress, and delete it when you
finish. If a session restarts for any reason — context hygiene, a crash, a
compaction event — the startup hook reads the lock and the next session resumes
immediately instead of asking "what were we doing?"

```
~/.agent/state/task.lock
```

### Format

Simple key-value, one per line. The fields are chosen so a cold session can act
on them without any other context:

```
TASK: Refactor the startup hook to consolidate context gathering
STEP: Move the attunement query into the main hook; test a cold start
CONTEXT: hooks/session-startup.py; the old attunement script is scripts/attune.py
TIMESTAMP: 2026-03-15T14:30-07:00
```

- **TASK** — the task name, one line.
- **STEP** — the *next concrete action*: what to **do** next, not what was just
  done. This is the most important field. "Move the attunement query into the
  main hook" is actionable; "worked on the hook" is not.
- **CONTEXT** — pointers (file paths, doc sections, job IDs) the resuming session
  needs. Pointers, not prose — the detail lives in the files it names.
- **TIMESTAMP** — ISO-8601, so the startup hook can judge staleness.

### How the Resume Works

The `SessionStart` hook (see [Context Management](03-context-management.md))
checks for the lock and, if it exists and is recent (say, under 24 hours old),
injects it as a **directive to resume**, not as background information:

```python
def read_task_lock():
    lock = Path.home() / ".agent" / "state" / "task.lock"
    if not lock.exists():
        return None
    fields = dict(
        line.split(":", 1) for line in lock.read_text().splitlines() if ":" in line
    )
    ts = fields.get("TIMESTAMP", "").strip()
    # Stale locks (>24h) are likely abandoned — surface, don't auto-resume.
    if ts and (datetime.now(tz) - parse(ts)) > timedelta(hours=24):
        return f"## Stale Task Lock (review)\n{lock.read_text()}"
    return f"## RESUME TASK\nStart working on STEP immediately.\n\n{lock.read_text()}"
```

The crucial behavior is on the agent's side: **the lock is the instruction.** On
startup with a fresh lock, the agent starts working on `STEP` immediately — it
does not present the lock and ask the operator what to do. The operator already
authorized this work in the previous session; re-confirming it on every restart
defeats the entire point of seamless recovery.

### Lock vs. Task File

They answer different questions and coexist:

| | Task file | Task lock |
|---|-----------|-----------|
| Question answered | *What are my commitments?* | *Where am I in the current task?* |
| Lifespan | Days to weeks | Minutes to hours |
| Count | Many, one per commitment | One, the active task |
| Granularity | Whole task + history | The single next step |
| On restart | Loaded as the task index | Loaded as a resume directive |

A task lock points *at* a task file when the in-flight work belongs to a tracked
commitment (`CONTEXT: ~/.agent/tasks/startup-refactor.md`), but plenty of
substantive work — a focused refactor, a one-session investigation — gets a lock
without ever warranting a task file.

### When to Use It

- **Write a lock** for any task that spans more than a few turns -- anything
  you'd mention in a handoff note. The cost is one small file; the payoff is a
  restart that loses nothing.
- **Don't bother** for single-shot work: a quick question, a one-line fix, a
  message reply. The lock is overhead there.
- **Update `STEP` as you go.** A lock whose `STEP` reflects what you finished an
  hour ago resumes you in the wrong place -- worse than no lock, because it reads
  as current.
- **Delete it on completion.** A leftover lock from yesterday's finished task
  will try to resume work that's already done. (The staleness check is a
  backstop, not a substitute for cleaning up.)
- **The lock is the instruction.** On startup with a fresh lock, start working
  on `STEP` immediately. Do not present the lock and ask what to do. The
  operator authorized this work in the previous session; re-confirming on every
  restart defeats the point.

## Practical Tips

### Don't Over-Track

The biggest risk with a task system is creating tasks for everything. This
leads to a cluttered index that the agent (and operator) learns to ignore.

Rule of thumb: if it'll be done this session and doesn't need external
visibility, it's not a task. It's just work.

### Keep Task Files Self-Contained

A task file should have enough context that the agent can pick it up cold — no
dependency on conversation history or session state. Include relevant commands,
file paths, and decision rationale in the body.

### Archive Aggressively

Completed and cancelled tasks accumulate. Set up a retention policy:

```bash
# In your reflection or cleanup script:
ARCHIVE_DIR="$HOME/.agent/tasks/archive"
mkdir -p "$ARCHIVE_DIR"

for f in "$HOME/.agent/tasks"/*.md; do
    status=$(grep "^status:" "$f" | head -1 | awk '{print $2}')
    if [[ "$status" == "completed" || "$status" == "cancelled" ]]; then
        mv "$f" "$ARCHIVE_DIR/"
    fi
done
```

### External System Choice

Any system with a CLI or API works. The pattern is the same regardless of
backend:

- **Apple Reminders** — Good if the operator is in the Apple ecosystem. Native
  CLI tools or AppleScript bridges work.
- **Todoist** — Good REST API, cross-platform.
- **Linear / GitHub Issues** — Good if tasks are code-related.
- **Google Tasks** — Accessible via Google API but less featured.

The key is that the external system is a *display layer*, not the source of
truth. Don't try to use its data model as your own.

### Handling Overdue Tasks

The index script marks overdue tasks explicitly. But the agent should also have
a rule about what to do with them:

```markdown
# In your rules file:
When a task is overdue:
- Mention it early in the session, not buried in context.
- Ask the operator: still relevant? New deadline? Drop it?
- Don't silently carry overdue tasks session after session.
```

## Example: Full Startup Output

Here's what the combined task index and reconciliation output looks like when
injected into a session:

```
**Tasks:**
- [high] Fix serial port timeout on reconnect (due tomorrow)
- [medium] Upgrade monitoring dependencies (due in 5d)
- Deploy staging environment
- [backlog] Evaluate alternative TTS providers

**Reconciliation:**
[reconcile] "Order replacement bearings" — marked done in Reminders. Should I close it?
[reconcile] "Schedule dentist appointment" — exists in Reminders, no task file. Want me to track it?
```

The agent can address the reconciliation questions naturally in conversation,
and the operator gets a clear picture of what's active without opening the task
files.
