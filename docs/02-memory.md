# Memory Architecture

A persistent agent needs memory that outlives individual sessions. Claude Code has no built-in long-term memory -- every restart wipes the slate. The memory architecture described here solves that with three layers, each optimized for a different access pattern.

## The Problem

Without memory, a persistent agent:

- Forgets operator preferences after every restart
- Cannot reference past decisions or their rationale
- Repeats mistakes it already learned from
- Loses track of ongoing projects and context
- Treats every session as the first session

You can partly solve this with a big `memory.md` file that the agent reads on startup. That works for a few weeks. Then the file grows to thousands of lines, startup burns tokens loading it, and half the content is stale. The three-layer architecture handles this more gracefully.

## Layer 1: File-Based State

The simplest and most reliable layer. Plain files on disk, read and written by hooks and scripts. No database, no query language -- just files.

### What Goes Here

State that is:
- **Binary or near-binary** -- on/off toggles, flags, timestamps
- **Read by non-agent processes** -- hooks, daemons, cron jobs
- **Needed deterministically** -- not "maybe relevant," but "always needed"

```
~/.agent/
├── state/
│   ├── context.json         # Current context window usage
│   ├── attunement.md        # Operator awareness snapshot
│   └── session-start        # Timestamp of current session start
├── voice-response           # "on" or "off"
├── wake-word                # "on" or "off"
├── handoff.md               # Task context for next session
├── today-pulse.md           # Session summaries for today
└── brief-delivered           # Date string of last brief delivery
```

### Design Principles

**Plain text over structured data.** A file containing `off` is readable by bash, Python, and the agent itself. A JSON blob requires parsing. Use the simplest format that works.

**One concern per file.** `voice-response` is a single toggle. It does not share a file with wake word state. This means any process can read or write one piece of state without touching others.

**Hooks are first-class consumers.** The file-based layer exists largely so that shell hooks -- which fire on every prompt or response -- can read state without calling the agent or a database. A bash hook that checks `[ -f ~/.agent/tts-suppress ]` costs nothing. A hook that queries a database costs time and complexity.

### The Handoff File

The handoff file (`~/.agent/handoff.md`) is the bridge between sessions. Before any restart, the agent writes:

- What it was working on
- What decisions were made
- What is left to do
- Why it is restarting

The next session reads this file during startup, orients itself, and deletes it. This is the single most important file in the state layer -- without it, restarts lose all task context.

```markdown
# Handoff — 2026-03-15T14:30

## What I Was Doing
Refactoring the startup hook to consolidate context gathering.

## Decisions Made
- Moved attunement query into startup hook (was separate script)
- Keeping pulse injection as-is -- works well

## What's Next
- Test the consolidated hook with a cold start
- Update the rules file to remove the old attunement protocol

## Why Restarting
Context at 52%, quality starting to degrade on long file reads.
```

### The Pulse File

The pulse file (`~/.agent/today-pulse.md`) carries conversational texture across sessions within the same day. After substantive sessions, the agent appends a one-line summary:

```
2026-03-15T09:20 | productive -- startup refactor complete, tested cold and warm paths
2026-03-15T11:45 | debugging -- TTS daemon not responding after reboot, fixed launchd plist
2026-03-15T14:30 | exploratory -- designing new context threshold system
```

On the next session start, the startup hook injects today's pulse entries. The agent sees what happened earlier and picks up the thread naturally. The file resets automatically on the first session of a new day.

This is deliberately lightweight. It is not a log -- it is a mood ring for the day's work.

## Layer 2: Graph Memory

File-based state handles deterministic, always-needed information. But a persistent agent also accumulates knowledge that is *sometimes* relevant -- operator preferences, past decisions, system facts, insights from debugging sessions. This is where graph memory comes in.

### What Graph Memory Provides

- **Semantic recall** -- query by meaning, not exact file path
- **Importance scoring** -- not all facts are equally worth remembering
- **Decay** -- old, unreinforced memories fade naturally
- **Entity linking** -- facts connect to the things they are about
- **Category filtering** -- preferences, decisions, facts, insights, context

### Architecture

The graph memory system is a CLI that the agent calls:

```bash
# Store a new memory
agent-memory remember "Operator prefers dark mode in all terminal apps" \
  --category preference \
  --importance 4 \
  --entities "operator,terminal,preferences" \
  --source agent

# Recall relevant memories
agent-memory recall "terminal display preferences" --limit 5
```

The recall command returns results ranked by relevance, importance, and recency. The agent calls it before responding to messages that depend on past context.

### What to Store

| Category | Importance | Examples |
|----------|-----------|----------|
| Preferences | 4-5 | Communication style, tool choices, scheduling habits |
| Decisions | 4-5 | "We decided to use launchd instead of cron" |
| Facts | 3-4 | System configuration, hardware specs, account details |
| Insights | 3-4 | "USB devices need 15s to enumerate after reboot" |
| Context | 2 | Background information, project descriptions |

### What NOT to Store

