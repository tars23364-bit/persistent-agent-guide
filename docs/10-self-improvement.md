# Self-Improvement

A persistent agent makes the same kinds of mistakes a human does — it forgets
lessons, repeats errors, and develops blind spots. The difference is that an
agent can build systematic infrastructure to catch and correct these patterns.
This chapter covers three interlocking mechanisms: a learnings log for capturing
corrections in real time, a reflection cycle for daily self-assessment, and a
promotion pipeline that graduates recurring patterns into permanent rules.

## The Problem

Every Claude Code session starts fresh. The agent doesn't remember that it got
corrected last Tuesday for using the wrong flag on a build command, or that a
particular API returns paginated results that need concatenation. Memory systems
(covered in [Chapter 2](02-memory.md)) help with factual recall, but they don't
address *behavioral* patterns — the kind of thing a human learns through
repeated experience.

Without a self-improvement system, you'll find yourself correcting the same
mistakes across sessions. The agent will apologize each time, store nothing, and
repeat it tomorrow.

## Architecture

```
Session (real-time)
  │
  ├─→ ~/.agent/learnings/LEARNINGS.md    # Corrections, knowledge gaps
  ├─→ ~/.agent/learnings/ERRORS.md       # Tool failures worth learning from
  └─→ ~/.agent/learnings/FEATURE_REQUESTS.md  # Capability gaps
        │
        ▼
Reflection (daily, scheduled)
  │
  ├─→ ~/.agent/reflections/YYYY-MM-DD.md  # Structured self-assessment
  ├─→ Memory commands (graph DB writes)    # Durable insights
  └─→ Promotion candidates                # Patterns ready for rules
        │
        ▼
Promotion (operator-approved)
  │
  └─→ .claude/rules/learned-patterns.md   # Permanent behavioral rules
```

## The Learnings Log

### What to Log

During sessions, the agent writes to structured log files when it notices
specific triggers:

**LEARNINGS.md** — Corrections and knowledge gaps:
- The operator corrects the agent ("no", "actually", "that's wrong")
- A previous approach or stored fact turns out to be incorrect
- The operator provides information the agent didn't know
- The agent discovers a cleaner or faster way to do something

**ERRORS.md** — Tool and command failures:
- Significant non-zero exit codes (not routine grep misses)
- Stack traces, MCP server failures, timeouts
- Only errors worth learning from — skip noise

**FEATURE_REQUESTS.md** — Capability gaps:
- The operator asks for something the agent can't do yet
- A workflow would clearly benefit from a tool that doesn't exist

### What NOT to Log

Equally important — don't log noise:

- Routine grep/ssh non-zero exits (these are normal control flow)
- Things already captured by the memory system or git history
- Transient issues that self-resolved
- Anything already in the promoted patterns file

The learnings log should be a curated signal, not an audit trail.

### Entry Format

Each entry follows a structured format that supports deduplication and
promotion tracking:

```markdown
## [LRN-20260415-001] build-system

**Logged**: 2026-04-15T14:32:00-07:00
**Priority**: medium
**Status**: pending

### What happened
Used `make build` instead of `make build-release` for the deployment
artifact. The debug build passed tests but failed in production due to
missing optimizations. Operator caught it during deploy review.

### Action
Always use `make build-release` for deployment artifacts. `make build`
is for local development only.

### Metadata
- Source: correction
- Pattern-Key: build.release-vs-debug
- Recurrence-Count: 1
- First-Seen: 2026-04-15
- Last-Seen: 2026-04-15
- Related: none
```

### Key Fields Explained

**Pattern-Key**: A stable, human-readable dedupe key. When the same mistake
happens again, the agent increments `Recurrence-Count` and updates `Last-Seen`
instead of creating a new entry. Use dotted notation:
`domain.specific-pattern`.

**Source**: Where the learning came from:
- `correction` — operator explicitly corrected the agent
- `error` — tool or command failure
- `discovery` — agent found something on its own
- `reflection` — identified during daily reflection

**Status**: Tracks the entry's lifecycle:
- `pending` — logged, not yet acted on
- `resolved` — the underlying issue was fixed
- `promoted` — graduated to a permanent rule

