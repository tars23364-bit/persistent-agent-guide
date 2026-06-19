# Context Management

The context window is the fundamental constraint of a persistent agent. Everything the agent knows, everything it has read, every tool result it has received -- all of it lives in a fixed-size buffer that, once full, triggers compaction. Compaction is lossy. Managing context is managing the agent's ability to do its job.

## The Constraint

Claude Code's context window is large (up to 1M tokens on some models) but finite. Every interaction consumes context:

- Each user message: tokens in
- Each agent response: tokens in
- Each tool call + result: tokens in (and tool results can be enormous)
- Each file read: tokens in
- System prompts, rules, injected context: tokens in

None of this goes away during a session. The context window is append-only until compaction fires.

## What Compaction Does

When context usage hits the auto-compaction threshold (configurable, typically ~90%), Claude Code summarizes the entire conversation into a compressed form. This:

- **Preserves the gist** of what happened
- **Loses granularity** -- specific details, exact values, mid-task state
- **Loses nuance** -- the difference between "we decided X after considering Y and Z" and "X was decided"
- **Breaks in-progress work** -- if the agent was mid-task, compaction can lose the thread

Compaction is not a graceful degradation. It is a hard reset of conversational detail. The agent after compaction is working from a summary, not from the actual conversation. This matters most for:

- Multi-step tasks where intermediate state is critical
- Debugging sessions where exact error messages matter
- Decisions with nuanced rationale

## Context Rot

Even before compaction, context quality degrades with length. Research shows this is non-linear -- the model's ability to retrieve and use information from earlier in the context drops faster as the window fills.

The degradation is non-linear -- earlier context becomes increasingly unreliable as the window fills, well before compaction fires. The effective context window is smaller than the nominal one. Plan accordingly.

## The Threshold System

Rather than letting compaction happen and dealing with the aftermath, use a threshold-based warning system that gives the agent progressively stronger signals to wrap up. All thresholds are **percentage-based**, not absolute token counts -- the window size is model-dependent and changes when you switch models.

### Architecture

Three components work together:

1. **Statusline bridge** -- a hook that runs on every turn, reads context window metrics from Claude Code, and writes them to a bridge file
2. **Bridge file** -- a JSON file at `~/.agent/state/context.json` that any process can read
3. **Threshold hook** -- a `UserPromptSubmit` hook that reads the bridge file and injects warnings into the agent's prompt automatically, every turn

```
Claude Code statusline → statusline-bridge.sh → context.json → context-threshold.sh → agent prompt
```

The hook is the ground truth for threshold values. If you change the hook, update the corresponding prose in your rules file to match -- they can drift otherwise.

### The Bridge File

The statusline hook receives context window data from Claude Code on every turn and writes it to disk:

```bash
#!/bin/bash
# statusline-bridge.sh -- writes context state for other hooks

STATE_DIR="$HOME/.agent/state"
STATE_FILE="$STATE_DIR/context.json"
mkdir -p "$STATE_DIR"

input=$(cat)

MODEL=$(echo "$input" | jq -r '.model.display_name // "unknown"')
USED_PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
REMAINING_PCT=$(echo "$input" | jq -r '.context_window.remaining_percentage // 100')

# Account for the auto-compaction buffer
AUTOCOMPACT_BUFFER="10.0"
FREE_UNTIL_COMPACT=$(echo "$REMAINING_PCT - $AUTOCOMPACT_BUFFER" | bc -l)

cat > "$STATE_FILE" <<EOF
{
  "model": "$MODEL",
  "used_pct": $USED_PCT,
  "remaining_pct": $REMAINING_PCT,
  "free_until_compact_pct": $FREE_UNTIL_COMPACT,
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

# Display in tmux statusline
USED_INT=$(printf "%.0f" "$USED_PCT")
FREE_INT=$(printf "%.0f" "$FREE_UNTIL_COMPACT")
echo "${MODEL} ${USED_INT}% used (${FREE_INT}% to compact)"
```

The bridge file is the key architectural decision. It decouples context monitoring from context warnings. The statusline hook writes it; the threshold hook reads it; other scripts (health checks, alerting) can read it too. No process needs to query the agent or Claude Code directly.

### Threshold Definitions

Define thresholds as named variables at the top of the hook for easy tuning:

```bash
#!/bin/bash
# context-threshold.sh -- UserPromptSubmit hook

# Thresholds (percentage of context used)
THRESHOLD_AWARE=30      # subtle one-liner
THRESHOLD_WRAPUP=45     # wrap-up advisory
THRESHOLD_CRITICAL=55   # hard restart directive

STATE_FILE="$HOME/.agent/state/context.json"

# Exit silently if bridge file missing or stale (>5 min)
[ ! -f "$STATE_FILE" ] && exit 0

if [ "$(uname)" = "Darwin" ]; then
    FILE_AGE=$(( $(date +%s) - $(stat -f %m "$STATE_FILE") ))
else
    FILE_AGE=$(( $(date +%s) - $(stat -c %Y "$STATE_FILE") ))
fi
[ "$FILE_AGE" -gt 300 ] && exit 0

USED_INT=$(jq -r '.used_pct // 0' "$STATE_FILE" | xargs printf "%.0f")

if [ "$USED_INT" -ge "$THRESHOLD_CRITICAL" ]; then
    echo "[CONTEXT CRITICAL: ${USED_INT}%] Prepare handoff and restart immediately. Do not start new tasks."
elif [ "$USED_INT" -ge "$THRESHOLD_WRAPUP" ]; then
    echo "[CONTEXT HIGH: ${USED_INT}%] Start wrapping up. Delegate remaining work. Prepare for restart."
elif [ "$USED_INT" -ge "$THRESHOLD_AWARE" ]; then
    echo "[ctx: ${USED_INT}%]"
fi
```

### Threshold Behaviors

Define what the agent should do at each level. Put this in `.claude/rules/protocols.md`:

```markdown
## Context Thresholds

**Below 30% -- Green zone.** No warnings. Work normally.

**30-44% -- Awareness zone** (`[ctx: NN%]`). Be mindful of large file
reads and verbose tool output. Start preferring subagents for
self-contained tasks. Prefer `grep` and targeted reads over full file
reads.

**45-54% -- Wrap-up zone** (`[CONTEXT HIGH: ...]`). Warning escalates.
Start winding down the current task. Delegate remaining work to
subagents. Update task.lock with the next concrete step -- this is
what the next session resumes from.

**55%+ -- Critical zone** (`[CONTEXT CRITICAL: ...]`). Hard warning.
Do not start new tasks. Update task.lock, write pulse entry, and
restart. Every token counts.
```

### Why These Numbers Are Conservative

You might think 55% is an aggressive critical threshold when the window does not compact until ~90%. There are two reasons for this:

1. **Context rot.** Quality degrades well before compaction. The model is already losing fidelity on earlier content well below the compaction threshold.

2. **Buffer for wrap-up.** Writing a handoff, updating task.lock, and doing a clean restart takes tokens. If you wait until 80% to start wrapping up, you might hit compaction during the wrap-up process itself.

Start conservative. Tune up based on observed quality, not theoretical capacity.

## Delegation Bias

The single most effective context management strategy is not reading less or writing less -- it is delegating work to subagents.

### Why Delegate

