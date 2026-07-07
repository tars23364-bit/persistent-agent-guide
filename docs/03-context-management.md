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

Long before compaction fires, context quality degrades. This is the single most important fact for threshold design, and the research on it is specific enough to build on:

**Degradation tracks absolute token count, not fraction of the window.** Chroma's "Context Rot" study (Hong, Troynikov, Huber; July 2025) measured 18 frontier models and found every one degrades monotonically as input grows, with the steepest drops in the 100K–500K range -- and no model held uniform accuracy across its full advertised window. The advertised window is a hardware spec; the *reliably usable* window is a smaller, model-specific number.

**Effective context length is the operative concept.** NoLiMa (Adobe Research, ICML 2025; arXiv 2502.05167) strips lexical overlap between question and evidence, forcing the model to actually reason rather than string-match. It defines **effective context length** as the longest context at which a model keeps ≥85% of its short-context score. On the 2024-era models it tested, 10 of 12 fell below *half* their baseline by 32K tokens. Current models hold far deeper (see below) -- use NoLiMa for the concept and method, not its numbers. The number you care about is not "% full"; it is "how deep can *this* model reason reliably."

**Distractors accelerate rot.** Both studies found degradation is worse when the context contains plausible-but-wrong material and when the query has low semantic similarity to the evidence. A real agent session -- mixed memory injections, tool output, code, half-relevant file reads -- is exactly that distractor-heavy regime. Coding agents were flagged as the worst case. Expect a working agent to rot *earlier* than any clean benchmark suggests.

**The knee moves with the model.** Anthropic's system-card GraphWalks figures (multi-hop graph traversal over long context, F1) for the 2026 flagships:

| GraphWalks subset | Opus 4.8 | Mythos/Fable 5 |
|---|---|---|
| BFS @ 256K | 85.9 | 91.1 |
| BFS @ 1M | 68.1 | ~79–80 |
| Parents @ 1M | 83.3 | 97.5 |

Two things to read off this table. First, the knee moved *outward* -- these models hold 86–91 F1 at 256K where a 2024 model was already collapsing at 32K. Second, the curve did **not** flatten: there is still a meaningful drop between 256K and 1M, and the drop differs *by model* (Opus loses ~18 points, Fable ~11). A stronger model genuinely runs deeper before rotting -- which means a threshold tuned for one model is mis-tuned for another *even at the same percentage fill*.

**Safety rots too.** The sleeper finding for autonomous agents: "When Refusals Fail: Unstable Safety Mechanisms in Long-Context LLM Agents" (arXiv 2512.02445) shows that guardrail and refusal behavior -- not just retrieval -- degrades as context fills, sometimes sharply past ~100K of padding, and for some models past 50K even on benign tasks. Every authority rule, safety constraint, and permission boundary that lives *in context* gets less reliable exactly when the session is longest. See "The Safety-Rot Floor" below.

One partial natural mitigation: newer flagships are better calibrated -- a model that says "I can't find it" instead of confidently hallucinating the needle is safer under rot. The rot is still there; its danger is reduced. Don't confuse better calibration with immunity.

## The Threshold System

Rather than letting compaction happen and dealing with the aftermath, use a threshold-based warning system that gives the agent progressively stronger signals to wrap up.

**Thresholds are absolute token counts, not percentages of the window.** An earlier version of this guide taught percentage-based thresholds for model portability. The research above says that is backwards: rot tracks absolute depth, so a percentage threshold silently *moves your warning point* every time the window size changes. 30% of a 1M window is 300K tokens -- deep into the degradation zone; 30% of a 200K window is 60K tokens -- nowhere near it. Same number, wildly different meaning.

The correct mental model:

- Pick absolute token marks calibrated to where the knee is for the models you run (defaults below).
- On a large-window model, the marks land mid-window and fire as designed.
- On a small-window model whose whole window sits *below* the first mark, the marks simply never fire -- the agent uses the full window and only warns near the compaction ceiling. That is intended behavior, not a bug: a 200K window ends before absolute rot territory begins.

### Starting values

Calibrated to a ~1M-token reference window:

| Threshold | Absolute mark | On a 1M window | Behavior |
|-----------|--------------|----------------|----------|
| Awareness | ~250K tokens | 25% | Subtle one-liner. Watch large reads, prefer delegation. |
| Wrap-up | ~350K tokens | 35% | Wind down, delegate remaining work, update task.lock. |
| Critical | ~450K tokens | 45% | No new tasks. Handoff, then restart. |

Why these are conservative relative to the benchmark table (which shows 86–91 F1 at 256K): GraphWalks is clean retrieval over synthetic structure. Your agent's context is memory injections plus tool dumps plus code plus distractors -- the regime both studies say rots earliest. Start conservative; tune outward from evidence (see Calibration below), not from the advertised window.

**Model-aware adjustment:** the GraphWalks deltas are the concrete justification for per-model thresholds. A Fable/Mythos-class session (91 @ 256K, ~80 @ 1M) can defensibly run its marks 100K+ deeper than an Opus-class session (86 @ 256K, 68 @ 1M). If your harness routes between models, keep a small per-model threshold table in the hook rather than one set of marks. If that's more machinery than you want on day one, one set of conservative marks is fine -- the per-model table is a tuning refinement, not a prerequisite.

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

The statusline hook receives context window data from Claude Code on every turn and writes it to disk. Claude Code reports percentages, so the bridge derives absolute tokens from the model's window size -- keep a small model→window map in the hook:

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

# Model → window size (tokens). Update when you add models.
case "$MODEL" in
    *[Ff]able*|*[Mm]ythos*)  WINDOW=1000000 ;;
    *[Oo]pus*)               WINDOW=1000000 ;;   # check your loaded variant; some are 200K
    *[Ss]onnet*)             WINDOW=1000000 ;;
    *)                       WINDOW=200000  ;;   # conservative default
esac

USED_TOKENS=$(echo "$USED_PCT * $WINDOW / 100" | bc | cut -d. -f1)

# Account for the auto-compaction buffer
AUTOCOMPACT_BUFFER="10.0"
FREE_UNTIL_COMPACT=$(echo "$REMAINING_PCT - $AUTOCOMPACT_BUFFER" | bc -l)