### Implementation: When the Agent Logs

The logging happens inline during the session. When the agent detects a
correction trigger (operator says "no, use X instead"), it appends to the
appropriate file:

```python
from datetime import datetime
from pathlib import Path

LEARNINGS_DIR = Path.home() / ".agent" / "learnings"

def log_learning(category: str, pattern_key: str, what_happened: str,
                 action: str, source: str = "correction",
                 priority: str = "medium"):
    """Append a learning entry to LEARNINGS.md."""
    LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = LEARNINGS_DIR / "LEARNINGS.md"

    now = datetime.now().astimezone()
    date_str = now.strftime("%Y%m%d")
    time_str = now.isoformat()

    # Check for existing entry with same pattern key
    existing = filepath.read_text() if filepath.exists() else ""
    if f"Pattern-Key: {pattern_key}" in existing:
        # Increment recurrence instead of creating new entry
        update_recurrence(filepath, pattern_key, time_str)
        return

    # Generate entry ID
    count = existing.count(f"[LRN-{date_str}-") + 1
    entry_id = f"LRN-{date_str}-{count:03d}"

    entry = f"""
## [{entry_id}] {category}

**Logged**: {time_str}
**Priority**: {priority}
**Status**: pending

### What happened
{what_happened}

### Action
{action}

### Metadata
- Source: {source}
- Pattern-Key: {pattern_key}
- Recurrence-Count: 1
- First-Seen: {now.strftime('%Y-%m-%d')}
- Last-Seen: {now.strftime('%Y-%m-%d')}
- Related: none
"""
    with open(filepath, "a") as f:
        f.write(entry)
```

In practice, the agent doesn't call a Python function — it appends to the
markdown file directly using its file editing tools. The format above is what
the entries look like, not necessarily how they're written.

### Recurrence Tracking

When the same pattern appears again, update the existing entry rather than
creating a duplicate:

1. Find the entry by `Pattern-Key`.
2. Increment `Recurrence-Count`.
3. Update `Last-Seen` to today.
4. Optionally append a note to the "What happened" section with the new
   context.

This is how entries become promotion candidates — through observed repetition,
not subjective importance.

## The Reflection Cycle

Daily reflection is the agent's structured self-assessment. It runs as a
scheduled job (via launchd or cron), typically late in the day after the
session summarizer has run.

### What It Does

1. Gathers the day's activity logs and session transcripts.
2. Feeds them through a cheaper/faster model (e.g., Sonnet) with a structured
   prompt.
3. Writes a reflection file to `~/.agent/reflections/YYYY-MM-DD.md`.
4. Executes any memory commands the reflection produces.
5. Marks the date as reflected to prevent duplicate runs.

### The Reflection Script

The script follows this structure:

```bash
#!/usr/bin/env bash
set -uo pipefail

TARGET_DATE="${1:-$(date '+%Y-%m-%d')}"
DELIVERED_FILE="$HOME/.agent/reflect-delivered"

# 1. Dedup — skip if already reflected today
if [[ -f "$DELIVERED_FILE" ]] && \
   [[ "$(cat "$DELIVERED_FILE")" == "$TARGET_DATE" ]]; then
    exit 0
fi

# 2. Gather inputs into a temp file
#    - Learnings files (LEARNINGS.md, ERRORS.md, FEATURE_REQUESTS.md)
#    - Today's log entries (grep for TARGET_DATE in each .log file)
#    - Session transcripts modified today
#    Cap each source (10KB per learnings file, 20KB per transcript)
#    Cap total input (200KB) to keep the model happy

# 3. Skip if insufficient activity (< 500 bytes)

# 4. Run through a cheaper model with the reflection prompt
claude -p --model sonnet \
    --system-prompt "$(cat reflect-prompt.md)" \
    < "$tmpinput" > "$tmpinput.out"

# 5. Write reflection to ~/.agent/reflections/YYYY-MM-DD.md

# 6. Execute memory commands from ```memory-cli blocks
#    The reflection model outputs memory commands for durable insights.
#    Parse them out and run them.

