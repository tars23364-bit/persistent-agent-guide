# Memory Architecture

A persistent agent needs memory that outlives individual sessions. Claude Code has no built-in long-term memory -- every restart wipes the slate. The memory architecture described here solves that with two tiers of purpose-built storage, plus infrastructure that delivers the right context at the right time.

## The Problem

Without memory, a persistent agent:

- Forgets operator preferences after every restart
- Cannot reference past decisions or their rationale
- Repeats mistakes it already learned from
- Loses track of ongoing projects and context
- Treats every session as the first session

You can partly solve this with a big `memory.md` file that the agent reads on startup. That works for a few weeks. Then the file grows to thousands of lines, startup burns tokens loading it, and half the content is stale. The two-tier architecture handles this more gracefully.

## Starting Point: Claude Code's Built-in Auto-Memory

Claude Code ships with an OEM auto-memory system. It writes individual `.md` files with YAML frontmatter (types: `user`, `feedback`, `project`, `reference`) and indexes them in `MEMORY.md`. If you are just getting started, this is the simplest thing that works.

### How It Works

When the agent learns something about the user or project, it creates a file like:

```markdown
---
type: user
---
Operator prefers dark mode in all terminal applications.
```

These files accumulate in a memory directory, and `MEMORY.md` acts as an index that is always loaded in the first ~200 lines of context.

### Where It Breaks Down

This system has scaling limits that show up within weeks of active use:

- **MEMORY.md is always loaded.** Every session pays the token cost of the full index, whether the content is relevant or not. At 50+ entries, you are burning hundreds of tokens on stale context.
- **Individual files proliferate.** There is no consolidation, deduplication, or decay. The 200th memory file is treated the same as the first.
- **No semantic search.** Retrieval is by file read, not by meaning. The agent cannot ask "what do I know about terminal preferences?" -- it reads everything or nothing.
- **No importance scoring.** A one-off debugging fact and a core operator preference have the same weight.
- **No decay.** Old memories never fade. The system accumulates noise indefinitely.

For a weekend project, this is fine. For a persistent agent that runs for months, you need something more intentional. The architecture below replaces the OEM system with two purpose-built tiers.

One specific anti-pattern to avoid once you adopt L2: do **not** continue writing individual `user_role.md`-style memory files alongside the graph store. That creates a split-brain between two systems. Commit fully -- L1 (MEMORY.md) as the working scratchpad, L2 (graph memory) as the durable store, nothing else.

## The Two-Tier Architecture

The evolved pattern uses two tiers, each optimized for a different access pattern:

- **L1 (MEMORY.md)** -- hot working context, always in the context window. This is the scratchpad: what is happening *right now*, updated during sessions, pruned on restart.
- **L2 (Graph memory)** -- durable long-term storage. Searched on demand via CLI. This is the source of truth for anything that persists beyond a single session.

Surrounding these two tiers is infrastructure: file-based state (toggles, flags, timestamps) read by hooks and daemons, and a context injection layer that assembles the right information at startup. These are not memory tiers themselves -- they are plumbing that makes the tiers work.

```
Infrastructure (file-based state)
├── Toggle files (voice, wake word, flags)
├── Handoff file (session bridge)
├── Pulse file (daily texture)
└── Context bridge (usage metrics)

L1: MEMORY.md (working context)
├── Active Work — what's in flight
├── Open Questions — cross-session follow-ups
└── Session Notes — scratchpad, wiped on restart

L2: Graph Memory (long-term storage)
├── Preferences, decisions, facts, insights, context
├── Importance scoring (1-5)
├── Entity graph (memories link to related memories)
└── Natural decay (access reinforces, absence fades)

Context Injection (startup assembly)
└── SessionStart hook gathers from all sources → additionalContext
```

## L1: MEMORY.md (Working Context)

MEMORY.md lives in your project directory and is auto-loaded by Claude Code in the first ~200 lines of every session. It is not a knowledge base -- it is a whiteboard. It has two jobs: (1) track what is actively in flight, and (2) serve as a pointer index to per-project READMEs and other living documents. Three sections:

### Active Work

What the agent is currently working on. For any project that has its own README, a single line pointing there is enough -- the README is the source of truth for status, phase, and open questions. Inline prose is for projects too small to warrant a README.

```markdown
## Active Work

- Startup hook refactor — testing consolidated warm-path logic → project/startup-hook/README.md
- Camera MCP server — USB enumeration unreliable after reboot, workaround in place
- Backup worker — pilot running, verifying 3-day retention window
```

Keep entries to one line. If an entry is growing into a paragraph, that project needs a README.

### Open Questions

Things to follow up on across sessions. Not tasks -- questions that need answers or decisions that need to be revisited.

```markdown
## Open Questions

- TTS model — check if newer version has been released
- Launchd vs cron for cardiac cycle — revisit after 30 days of launchd reliability data
```

### Session Notes

Scratchpad for the current session. Anything that might be useful later but has not been triaged yet. Wiped on every restart after the promotion scan (see below).

```markdown
## Session Notes

- Discovered USB devices need 15s to enumerate after reboot — verify on next cold start
- Operator mentioned preferring shorter morning briefs — observe for two more sessions before storing
```

### L1 Discipline

MEMORY.md should stay short -- ideally under 200 lines. If it is growing beyond that, you are storing things that belong in project READMEs or L2. Common symptoms:

- Active Work entries with multi-line status blocks that belong in a README
- Open Questions that were answered but never pruned
- Session Notes that survived multiple restarts

The promotion protocol (below) prevents this rot.

### Monitoring Memory Health

The promotion protocol is a *manual* discipline -- it runs when the agent restarts and remembers to scan. Discipline decays. The file that is "always loaded" is exactly the one whose growth nobody notices, because every session pays the cost a little at a time. By the time startup feels slow, the rot is months deep.

So measure it mechanically. A small background worker -- a launchd job on a slow cadence (every few minutes is plenty; the file changes rarely) -- watches the memory file for three distinct failure modes that no single metric catches:

- **Size.** A token estimate (`len(raw) // 4` is close enough) against advisory and hard thresholds. This is the obvious one and the least useful -- by the time the file is *big*, you already feel it.
- **Growth.** Current size against a *baseline snapshot* captured right after the last clean-up. A file that is 3,000 tokens and stable is healthy; a file that is 3,000 tokens and was 1,800 a week ago is rotting. Growth catches the trend before the absolute size trips an alarm. Re-baseline after every audit so growth is measured against your own clean state, not against zero.
- **Semantic redundancy.** Split the file into blocks (bullets, paragraphs, headings), embed each one with a local embedding model, and cluster pairs whose cosine similarity exceeds ~0.90. This surfaces the *same fact restated five ways* -- the single most common bloat pattern, and the one size and growth are both blind to, because five paraphrases of one fact look exactly like five distinct facts to a byte counter. Cache the per-block embeddings by content hash so each run only re-embeds what changed, and skip the whole pass when the file is byte-for-byte unchanged since the last sample.

**The collector flags; it does not decide.** This is the load-bearing rule. The worker's job ends at writing a tiered status (GREEN / AMBER-advisory / RED) to a state file. Acting on it -- culling stale entries, promoting a status block into a project README, merging the five paraphrases -- is the agent's call at the next restart or in conversation, not the worker's. A background process that silently rewrites the agent's own working memory is a far worse failure than a bloated file. Doc-bloat is ambient context, not a 3 AM interrupt: surface it where the agent will see it anyway (startup injection, a status tile), and leave the push notifications for things that are actually on fire.