cat > "$STATE_FILE" <<EOF
{
  "model": "$MODEL",
  "window_tokens": $WINDOW,
  "used_tokens": $USED_TOKENS,
  "used_pct": $USED_PCT,
  "remaining_pct": $REMAINING_PCT,
  "free_until_compact_pct": $FREE_UNTIL_COMPACT,
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

# Display in tmux statusline
USED_INT=$(printf "%.0f" "$USED_PCT")
FREE_INT=$(printf "%.0f" "$FREE_UNTIL_COMPACT")
echo "${MODEL} ${USED_TOKENS} tok (${USED_INT}%, ${FREE_INT}% to compact)"
```

The bridge file is the key architectural decision. It decouples context monitoring from context warnings. The statusline hook writes it; the threshold hook reads it; other scripts (health checks, alerting) can read it too. No process needs to query the agent or Claude Code directly.

### Threshold Definitions

The threshold hook compares **absolute used tokens** against the marks, and separately warns near the compaction ceiling (which *is* percentage-relative, because compaction is):

```bash
#!/bin/bash
# context-threshold.sh -- UserPromptSubmit hook

# Thresholds (absolute tokens, ~1M reference; see Context Management chapter)
THRESHOLD_AWARE=250000
THRESHOLD_WRAPUP=350000
THRESHOLD_CRITICAL=450000
CEILING_PCT=80          # near-compaction warning, any window size

STATE_FILE="$HOME/.agent/state/context.json"

# Exit silently if bridge file missing or stale (>5 min)
[ ! -f "$STATE_FILE" ] && exit 0

if [ "$(uname)" = "Darwin" ]; then
    FILE_AGE=$(( $(date +%s) - $(stat -f %m "$STATE_FILE") ))
else
    FILE_AGE=$(( $(date +%s) - $(stat -c %Y "$STATE_FILE") ))
fi
[ "$FILE_AGE" -gt 300 ] && exit 0

USED_TOK=$(jq -r '.used_tokens // 0' "$STATE_FILE")
USED_PCT=$(jq -r '.used_pct // 0' "$STATE_FILE" | xargs printf "%.0f")
USED_K=$((USED_TOK / 1000))

if [ "$USED_TOK" -ge "$THRESHOLD_CRITICAL" ]; then
    echo "[CONTEXT CRITICAL: ${USED_K}K tokens] Prepare handoff and restart immediately. Do not start new tasks."
elif [ "$USED_TOK" -ge "$THRESHOLD_WRAPUP" ]; then
    echo "[CONTEXT HIGH: ${USED_K}K tokens] Start wrapping up. Delegate remaining work. Prepare for restart."
elif [ "$USED_TOK" -ge "$THRESHOLD_AWARE" ]; then
    echo "[ctx: ${USED_K}K]"
elif [ "$USED_PCT" -ge "$CEILING_PCT" ]; then
    # Small-window model: absolute marks never fired, but compaction is near.
    echo "[CONTEXT CEILING: ${USED_PCT}% of window] Compaction approaching. Wrap up and restart cleanly."
fi
```

### Threshold Behaviors

Define what the agent should do at each level. Put this in `.claude/rules/protocols.md`, and keep the numbers in sync with the hook -- the hook is ground truth:

```markdown
## Context Thresholds

**Below the awareness mark -- Green zone.** No warnings. Work normally.

**Awareness (~250K)** (`[ctx: NNNK]`). Be mindful of large file reads
and verbose tool output. Start preferring subagents for self-contained
tasks. Prefer `grep` and targeted reads over full file reads.

**Wrap-up (~350K)** (`[CONTEXT HIGH: ...]`). Warning escalates. Start
winding down the current task. Delegate remaining work to subagents.
Update task.lock with the next concrete step -- this is what the next
session resumes from.

**Critical (~450K)** (`[CONTEXT CRITICAL: ...]`). Hard warning. Do not
start new tasks. Do not take authority-gated or irreversible actions
on in-context judgment alone (see The Safety-Rot Floor). Update
task.lock, write pulse entry, and restart. Every token counts.

**Ceiling (80% of any window)** (`[CONTEXT CEILING: ...]`). Fires on
small-window models where the absolute marks never trigger. Same
behavior as Critical.
```

## The Safety-Rot Floor

Retrieval rot costs you accuracy. Safety rot costs you the properties that make autonomy safe to grant in the first place.

The "When Refusals Fail" result means the rules an agent carries in context -- authority tiers, "never push to main," "confirm before external sends," tool-permission discipline -- measurably weaken as the context fills. The agent doesn't feel this happening; degraded rule-following looks like normal operation from the inside. Design for it structurally:

1. **Hard floor for irreversible actions.** Past the critical mark, the agent should not take irreversible, authority-gated, or externally-visible actions on its own judgment. Not because it *will* misbehave -- because its in-context guardrails can no longer be trusted at spec. Defer to the post-restart session; the task lock carries the intent across.
2. **Guardrails belong in the harness, not just in context.** Permission systems, deny-lists, and hooks that gate tool calls (e.g., a PreToolUse hook that blocks `git push` to main) do not rot -- they run outside the model. Every safety property you care about should exist at least once outside the context window. In-context rules are the UX; out-of-context enforcement is the guarantee.
3. **Refresh rules before going deep.** A restart re-injects rules at full fidelity at position zero. If a long autonomous run is planned, restarting *before* it starts is cheap insurance -- the run begins with fresh guardrails instead of hour-six ones.

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

### Delegation Increases with Context Depth

| Context depth (1M ref) | Delegation Posture |
|--------------|-------------------|
| < 250K | Delegate self-contained tasks when convenient |
| 250K–350K | Prefer delegation for anything file-heavy |
| 350K–450K | Delegate everything except conversation and decisions |
| 450K+ | Do not start new tasks. Delegate only wrap-up work. |

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

The key discipline: **update STEP before restarting**. The wrap-up zone behavior should include "update task.lock with the next concrete step" -- that step is what carries the work forward. A stale or incomplete lock is worse than no lock, because it implies continuity that doesn't exist.

Delete the lock when the task is finished or abandoned. The lock is the instruction to future sessions, not background context.

## Practical Guidelines

### Reading Files

- **Green zone**: read files normally
- **Awareness mark+**: prefer `grep` and targeted reads (specific line ranges) over full file reads
- **Wrap-up mark+**: delegate file-heavy tasks to subagents; use grep, head, and tail inline
- **Never** read a file larger than ~5K tokens in the critical zone

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

- **Heavy file work**: expect to hit the awareness mark in 30-60 minutes. Delegate early.
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
# Color thresholds for the tmux statusline (keyed to the absolute marks)
if [ "$USED_TOKENS" -lt 250000 ]; then
    CTX_COLOR="$GREEN"
elif [ "$USED_TOKENS" -lt 350000 ]; then
    CTX_COLOR="$YELLOW"
else
    CTX_COLOR="$RED"
fi

# Progress bar (10 segments, against the window)
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
- **Calibration log**: append per-session fill data for the watch loop below

The bridge file is informational only -- never modify it from inside a session. It is written by the statusline hook and read by everything else.

## Calibration: Thresholds Are a Hypothesis

The numbers in this chapter will be wrong for you eventually -- possibly already. They are calibrated to 2026 flagship models from benchmark data that under-represents real agentic load. Models change; your workload changes; the knee moves. Treat every threshold as a hypothesis under continuous falsification, not a constant.

The minimum viable calibration discipline:

1. **Log fill against quality.** Whenever the agent visibly degrades -- forgets an earlier decision, re-reads a file it already read, contradicts established session facts, misses an in-context instruction -- note the token depth it happened at. A one-line append to a log file is enough (`date, model, used_tokens, symptom`). Derailments cluster; after a few weeks the cluster tells you where *your* knee is, on *your* workload.
2. **Re-measure on every model swap.** A new model means new curves -- in 2026 a single generation moved GraphWalks BFS @ 1M from 40.3 to 68.1. Never carry thresholds across a model change unexamined. Check the new model's long-context evals (system cards report them), then confirm against your own derailment log within the first week.
3. **Tune from evidence, in one direction at a time.**

| Threshold | Move it deeper if... | Move it shallower if... |
|-----------|---------------------|------------------------|
| Awareness | No degradation symptoms logged below the wrap-up mark for weeks | Derailments logged below it |
| Wrap-up | Sessions consistently wrap cleanly well past it | Handoffs are rushed or task.lock updates get sloppy |
| Critical | Handoff + restart reliably fits in the remaining room | Compaction ever fires before a clean restart |

4. **Keep the safety floor stricter than the quality marks.** Retrieval quality degrades visibly; rule-following degrades silently. Whatever your derailment log says about quality, do not move the irreversible-action floor deeper on quality evidence alone.

If you do only one thing from this section: write the derailment log line into your wrap-up routine. Calibration without measurement is just vibes with extra steps.

## Sources

- Chroma Research, *Context Rot: How Increasing Input Tokens Impacts LLM Performance* (Hong, Troynikov, Huber; July 2025) -- the absolute-length thesis, 18 models.
- *NoLiMa: Long-Context Evaluation Beyond Literal Matching* (Adobe Research, ICML 2025; arXiv 2502.05167) -- effective context length, ≥85%-of-base definition.
- *When Refusals Fail: Unstable Safety Mechanisms in Long-Context LLM Agents* (arXiv 2512.02445, Dec 2025) -- safety-rot in long-context agents.
- Anthropic system cards (Opus 4.8; Fable 5 / Mythos 5, 2026) -- GraphWalks long-context figures cited above.

## Summary

Context management is not optional for a persistent agent. The strategies here -- absolute, model-aware threshold warnings; delegation bias; bridge files; the safety-rot floor; compaction recovery; a standing calibration loop -- exist because context loss is the most common failure mode, and because the window a model advertises is not the window it can reliably use. An agent that manages its context well can run indefinitely, restarting cleanly and picking up where it left off. An agent that ignores context ends up compacted, confused, and starting over.