# 7. Mark delivered
echo "$TARGET_DATE" > "$DELIVERED_FILE"
```

Key implementation details:

- **Input capping.** Each learnings file is capped at 10KB, each transcript at
  20KB, and the total input at 200KB. This prevents the reflection model from
  choking on a particularly active day.
- **Deduplication.** A delivered-date file prevents duplicate runs when both
  the scheduler and the shutdown sequence trigger reflection.
- **Memory bridge.** The reflection output can contain fenced `memory-cli` blocks
  with memory commands. The script parses and executes them, bridging daily
  insights into the long-term memory graph.

### The Reflection Prompt

The reflection model receives the day's activity and produces a structured
assessment. This is the system prompt:

```markdown
You are a reflection engine. You receive a day's activity logs and session
transcripts and produce a structured self-assessment. This is NOT a session
summary — it's the meta layer: patterns, calibration, and gaps.

Write a reflection with these sections:

## Session Review
- What tasks were performed today
- What went well
- What failed or required correction
- Patterns in the types of work

## Persona Calibration
- Was communication style consistent and appropriate?
- Were there moments where the persona got in the way?
- Were there missed opportunities to take the lead?

## Knowledge Gaps
- Topics where uncertain answers were given
- Areas where more information was needed
- Skills or references that should be expanded

## Learnings Review
- Scan LEARNINGS.md and ERRORS.md entries
- Flag entries with Recurrence-Count >= 3, across 2+ sessions,
  within 30 days — these are promotion candidates
- For each candidate, draft a short rule for learned-patterns.md
- Flag stale entries (> 14 days, no recurrence) for cleanup

## Action Items
- Concrete next steps from today's work
- Deferred or incomplete items
- Promotion candidates (if any)

## Memory Commands
If durable insights emerged, output memory commands in a fenced block
tagged `memory-cli`. Only store conclusions, not raw facts.

Keep it grounded and factual. If it was a light day, say so briefly.
```

### Why a Separate Model

Reflection uses a faster, cheaper model (Sonnet) for two reasons:

1. **Cost.** Reflection runs daily. Using your primary model for a task that's
   mostly structured analysis would be expensive.
2. **Perspective.** A different model sometimes catches things the primary model
   normalizes. It's not a perfect external reviewer, but it provides some
   distance.

The reflection script runs outside the agent session — it's a background job
that writes files the agent reads on the next startup.

## Pattern Promotion

The highest-value output of the self-improvement system: recurring learnings
that graduate into permanent behavioral rules.

### The Promotion Threshold

A learning becomes a promotion candidate when it meets all three criteria:

1. **Recurrence >= 3** — The same pattern has been observed at least three
   times.
2. **Across 2+ sessions** — It's not a single bad session; it's a recurring
   issue.
3. **Within 30 days** — It's a current pattern, not ancient history.

These thresholds are deliberately conservative. You want to promote patterns
that are genuinely persistent, not one-off issues that happened to cluster.

### The Promotion Flow

```
LEARNINGS.md entry
  │  (recurrence >= 3, 2+ sessions, 30 days)
  ▼
Reflection flags it as candidate
  │
  ▼
Agent proposes rule to operator
  │  "I keep making this mistake. Proposed rule: ..."
  ▼
Operator approves / modifies / rejects
  │
  ▼
Rule added to .claude/rules/learned-patterns.md
  │
  ▼
Entry status → "promoted"
```

### The Learned Patterns File

Promoted rules live in `.claude/rules/learned-patterns.md`. This file is loaded
every session (Claude Code auto-loads everything in `.claude/rules/`), so
promoted patterns become permanent behavior.

```markdown
# Learned Patterns

Promoted from learnings after meeting threshold
(3+ recurrences, 2+ sessions, 30 days).
Each rule approved by operator before promotion.

## Build artifacts
Always use `make build-release` for deployment. `make build` produces
debug artifacts that fail in production.
— Promoted: 2026-04-20, from LRN-20260415-001

## API pagination
The monitoring API paginates at 100 results. Always check `next_page`
in responses and concatenate. Don't assume a single request returns
everything.
— Promoted: 2026-04-22, from LRN-20260408-003