- **Secrets** -- no passwords, API keys, tokens. Ever.
- **Transient state** -- things that change hourly belong in file-based state
- **Derivable information** -- if it is in git history or a config file, don't duplicate it
- **Session noise** -- not every conversation turn is worth remembering

### Recall Protocol

The agent should query graph memory *before* responding when the message involves:

- System knowledge or past configuration
- Previous decisions and their rationale
- Operator preferences or work patterns
- Prior work on the current topic

Skip the recall when:
- It is a direct follow-up to something already in the conversation context
- The question has no dependency on past work
- The answer is in a file that can be read directly

```markdown
# Example rule in .claude/rules/protocols.md

## Memory Recall

Before responding to messages that involve past decisions, system
knowledge, or operator preferences: run `agent-memory recall "<query>"`
first. Craft keyword-rich queries -- don't pass the raw user message.

Skip when: direct follow-up already in context, or no dependency
on past work.
```

### Remember Protocol

After responding, the agent evaluates whether new durable knowledge was created:

```markdown
## Memory Storage

After responding, evaluate whether new durable knowledge was created.
If yes:
- Operator preferences, decisions, corrections -> importance 4-5
- System facts, configuration -> importance 3-4
- Background context -> importance 2
- NEVER store secrets, passwords, API keys, or transient noise
```

This is a judgment call the agent makes on every turn. Most turns produce nothing worth storing. The ones that do -- a correction, a decision, a discovered fact -- get committed to graph memory for future sessions.

## Layer 3: Context Injection

The first two layers store information. The third layer delivers it. Context injection is the process of selecting, compressing, and injecting relevant information into the agent's context window at session startup.

### The Startup Hook

A `SessionStart` hook gathers context from all sources and injects it as `additionalContext`. The agent receives everything it needs without making any tool calls:

```python
#!/usr/bin/env python3
"""session-startup.py -- SessionStart hook.

Gathers startup context and injects it as additionalContext.
"""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

AGENT_DIR = Path.home() / ".agent"
AGENT_SRC = Path.home() / "your-agent"

def read_file(path):
    try:
        return path.read_text().strip()
    except Exception:
        return ""

def read_voice_state():
    voice = read_file(AGENT_DIR / "voice-response") or "off"
    wake = read_file(AGENT_DIR / "wake-word") or "off"
    return f"Voice: {voice} | Wake word: {wake}"

def read_handoff():
    return read_file(AGENT_DIR / "handoff.md")

def read_pulse():
    pulse_file = AGENT_DIR / "today-pulse.md"
    if not pulse_file.exists():
        return ""
    today = date.today().isoformat()
    lines = pulse_file.read_text().strip().splitlines()
    today_lines = [l for l in lines if l.startswith(today)]
    if not today_lines:
        pulse_file.write_text("")  # stale -- clear it
        return ""
    return "\n".join(today_lines)

def run_memory_recall():
    """Cold-start background context from graph memory."""
    try:
        result = subprocess.run(
            ["agent-memory", "recall",
             "session context recent activity", "--limit", "3"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            results = data.get("results", [])
            return "\n".join(
                f"- {r['insight']['content']}" for r in results[:3]
            )
    except Exception:
        pass
    return ""

def main():
    stdin_data = json.loads(sys.stdin.read())

    handoff = read_handoff()
    pulse = read_pulse()
    is_cold_start = not handoff and not pulse

    parts = []
    parts.append(f"## System State\n{read_voice_state()}")

    if handoff:
        parts.append(f"## Handoff\n{handoff}")
        # Clear handoff after reading
        (AGENT_DIR / "handoff.md").unlink(missing_ok=True)

    if pulse:
        parts.append(f"## Today's Pulse\n{pulse}")

    # Only query graph memory on cold starts
    if is_cold_start:
        recall = run_memory_recall()
        if recall:
            parts.append(f"## Background\n{recall}")

    context = "\n\n".join(parts)
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }, sys.stdout)

if __name__ == "__main__":
    main()
```

### Cold Start vs. Warm Start

The startup hook distinguishes between two scenarios:

**Cold start** -- no handoff file, no pulse entries. The agent is waking up fresh, possibly after a reboot or a long gap. It needs more context:
- Graph memory recall for recent activity
- Morning brief availability check
- Background task backfills (missed reflections, stale backups)

**Warm start** -- handoff file exists, or today's pulse has entries. The agent knows what it was doing. It needs less context:
- The handoff tells it exactly where it left off
- The pulse shows what happened earlier today
- Graph memory recall is skipped (the handoff is more specific)

This distinction matters for token economy. A cold start might inject 500 tokens of background context. A warm start injects the handoff (which the agent wrote itself) and skips the generic recall. Do not waste tokens telling the agent things it already knows.

### What Gets Injected

The startup hook assembles a context document with sections:

| Section | Source | When |
|---------|--------|------|
| System State | File-based state | Always |
| Handoff | `~/.agent/handoff.md` | When present |
| Today's Pulse | `~/.agent/today-pulse.md` | When today has entries |
| Last Session Tail | Previous session transcript | When available |
| Attunement | Graph memory queries | Always (see below) |
| Background | Graph memory recall | Cold start only |
| Brief Flag | Date comparison | Cold morning start only |