Subagents (spawned via Claude Code's agent tool) run in their own context windows. Their work does not consume the main session's context. They do the work, return a tight summary, and the main session stays clean.

Two reasons to delegate earlier than pure efficiency math suggests:

**1. Presence over throughput.** The main session is a working relationship with the operator. Research, data gathering, and file grinding pull attention and context away from the conversation. The main session should stay conversational -- subagents do the legwork.

**2. Context protection.** Every tool result and file dump in the main window accelerates context rot. Subagents work in their own context and return compressed summaries. Less noise in the main window means longer retention of what matters.

### The Delegation Guideline

```markdown
## Delegation Bias

If a task is self-contained enough that a subagent *could* handle it,
default to delegating -- even in the crossover zone where doing it
inline might be slightly more token-efficient.

Tasks touching 5+ independent files: strongly prefer parallel
subagents over sequential inline processing.

Reserve the main session for judgment, synthesis, and conversation.
```

This is a bias, not a hard rule. Some tasks are faster inline. But when in doubt, delegate.

### What to Delegate

Good candidates for subagent delegation:
- File searches across the codebase
- Reading and summarizing long files
- Running test suites and reporting results
- Data gathering from multiple sources
- Refactoring tasks with clear specs
- Health checks and diagnostics

Keep in the main session:
- Decisions that need operator input
- Conversations about approach or architecture
- Tasks that require back-and-forth judgment
- Quick one-line commands

### Delegation Increases with Context Usage

| Context Usage | Delegation Posture |
|--------------|-------------------|
| < 30% | Delegate self-contained tasks when convenient |
| 30-44% | Prefer delegation for anything file-heavy |
| 45-54% | Delegate everything except conversation and decisions |
| 55%+ | Do not start new tasks. Delegate only wrap-up work. |

## Compaction Recovery

Despite the warning system, compaction sometimes happens -- a long debugging session, an unexpectedly large tool result, or the agent ignoring warnings. Plan for it.

### The Pre-Compaction Hook

A `PreCompact` hook fires right before compaction occurs. Use it to drop a flag file:

```bash
#!/bin/bash
# precompact-flag.sh -- PreCompact hook

FLAG_FILE="$HOME/.agent/compacted.json"
input=$(cat)
SESSION_ID=$(echo "$input" | jq -r '.session_id // "unknown"')

USED_PCT="unknown"
if [ -f "$HOME/.agent/state/context.json" ]; then
    USED_PCT=$(jq -r '.used_pct // "unknown"' "$HOME/.agent/state/context.json")
fi

cat > "$FLAG_FILE" <<EOF
{
  "event": "compaction",
  "session_id": "$SESSION_ID",
  "context_pct_at_compaction": $USED_PCT,
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
```

### The Recovery Injection

A `SessionStart` hook (or an additional matcher in your startup hook) checks for the flag file and injects a recovery directive:

```bash
#!/bin/bash
# compact-recovery-inject.sh -- SessionStart hook

FLAG_FILE="$HOME/.agent/compacted.json"
[ ! -f "$FLAG_FILE" ] && exit 0

SESSION_ID=$(jq -r '.session_id // "unknown"' "$FLAG_FILE")
TIMESTAMP=$(jq -r '.timestamp // "unknown"' "$FLAG_FILE")
CTX_PCT=$(jq -r '.context_pct_at_compaction // "unknown"' "$FLAG_FILE")

cat <<EOF
[COMPACTION RECOVERY -- HANDLE BEFORE ANYTHING ELSE]
Context was auto-compacted at ${TIMESTAMP} (was at ${CTX_PCT}% usage).
Session ID: ${SESSION_ID}

REQUIRED before resuming work:
1. Spawn a recovery subagent on the compacted session transcript
   - Commit any insights to graph memory
   - Write a retroactive pulse entry
   - Return a task recovery brief
2. Read the brief, orient yourself
3. Delete the flag file
4. Resume work
EOF
```

The recovery subagent reads the full session transcript (which is on disk in the Claude Code project directory), extracts anything the compaction summary missed, and returns a brief to the main session. This is not perfect -- you still lose nuance -- but it catches the most important losses.

## Task Lock: Continuity Across Restarts

Context management is not just about reading less -- it is also about surviving the restarts that context pressure forces. A task lock is a small state file that carries task continuity across session boundaries.

For any substantive task that spans more than a few turns, write a `task.lock` file at the start and delete it at completion:

```
TASK: Short task name
STEP: Next concrete action (what to DO, not what you did)
CONTEXT: Pointers to relevant docs (paths, sections)
TIMESTAMP: ISO-8601
```

The `SessionStart` hook reads the lock file on every session start. If a non-stale lock exists (typically: under 24 hours old), it injects a resume directive -- the next session starts working immediately from where the previous one left off, not from zero orientation.

The key discipline: **update STEP before restarting**. The wrap-up zone behavior (45-54%) should include "update task.lock with the next concrete step" -- that step is what carries the work forward. A stale or incomplete lock is worse than no lock, because it implies continuity that doesn't exist.

Delete the lock when the task is finished or abandoned. The lock is the instruction to future sessions, not background context.

## Practical Guidelines

### Reading Files

- **Below 30%**: read files normally
- **30%+**: prefer `grep` and targeted reads (specific line ranges) over full file reads
- **45%+**: delegate file-heavy tasks to subagents; use grep, head, and tail inline
- **Never** read a file larger than ~5K tokens in the critical zone (55%+)

Note: each file read is capped at 2,000 lines and truncation is silent -- you won't be warned when a file is cut off. For files you know are large, read in chunks using offset and limit parameters. Tool results over ~50K characters are also silently truncated; if a search returns suspiciously few results, narrow scope rather than assuming completeness.

### Tool Output

Some tools produce enormous output. Watch for:
- `git log` without `--oneline` or `-n` limit
- `ls -la` on large directories
- Test suites that print every test case
- API responses with deeply nested JSON

Prefer flags that limit output. `-n 20`, `--oneline`, `| head -50` are your friends.

### Session Length Planning

A session that will involve heavy file work (refactoring, debugging, research) will consume context faster than a conversational session. Plan accordingly:

- **Heavy file work**: expect to hit 30% in 30-60 minutes. Delegate early.
- **Conversational**: can run for hours before hitting thresholds
- **Mixed**: the most common pattern. Delegate file work, keep conversation in the main session.

### The Session Elapsed Nudge

A `UserPromptSubmit` hook can track session elapsed time and nudge the agent toward reflection after extended periods:

```bash
#!/bin/bash
# session-elapsed.sh -- UserPromptSubmit hook

NUDGE_HOURS=8
STATE_DIR="$HOME/.agent/state"
SESSION_START_FILE="${STATE_DIR}/session-start"

mkdir -p "$STATE_DIR"

if [ ! -f "$SESSION_START_FILE" ]; then
    date +%s > "$SESSION_START_FILE"
    exit 0
fi

SESSION_START=$(cat "$SESSION_START_FILE")
NOW=$(date +%s)
ELAPSED_HOURS=$(( (NOW - SESSION_START) / 3600 ))

[ "$ELAPSED_HOURS" -lt "$NUDGE_HOURS" ] && exit 0

echo "[SESSION ${ELAPSED_HOURS}h] Consider running a reflection and reviewing pending learnings."
```

This is a soft nudge, not a hard limit. Long sessions are fine if context is managed well. But after 8 hours of continuous operation, a reflection and potential restart keeps quality high.

## Monitoring and Visibility

### The Statusline

The statusline bridge hook already displays context usage in the tmux status bar. Add color coding for quick visual reference:

```bash
# Color thresholds for the tmux statusline
if [ "$USED_INT" -lt 50 ]; then
    CTX_COLOR="$GREEN"
elif [ "$USED_INT" -lt 70 ]; then
    CTX_COLOR="$YELLOW"
else
    CTX_COLOR="$RED"
fi

# Progress bar (10 segments)
BAR_WIDTH=10
FILLED=$((USED_INT * BAR_WIDTH / 100))
EMPTY=$((BAR_WIDTH - FILLED))
BAR=$(printf "%${FILLED}s" | tr ' ' '#')
BAR="${BAR}$(printf "%${EMPTY}s" | tr ' ' '-')"

echo "${MODEL} ${CTX_COLOR}${BAR} ${USED_INT}%${RESET}"
```

This gives both the agent (via prompt injection) and the operator (via tmux) visibility into context health.

### Bridge File Consumers

The bridge file at `~/.agent/state/context.json` can be read by anything:

- **Threshold hook**: injects warnings into agent prompts
- **Health check scripts**: include context state in system health reports
- **Alert system**: trigger notifications if context is critically high and no restart has happened
- **External dashboards**: if you build monitoring UI, read the bridge file

The bridge file is informational only -- never modify it from inside a session. It is written by the statusline hook and read by everything else.

## Tuning

Start with these thresholds and adjust based on observation. All values are percentage-based -- they apply regardless of nominal window size, which varies by model:

| Threshold | Starting Value | Adjust Up If... | Adjust Down If... |
|-----------|---------------|-----------------|-------------------|
| Awareness | 30% | Quality is fine at 40%, you want less noise | Agent makes errors referencing earlier context |
| Wrap-up | 45% | You consistently wrap up cleanly at 50% | Compaction has happened despite warnings |
| Critical | 55% | Buffer math works out (enough room for handoff + task.lock) | Compaction keeps happening |
| Compaction | 90% | Never -- this is a safety net, not a target | -- |

The autocompact percentage is set via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`. Setting it to 90 gives you 10% buffer between the critical threshold and actual compaction. This buffer exists so the agent can write its handoff and restart cleanly.

## Summary

Context management is not optional for a persistent agent. The strategies here -- threshold warnings, delegation bias, bridge files, compaction recovery -- exist because context loss is the most common failure mode. An agent that manages its context well can run indefinitely, restarting cleanly and picking up where it left off. An agent that ignores context ends up compacted, confused, and starting over.