## SSH tunnel ordering
Start the SSH tunnel before launching the database client. If the client
starts first, it caches the failed connection and won't retry even after
the tunnel comes up.
— Promoted: 2026-05-01, from LRN-20260418-007
```

### Why Operator Approval

The agent proposes promotions; it doesn't execute them autonomously. Two
reasons:

1. **Accuracy.** The agent might generalize incorrectly from specific
   instances. The operator can narrow or broaden the rule.
2. **Trust.** Rules that govern agent behavior should have human oversight.
   Self-modifying behavior without approval is a safety concern.

This is a deliberate friction point. The cost (a few seconds of operator
review) is negligible compared to the risk of a bad rule becoming permanent.

## Connecting the Pieces

Here's how the three mechanisms interact: On Monday, the agent uses the wrong
build flag and the operator corrects it — logged to LEARNINGS.md, recurrence 1.
The same mistake recurs Wednesday (recurrence 2) and Thursday (recurrence 3,
across 3 sessions). Thursday night's reflection flags it as a promotion
candidate and drafts a rule. Friday, the agent presents it, the operator
approves, and it's added to `learned-patterns.md`. Next Monday, the rule loads
at startup and the mistake doesn't happen.

## Stale Entry Cleanup

Not every learning becomes a pattern. The reflection cycle also flags stale
entries for cleanup:

- **Stale**: Pending status, older than 14 days, recurrence count of 1.
- **Action**: Suggest deletion or resolution during the next reflection.

This prevents the learnings files from growing unbounded. A learning that
happened once two weeks ago and never recurred is probably not a pattern —
it's just something that happened.

## Practical Tips

### Keep Learnings Files Readable

These files are read by both the reflection model and (occasionally) the
agent itself. Use clear language, not shorthand. Future sessions need to
understand what happened without the original conversation context.

### Don't Over-Log

The biggest risk is logging everything and drowning in noise. The "When NOT
to Log" list is as important as the "When to Log" list. If you find the
learnings files growing past a few dozen entries without promotions, you're
probably logging too much.

### Separate the Summarizer from the Reflector

Session summarization ("what happened today") and reflection ("what patterns
emerged") are different tasks. The summarizer runs first and produces a
condensed record. The reflector consumes that record plus the raw logs and
produces the meta-analysis. Don't combine them — they serve different
purposes and benefit from different prompts.

### Schedule Reflection After Activity

Run the reflection late in the day, after the session summarizer. If you run
it too early, you miss the afternoon's activity. If you run it during a
session, it burns context and blocks the conversation. A launchd plist that
fires at 11:45 PM works well (see [Chapter 4](04-os-integration.md) for
launchd patterns).

### Use the Dedup Date for Idempotency

The reflection script writes the target date to a `reflect-delivered` file
and checks it before running. This means:

- If launchd fires at 11:45 PM and the agent also triggers reflection during
  a shutdown at 11:50 PM, it only runs once.
- If you want to re-run reflection for a date, delete the delivered file.

### Memory Commands Are the Bridge

The reflection's `memory-cli` block is how daily insights enter the long-term
memory graph. Without this bridge, reflection output would be a write-only
archive — useful for debugging but not for improving behavior.

Keep memory commands focused on conclusions: "The monitoring API paginates at
100 results" — not "Today I learned that the monitoring API paginates at 100
results when I was trying to fetch alerts and only got the first page."

## Measuring Effectiveness

How do you know if the self-improvement system is working?

- **Promotion rate**: If you're promoting 1-2 patterns per month, the system
  is finding real recurring issues.
- **Recurrence after promotion**: If a promoted pattern still recurs, the
  rule is too vague or the agent isn't loading it correctly.
- **Learnings file size**: If it grows without bound, you're logging too
  much. If it's always empty, you're not logging enough.
- **Operator corrections**: Track (informally) whether the operator has to
  repeat corrections. A working self-improvement system should reduce
  repetition over weeks.

None of these need formal dashboards. Periodic spot-checks during the
reflection review are sufficient.