The irony worth internalizing: an agent can be asked *"is my memory file healthy?"* and answer confidently from a file it can no longer fully load, because the file outgrew the read budget. Interoception -- the agent measuring its own state from the outside -- exists precisely because self-report from the inside is unreliable. The same pattern applied to the *host* (disk, memory pressure, service liveness) is covered in [OS Integration](04-os-integration.md#interoception-a-granular-vitals-feed).

## L2: Graph Memory (Long-Term Storage)

L2 is the durable layer. It handles knowledge that is *sometimes* relevant -- operator preferences, past decisions, system facts, debugging insights. The agent queries it on demand rather than loading it into every session.

### What Graph Memory Provides

- **Semantic recall** -- query by meaning, not exact file path
- **Importance scoring** -- not all facts are equally worth remembering (1-5 scale)
- **Natural decay** -- old, unreinforced memories fade; accessed memories get reinforced
- **Entity extraction and graph edges** -- memories link to related memories, forming a knowledge graph
- **Multiple stores** -- separate named stores let you isolate domains (work, personal, per-project) so queries do not bleed across contexts
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

If your graph memory tool supports PPR (Personalized PageRank) augmentation, consider wrapping it in a thin shell script that routes in-conversation recall through PPR while leaving short structured queries (startup recall, category-filtered lookups) on the direct path. PPR improves multi-hop recall for free-form conversational queries; it can *hurt* precision on short targeted queries. Keep the paths separate.

### Categories and Importance

| Category | Importance | Examples |
|----------|-----------|----------|
| Preferences | 4-5 | Communication style, tool choices, scheduling habits |
| Decisions | 4-5 | "We decided to use launchd instead of cron," architectural choices |
| Facts | 3-4 | System configuration, hardware specs, network topology |
| Insights | 3-4 | "USB devices need 15s to enumerate after reboot" |
| Context | 2 | Background information, project descriptions, one-off notes |

### What NOT to Store

- **Secrets** -- no passwords, API keys, tokens. Ever. Hard rule, no exceptions.
- **Code patterns or architecture** -- these live in the codebase and git history. They are derivable. Storing them creates stale duplicates.
- **Transient state** -- things that change hourly belong in file-based state, not memory.
- **Ephemeral task details** -- use task files or plans for these. Memory is for knowledge, not project management.
- **Session noise** -- not every conversation turn is worth remembering.

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

Recall before web search -- if the answer is already in memory, retrieving it there is faster and cheaper.

```markdown
# Example rule in .claude/rules/protocols.md

## Memory Recall

Before responding to messages that involve past decisions, system
knowledge, or operator preferences: run `agent-memory recall "<query>"`
first. Craft keyword-rich queries -- don't pass the raw user message.

Skip when: direct follow-up already in context, or no dependency
on past work. Recall before web search.
```

### Remember Protocol

After every substantive response, the agent runs a decision tree:

1. **Does the exchange contain a user directive, reasoning conclusion, or durable observed state?** If no to all three, stop. Most turns produce nothing worth storing.

2. **Does a highly overlapping memory already exist?** If yes, decide: update the existing memory (APPEND-CORRECTION), replace it, or skip. Do not create near-duplicates.

3. **Is it worth storing?** The test: would rebuilding this knowledge from scratch cost more than storing and recalling it? If the answer is derivable from a config file or git log, do not store it.

4. **Delegate the write.** Do not run `agent-memory remember` in the main conversation thread. Batch all writes from a single turn into one sub-agent call. Pass the content and metadata (category, importance, entities) -- not raw CLI commands. The sub handles mechanical execution; the main thread handles judgment.

Write classes:
- **APPEND** -- new fact, no prior overlap. Sub writes directly.
- **APPEND-CORRECTION** -- supersedes stale state. Compose the corrected fact in the main turn; sub writes the new entry and links it to the stale one. The built-in diff handles near-match auto-replacement; the explicit link adds graph traceability.
- **DESTRUCTIVE FORGET** -- hard expunge of wrong or sensitive data. Gate this: get operator acknowledgment before the sub runs the delete.

Importance guidelines:

- Operator preferences, decisions, corrections: importance 4-5
- System facts, configuration discoveries: importance 3-4
- Background context: importance 2
- Never store secrets, passwords, API keys, or transient noise

## The Promotion Protocol

Promotion is the bridge between L1 and L2. It runs at session end (during your restart/shutdown sequence), before clearing MEMORY.md:

### Step 1: Scan Active Work

- Still active? Keep in L1.
- Completed? Remove from L1. If the completion involved a decision or insight worth preserving, promote to L2.
- Project state changes? Update the project's README -- not L1, not L2.

### Step 2: Scan Open Questions

- Answered? Remove from L1.
- Still open? Keep in L1.
- Persisted 3+ sessions without resolution? Promote to L2 as context ("open question about X, unresolved since date Y"), then remove from L1.

### Step 3: Scan Session Notes

- Contains durable knowledge (insight, preference, decision)? Promote to L2.
- Everything else? Discard.

The full flow at restart is: durable insights → L2, project state changes → per-project READMEs, global state changes → MEMORY.md pointer updates. Handoffs are pointers ("see project README"), not retransmissions of project state.

### Why This Matters

Without promotion, L1 becomes a graveyard of stale notes and L2 stays empty. Without pruning, L1 grows until it is as noisy as the monolithic memory file you were trying to escape. The protocol ensures knowledge flows from scratchpad to durable storage at the natural session boundary.

```markdown
# Example rule for .claude/rules/protocols.md

## Promotion Protocol

At session end (/restart), before clearing MEMORY.md:
1. Active Work — still active? Keep. Done? Remove (promote insight if any). State changes? → project README.
2. Open Questions — answered? Remove. Open? Keep. 3+ sessions? Promote to L2.
3. Session Notes — durable? Promote to L2. Rest → discard.
Handoffs = pointers to READMEs + next action. Not a retransmission of project state.
```

## Project READMEs: Per-Project Source of Truth

As your agent accumulates active projects, L1 (MEMORY.md) becomes a tempting place to track status per project. Resist this. A project that has more than a few sentences of state deserves its own README.

### What Goes in a Project README

Each project README is the definitive catch-up document for that project. A useful structure:

- **Situation** -- one paragraph of current state, written for a reader who was away for two weeks
- **Quick Status** -- a table, one row per live workstream: what it is, current state, what's next
- **Phase map** -- where the project has been and where it is going
- **Key files** -- the three to five files a new session needs to know about
- **Open questions** -- project-specific blockers and unresolved decisions

```markdown
## Situation
The async worker was refactored last week to use launchd instead of cron.
Three of five workers are running cleanly; two are failing silently.

## Quick Status
| Workstream | State | Next |
|------------|-------|------|
| launchd migration | 3/5 live | debug workers 4 and 5 |
| monitoring hooks | in progress | finish stop hook |

## Key Files
- `workers/async-worker.py` — main worker
- `launchd/com.agent.async-worker.plist` — launchd config
- `.claude/rules/workers.md` — worker conventions
```

### How L1 References It

MEMORY.md carries one line per active project, pointing to its README:

```markdown
## Active Work
- Async worker launchd migration — workers 4/5 failing silently → projects/async-worker/README.md
```

Do not copy project state into MEMORY.md. Read the README on demand, when you branch into that project's work. The handoff (written at restart) lists which READMEs the next session should consult and what the immediate next action is -- it does not retransmit project state.

## The Reference Shelf: Stable Ground Truth

The memory tiers above handle *evolving* knowledge -- L1 tracks what is hot right now, L2 accumulates preferences and insights over time, and project READMEs capture per-project state as it changes. There is a fourth category that fits none of those: knowledge that is **stable, operational, and needs to be citable** -- environment facts about your machines and network, and runbooks for recurring procedures.

This knowledge belongs in a separate home. We call ours the reference shelf (the live implementation is named Atlas, if you want a concrete reference point).

### Why a Separate Home

The distinction matters:

| Layer | Changes how often | What you do with it |
|-------|------------------|---------------------|
| L2 (graph memory) | Frequently -- new insights every session | Recall against; fades if unused |
| Project READMEs | Per project -- as work evolves | Read before branching into a project |
| Reference shelf | Rarely -- when environment changes | Check and cite before acting on known procedures |

Putting environment facts in graph memory creates two problems. First, they never decay -- a memory about which SSH port a host uses does not fade after a few weeks of non-use, which is exactly what decay is supposed to do. Second, they are not citable -- when the agent derives an SSH command from a graph memory result, there is no stable path another agent or a human can point to and verify. The reference shelf is just files on disk. The path is the citation.

Putting runbooks in project READMEs creates a different problem: runbooks are cross-cutting. The procedure for renewing OAuth on a headless host does not belong to any single project -- it is environment ops, used whenever the need arises. Mixing it into a project README obscures it.

The reference shelf separates stable ground truth from evolving state. That separation buys two things:

- **Single-writer trust.** One agent owns and writes the shelf for its primary host. Peer agents read but do not write. There is no concurrent-write contention, no merge conflict, no stale override. Trust is proportional to write discipline.
- **Cross-agent portability.** The shelf is markdown with a uniform `INDEX.md` traversal contract. Any agent -- any model, any harness, any host -- can read it with no model-specific affordances. An agent that cannot call a tool or run a query can still `Read` a file.

### Structure

A minimal reference shelf looks like this:

```
reference/
├── INDEX.md          # Root: summary, status, contents list, recent changes
├── CHANGELOG.md      # Append-only. One line per change: date + path + description
├── system/
│   ├── INDEX.md      # Overview of system branch
│   ├── INFO.md       # Hardware, OS, compute specs
│   ├── NETWORK.md    # Hosts, IPs, SSH aliases, topology
│   ├── ACCESS.md     # How to reach what (pointers to secrets, never secrets)
│   └── QUIRKS.md     # Discovered environment misbehaviors; append-only
└── ops/
    ├── INDEX.md      # Index of all runbooks
    ├── renew-auth-headless.md   # Renew auth on a headless host via VNC
    ├── launch-gpu-training.md  # Submit a batch training job to your compute cluster
    └── bulk-transfer.md        # Large file transfer between hosts over direct link
```

Every `INDEX.md` follows the same shape (the contract that makes traversal uniform):

1. **Summary** -- one paragraph on what this branch covers
2. **Status** -- current state, or N/A
3. **Contents** -- each file/folder with a one-line description and link
4. **Recent changes** -- the last 3-5 entries from `CHANGELOG.md` relevant to this branch
5. **Cross-refs** -- links to related branches or external docs

The shape does not change. An agent that has read one `INDEX.md` knows how to read any other.

### What Goes Here

The membership test (from the live spec): *Could a stranger reading just this entry act on it correctly without the backstory?* If yes, it belongs on the shelf. If it needs narrative, it goes elsewhere.

**Good reference shelf content:**
- Machine specs, hostnames, SSH aliases, network topology
- Access patterns (how to reach a host, which credential to use -- pointer only, never the credential itself)
- Known environment quirks and workarounds ("this service needs 15 seconds after reboot before it responds")
- Step-by-step runbooks for recurring procedures that do not belong to any single project

**Does not belong here:**
- Operator preferences, decisions, insights -- those are L2, they evolve and decay
- Project status and phase -- that is the project README
- Transient state (a job is currently running, a service is currently down) -- that is file-based state
- Secrets -- the shelf carries pointers ("use the keychain entry named X"), never the secret itself

### Write Discipline

The shelf only stays trustworthy if it is updated when the environment changes. When you resolve an environmental surprise -- a service moved, a hostname changed, a quirk was discovered -- the resolution includes updating the relevant shelf entry. Not optional. A shelf that is sometimes stale is worse than no shelf, because the agent stops trusting it.

The corollary: check the shelf *before* acting on a known procedure, not after. This pairs with the "ground before modify" discipline -- check installed reality (the shelf) before improvising. If the shelf does not have the answer, add it after you discover it. That is how it stays current.

Append-only files (`QUIRKS.md`, `CHANGELOG.md`) get new entries, not overwrites. The record of what was true before is part of the value -- it explains why the current state is what it is.

### When to Build It

You do not need a reference shelf on day one. The signal that it is time:

- You find yourself re-deriving SSH commands or network paths from memory or scattered notes
- A runbook lives in your head (or a chat log) and you have executed it more than twice
- A peer agent (or a second session of the main agent) needs environment facts that only you have ground truth on
- You want a human to be able to audit what the agent knows about the environment without reading graph memory exports

Start with a flat `reference/` directory and a single `INDEX.md`. Add `system/` entries as you formalize what you already know. Add runbooks to `ops/` as procedures recur. The shelf grows from necessity, not upfront design.

---

## Infrastructure: File-Based State

The simplest and most reliable layer. Plain files on disk, read and written by hooks and scripts. Not a memory tier -- it is plumbing that supports the tiers above.

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

**Hooks are first-class consumers.** The file-based layer exists largely so that shell hooks -- which fire on every prompt or response -- can read state without calling the agent or a database. A bash hook that checks `[ -f ~/.agent/tts-suppress ]` costs nothing.

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

## Context Injection: The Startup Hook

The infrastructure and memory tiers store information. Context injection delivers it. A `SessionStart` hook gathers from all sources and injects the right context into the agent's window at startup. (See [Context Management](./03-context-management.md) for the full hook implementation.)

The core logic is straightforward: read the handoff, read today's pulse, check if this is a cold or warm start, and assemble a context document:

```python
def main():
    handoff = read_handoff()
    pulse = read_pulse()
    is_cold_start = not handoff and not pulse

    parts = []
    parts.append(f"## System State\n{read_voice_state()}")

    if handoff:
        parts.append(f"## Handoff\n{handoff}")
        (AGENT_DIR / "handoff.md").unlink(missing_ok=True)

    if pulse:
        parts.append(f"## Today's Pulse\n{pulse}")

    if is_cold_start:
        recall = run_memory_recall()  # graph memory query
        if recall:
            parts.append(f"## Background\n{recall}")

    context = "\n\n".join(parts)
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }, sys.stdout)
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

| Section | Source | When |
|---------|--------|------|
| System State | File-based state | Always |
| Handoff | `~/.agent/handoff.md` | When present |
| Today's Pulse | `~/.agent/today-pulse.md` | When today has entries |
| Last Session Tail | Previous session transcript | When available |
| Tasks | Task index | When tasks exist |
| Profiles | Per-person summaries | When populated |
| Background | Graph memory recall | Cold start only |
| Brief Flag | Date comparison | Cold morning start only |

### The Recall-on-Cold-Start Pattern

A simpler alternative to running multiple structured queries every startup is single-query background recall. On a cold start (no handoff file, no pulse entries for today), run one free-form recall query for recent activity and inject the top few results as a "Background" section:

```python
# Cold start only -- warm starts already have a handoff
if is_cold_start:
    recall = run_graph_recall("session context recent activity", limit=3)
    if recall:
        parts.append(f"## Background\n{recall}")
```

This is cheaper than the multi-query attunement pattern described in some earlier designs. The tradeoff: less structured (you get whatever the graph considers most relevant rather than answers to specific questions), but it is faster, lower token cost, and does not risk stale structured queries. On warm starts, the handoff is more specific and background recall is skipped entirely.

If you find the single-query approach misses important context (preferences, communication style), add targeted queries -- but measure the token cost first. Structured multi-query attunement can consume 300-500 tokens on startup even when none of it is relevant to the current conversation.

### Injection Size Budget

Context injection should be slim. Target 500-1500 tokens for the entire startup injection. If you are injecting more than 2000 tokens, you are probably including raw data that should be summarized or queried on demand.

Rules of thumb:
- Handoff: 200-400 tokens (the agent wrote it -- it is already compressed)
- Pulse: 50-100 tokens (one-liners by design)
- Attunement: 200-400 tokens (bullet points from graph queries)
- System state: 50-100 tokens (toggles and flags)
- Background recall: 100-200 tokens (3 memories max)

## What to Store Where

| Information | Where | Why |
|-------------|-------|-----|
| Toggle states (voice, wake word) | File-based infra | Read by hooks without agent involvement |
| Current context window usage | File-based infra | Updated every turn by statusline hook |
| Handoff between sessions | File-based infra | Written once, read once, deleted |
| Today's session summaries | File-based infra | Append-only, reset daily |
| Current tasks and in-flight work | L1 (MEMORY.md) | Pointer index; one line per active project |
| Cross-session open questions | L1 (MEMORY.md) | Visible until answered or promoted to L2 |
| Session scratchpad | L1 (MEMORY.md) | Temporary, wiped after promotion scan |
| Per-project status, phases, open questions | Per-project README | Source of truth for project state; read on demand |
| Environment facts, host specs, network topology | Reference shelf | Stable, citable, single-writer; check before recurring ops |
| Recurring procedure runbooks | Reference shelf (ops/) | Cross-cutting -- not owned by any project; citable path |
| Operator preferences | L2 (Graph memory) | Semantic recall, accumulated over time |
| Past decisions + rationale | L2 (Graph memory) | Queried when relevant, not always loaded |
| System configuration facts | L2 (Graph memory) | Queried on demand, not burned into context |
| Debugging insights | L2 (Graph memory) | Decay handles obsolescence naturally |
| Startup context bundle | Context injection | Assembled fresh each session from all sources |

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
- Entity extraction for graph edges

Key implementation features to aim for:

- **Entity extraction**: when storing a memory, extract entities (people, systems, projects) and create graph edges. This lets recall traverse relationships, not just match keywords.
- **Importance scoring (1-5)**: not all facts deserve equal recall priority. Operator corrections are a 5. Background context is a 2.
- **Natural decay**: memories that are never accessed fade over time. Memories that are accessed get reinforced. This prevents the store from becoming a landfill.
- **Intent-aware recall**: the recall engine should detect what kind of query it is receiving -- a specific lookup ("what port does the TTS daemon use?"), an exploratory search ("what do I know about the operator's schedule?"), or context-building ("recent sessions and decisions"). Each type benefits from different retrieval strategies.
- **Categories**: preference, decision, fact, insight, context. These let the agent filter recall to the right domain.

Build this when simpler approaches hit their limits, not before.

## Common Mistakes

**Loading everything at startup.** Do not read a 2000-line memory file on every session. Use L1 for the hot scratchpad (small, always loaded), L2 for on-demand recall (large, queried), and context injection for the compressed startup bundle.

**Storing transient state in graph memory.** "The TTS daemon is currently down" is file-based state, not a memory. It will be stale in an hour. Graph memory is for durable knowledge.

**Skipping the handoff.** The handoff file is the cheapest, highest-value memory mechanism. It costs almost nothing to write and saves enormous context on the next startup. Never restart without writing one.

**Over-querying graph memory.** Not every user message needs a memory recall. If the operator says "fix the typo on line 12," the agent does not need to query its memory about typos. Reserve recall for messages that depend on past context.

**Storing secrets.** Never store passwords, API keys, or tokens in any memory layer. This is a hard rule with no exceptions.

**Writing memory from the main thread.** Memory writes (the `agent-memory remember` call) consume tokens and I/O in whatever context they run. If you write memories from the main conversation thread, you are polluting the operator's context window with bookkeeping. Batch all writes from a given turn into a single sub-agent call. The main thread decides *what* to store and composes the content; the sub handles execution. Never write in the main thread, and never spin up one sub per write -- batch them.

**Storing derivable information.** If it is in git history, a config file, or the codebase itself, do not duplicate it in memory. Code patterns, architecture decisions visible in the code, and file structures are all derivable. Memory should store things that are *not* written down elsewhere -- preferences stated verbally, one-off debugging discoveries, decisions made in conversation.

**Using memory as a task tracker.** Memory is for knowledge -- facts, preferences, insights, decisions. If you need to track work items, deadlines, and status, use a task management system (see [Task Management](./09-task-management.md)). Mixing the two makes both worse.

**Trusting stale memories blindly.** Memory records become stale. A preference stored three months ago may no longer apply. A system fact from before a migration is wrong. Always verify memories against current state before acting on them, especially for system configuration and technical facts.