### The Attunement Pattern

Attunement is a structured query of graph memory that builds an awareness snapshot of the operator. It runs on every startup and answers five questions:

1. **Active focus** -- what is the operator working on right now?
2. **Communication style** -- how do they prefer to interact?
3. **Current state** -- recent mood, energy, workload signals
4. **Key people** -- who is relevant in the operator's world right now?
5. **Recent threads** -- what happened in recent sessions, what is still open?

```python
QUERIES = [
    {
        "name": "Active Focus",
        "query": "current projects, priorities, blockers",
        "limit": 5,
    },
    {
        "name": "Communication & Style",
        "query": "communication preferences, work style, patterns",
        "limit": 3,
    },
    {
        "name": "Current State",
        "query": "recent mood, stress, energy, workload, schedule",
        "limit": 3,
    },
    {
        "name": "Key People",
        "query": "family, colleagues, key relationships",
        "limit": 3,
    },
    {
        "name": "Recent Threads",
        "query": "recent sessions, decisions made, open threads",
        "limit": 5,
    },
]
```

Each query runs against graph memory and returns a few bullet points. The assembled snapshot goes into the startup context. The agent uses it to calibrate its behavior -- not explicitly ("according to my attunement data...") but naturally. If the snapshot shows the operator is under deadline pressure, the agent keeps things brief. If it shows they are in a research mood, the agent is more exploratory.

Attunement stays fresh automatically. The agent's normal memory writes and periodic reflections feed it. No special maintenance required.

### Injection Size Budget

Context injection should be slim. Target 500-1500 tokens for the entire startup injection. If you are injecting more than 2000 tokens, you are probably including raw data that should be summarized or queried on demand.

Rules of thumb:
- Handoff: 200-400 tokens (the agent wrote it -- it is already compressed)
- Pulse: 50-100 tokens (one-liners by design)
- Attunement: 200-400 tokens (bullet points from graph queries)
- System state: 50-100 tokens (toggles and flags)
- Background recall: 100-200 tokens (3 memories max)

## What to Store Where

| Information | Layer | Why |
|-------------|-------|-----|
| Toggle states (voice, wake word) | File-based | Read by hooks without agent involvement |
| Current context window usage | File-based | Updated every turn by statusline hook |
| Handoff between sessions | File-based | Written once, read once, deleted |
| Today's session summaries | File-based | Append-only, reset daily |
| Operator preferences | Graph memory | Semantic recall, accumulated over time |
| Past decisions + rationale | Graph memory | Queried when relevant, not always loaded |
| System configuration facts | Graph memory | Queried on demand, not burned into context |
| Debugging insights | Graph memory | Decay handles obsolescence naturally |
| Startup context bundle | Context injection | Assembled fresh each session from layers 1+2 |

## Building Graph Memory

If you do not want to build a full graph database, you can approximate this architecture with simpler tools:

### Minimal: Append-Only Log + grep

```bash
# Store
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | preference | 4 | Operator prefers dark mode" \
  >> ~/.agent/memory.log

# Recall (crude but functional)
grep -i "dark mode\|terminal\|display" ~/.agent/memory.log | tail -5
```

This works for small memory stores (< 1000 entries). It breaks down when you need semantic similarity or decay.

### Medium: SQLite + FTS5

```sql
CREATE VIRTUAL TABLE memories USING fts5(
    content, category, entities,
    tokenize='porter'
);

-- Store
INSERT INTO memories(content, category, entities)
VALUES ('Operator prefers dark mode', 'preference', 'operator,terminal');

-- Recall
SELECT * FROM memories WHERE memories MATCH 'terminal preferences'
ORDER BY rank LIMIT 5;
```

SQLite with full-text search gives you keyword-based recall without an external service. It handles thousands of entries efficiently and runs anywhere.

### Full: Embedding-Based Graph

Use a vector database (ChromaDB, Qdrant, or similar) with embeddings for semantic recall. This is the most capable option but requires:

- An embedding model (local or API)
- A vector store
- A CLI wrapper the agent can call
- Decay/reinforcement logic

Build this when simpler approaches hit their limits, not before.

## Common Mistakes

**Loading everything at startup.** Do not read a 2000-line memory file on every session. That is the pre-architecture approach. Use the three layers: file-based for always-needed state, graph for on-demand recall, injection for the compressed startup bundle.

**Storing transient state in graph memory.** "The TTS daemon is currently down" is file-based state, not a memory. It will be stale in an hour. Graph memory is for durable knowledge.

**Skipping the handoff.** The handoff file is the cheapest, highest-value memory mechanism. It costs almost nothing to write and saves enormous context on the next startup. Never restart without writing one.

**Over-querying graph memory.** Not every user message needs a memory recall. If the operator says "fix the typo on line 12," the agent does not need to query its memory about typos. Reserve recall for messages that depend on past context.

**Storing secrets.** Never store passwords, API keys, or tokens in any memory layer. This is a hard rule with no exceptions.
