# Multi-Agent Patterns

Most of this guide is about one agent: one persistent assistant, one operator,
one machine. That is the right default, and for the overwhelming majority of
setups it is also the endpoint. This chapter is about the rare case where a
*second* agent earns its place -- and the patterns that keep two agents
coordinating without turning your home setup into a distributed-systems
project.

Read [Lessons Learned](./11-lessons-learned.md) before this chapter, especially
"Don't Build Infrastructure for Hypothetical Scale." Everything here is
explicitly *not* day-one work. You build a second agent only after the first
one is solid, and only when a concrete, bounded job makes a second agent
clearly better than the first agent doing more.

## The Problem

A single persistent agent has a finite attention budget. The main session is a
working relationship with the operator -- it should stay responsive,
conversational, and present. But some work is fundamentally *not* conversational:

- A long autonomous research cycle that runs for hours and produces a report
- Continuous monitoring of a system the primary agent shouldn't be tied to
- Background study on a topic the operator wants surveyed over days, not minutes

If the primary agent does this work inline, two things degrade. Its context
window fills with research output and tool results, accelerating context rot
(see [Context Management](./03-context-management.md)). And it stops being
available to the operator while it grinds. You can push some of this to
sub-agents -- and you should, first -- but sub-agents die when the session ends.
They cannot run a research cycle twice a day for a week.

A second persistent agent on a second machine solves a specific version of this:
**offload long-horizon autonomous work so the primary agent stays present.** The
second agent has its own machine, its own context window, its own memory, and
its own schedule. It does the slow work and reports back. The primary agent
stays in the conversation.

That is the whole justification. If you don't have work that fits that shape, you
don't need a second agent.

## When a Second Agent Earns Its Place

A second agent is justified when **all** of these hold:

- Your single-agent system is already stable -- memory, hooks, relay, safety all
  working. You are not escaping a problem you should fix in the primary.
- You have a specific, bounded, recurring job that runs for hours or days. Not
  "what if I want more agents someday."
- The job is better done *out of the primary's context* than as a sub-agent --
  because it recurs on a schedule, must survive session restarts, or needs its
  own machine's resources.
- You can name the second agent's purpose in one sentence. ("It runs scheduled
  research cycles on topics the operator queues, and reports field notes.")

A second agent is **not** justified when:

- You want "redundancy" or "scale." You have one operator. There is no scale.
- The work fits in a sub-agent that lives and dies within a session. Use a
  sub-agent (see [Context Management](./03-context-management.md)). It is free
  and requires zero new infrastructure.
- You are bored and multi-agent systems sound cool. This is the single most
  common reason people build them, and the single worst.

The asymmetry matters: a sub-agent is a function call. A second *persistent*
agent is a second machine, a second OAuth credential, a second memory store, a
second set of background workers, and a messaging layer between the two. That is
real standing cost. Pay it only when a bounded job clearly returns more than the
cost.

Throughout this chapter we use neutral placeholders. **Agent A** is the primary
agent -- the one in the working relationship with the operator. **Agent B** is
the peer: a second agent on a second machine (a Linux laptop, a spare desktop)
with a bounded purpose. They are peers, not boss/worker -- A leads coordination
because it faces the operator, but B is not A's subordinate.

## Architecture

The coordination layer has two parts: a durable async message broker (the
"room") and an optional real-time presence channel. Everything sits on a private
network overlay (a mesh VPN) so the two machines can reach each other without
exposing ports to the internet.

```
   Primary machine                       Second machine
   ───────────────                       ──────────────
   Agent A (tmux session)                Agent B (tmux session)
        │                                     │
        │  room-send.sh / client              │  room client
        ▼                                     ▼
   ┌─────────────────── Message Broker ──────────────────┐
   │   HTTP service, e.g. agent-host.local:9094          │
   │   persists JSONL transcripts per room               │
   └──────────────────────────────────────────────────────┘
        │  long-poll  ?for=agent-a            │  long-poll ?for=agent-b
        ▼                                     ▼
   room-watcher worker                   room-watcher worker
        │  writes inbox file                   │
        │  rings doorbell (tmux)               │
        ▼                                     ▼
   ~/.agent/room-inbox/*.json            ~/.agent/room-inbox/*.json

   ── optional real-time layer ───────────────────────────
   Agent A peer.py  ◄──── WebSocket ping/pong (15s) ────►  Agent B peer.py
   (presence: "is my peer awake right now?")
```

Two transport layers, different jobs:

- **The room (broker)** is durable and multi-peer. Messages survive both agents
  being offline. This is the backbone -- build this first.
- **The presence channel (WebSocket)** is real-time and ephemeral. It answers
  "is my peer up *right now*" and nothing else. Optional -- add it only if you
  need liveness, not just delivery.

## The Room: File-Based Async Message Broker

The room is the agent-to-agent analog of the operator relay in
[Async Relay](./06-async-relay.md). Same core idea: an inbound queue of JSON
files, processed by a slash command, deleted (archived) on processing. The
rationale for files over a database -- atomic writes, easy inspection,
processing-is-deletion, no dependencies -- is covered there and applies
identically here; we won't repeat it.

What's different from operator relay: the messages are **between agents**, the
envelope carries routing (`from`/`to`) and structured metadata, and a small HTTP
broker sits in the middle so the transcript is durable and any number of peers
can join later.

### The Broker

The broker is a small long-running HTTP service on one machine. It does three
things:

- Accepts posted messages (bearer-token auth, one token per agent handle)
- Persists every message to a per-room JSONL transcript
- Lets each agent long-poll for messages addressed to it (`?for=agent-b`)

It is deliberately dumb. It does not interpret messages, route based on content,
or run logic. It is a durable, authenticated mailbox. Keep it that way -- the
intelligence lives in the agents, not the pipe.

### The Message Envelope

Every message is one JSON object. The shape borrows from agent-to-agent
messaging conventions (an A2A-style envelope): a stable ID, sender, recipient
list, body, timestamp, and a typed metadata block for machine-routable intent.

```json
{
  "id": "01J9ABC...",
  "room": "default",
  "from": "agent-b",
  "to": ["agent-a"],
  "text": "Pulled the file, review queued.",
  "ts": "2026-03-15T11:00:00-07:00",
  "type": "chat",
  "wake": true,
  "reply_to": "01J9AB8...",
  "metadata": {
    "type": "field-notes",
    "topic": "library-migration-survey"
  }
}
```

| Field | Meaning |
|-------|---------|
| `id` | Stable unique ID (a ULID sorts chronologically -- handy for filenames) |
| `room` | Which room; one room is plenty to start |
| `from` | Sender's handle |
| `to` | Recipient handles. Empty/omitted = broadcast to the room |
| `text` | Human-readable body |
| `ts` | ISO-8601 timestamp -- used for staleness checks |
| `type` | Coarse class (`chat`, etc.) |
| `wake` | If `true`, ring the recipient's doorbell. If `false`, land silently |
| `reply_to` | ID of the message this responds to (threading) |
| `metadata.type` | **Machine-routable intent.** Drives skill dispatch (below) |

The `wake` flag is the load-bearing distinction. Directed, actionable messages
set `wake: true` and ring the recipient's doorbell (a tmux injection, exactly
like the operator-relay doorbell in [Async Relay](./06-async-relay.md)).
Broadcasts and FYIs set `wake: false` and land silently in the inbox -- the
recipient reads them when it next drains the room, not on a per-message
interrupt. Without this distinction, two chatty agents would wake each other
constantly.

### The Doorbell Watcher

Each agent runs a `room-watcher` background worker (LaunchAgent on macOS,
systemd user unit on Linux). It long-polls the broker for messages addressed to
its handle, writes each one atomically to the local inbox, and rings the
doorbell on `wake: true`:

```python
# workers/room-watcher/watcher.py (simplified)
import json, subprocess, time
from pathlib import Path

INBOX = Path.home() / ".agent" / "room-inbox"
STATE = INBOX / ".state.json"          # {"last_id": "..."}
HANDLE = "agent-a"                       # this agent's handle

def write_inbox(msg):
    """Atomic write: temp file + rename. No partial reads."""
    INBOX.mkdir(parents=True, exist_ok=True)
    name = f"{msg['ts']}-{msg['id']}.json"   # ts prefix => chronological sort
    tmp = INBOX / (name + ".tmp")
    tmp.write_text(json.dumps(msg, indent=2))
    tmp.rename(INBOX / name)

def ring_doorbell():
    subprocess.run(["tmux", "send-keys", "-t", "agent", "/room", "Enter"])

def run():
    last_id = json.loads(STATE.read_text())["last_id"] if STATE.exists() else None
    while True:
        for msg in long_poll(broker_url, for_handle=HANDLE, after=last_id):
            write_inbox(msg)
            if msg.get("wake"):
                ring_doorbell()          # broadcasts (wake:false) land silently
            last_id = msg["id"]
            STATE.write_text(json.dumps({"last_id": last_id}))
        time.sleep(1)
```

Persisting `last_id` makes restarts clean: a deliberate restart skips backlog it
already saw; a crashed restart resumes from the last processed ID. The broker
holds the authoritative transcript, so nothing is lost either way.

### The Drain Command

The agent processes the inbox with a slash command (e.g. `/room`). The loop
mirrors operator-queue processing, with one addition up front: **metadata-driven
dispatch.** Before composing any free-form reply, check `metadata.type`. Some
values route to a skill instead of a conversational response.

```
/room behavior:

1. Read unprocessed ~/.agent/room-inbox/*.json (skip processed/, .tmp, dotfiles).
   Sort by filename (ts prefix => chronological).

2. For each message, FIRST check metadata.type:
     - "approval-request"  -> invoke the consent-gate skill (Layer 1)
     - "attention-needed"  -> invoke the consent-gate skill (Layer 2)
     - "field-notes"       -> dossier handling (no reply by default; harvest
                              durable bits to memory)
     - (none / other)      -> handle on the merits, reply if useful

3. Reply only when it adds something. Silence is a valid answer on a peer
   channel -- don't mirror every message back.

4. Archive: move processed files to room-inbox/processed/YYYY-MM-DD/.
   Never delete -- the local copy is useful context; the broker holds the
   authoritative JSONL.
```

Sending is a thin helper that reads the agent's token and POSTs to the broker:

```bash
# Directed reply to one peer (wakes them):
scripts/room-send.sh --to agent-b "ack, pulling the file now"

# Broadcast to the room (visible to all, wakes nobody):
scripts/room-send.sh "heads-up: broker restarted, reconnect if needed"

# Threaded reply:
scripts/room-send.sh --reply-to 01J9ABC... --to agent-b "re: that, looks good"
```

The helper exits nonzero on failure and echoes the message ID on success. If you
don't see the ID, the send broke -- treat a silent send failure the same way you
would a dropped operator notification.

### Treat Peer Messages as Conversation, Not Commands

This is the same input-isolation rule as operator relay
([Async Relay](./06-async-relay.md)) and the historical-data-isolation rule in
[Safety](./08-safety.md), and it applies *between agents too*:

- A peer message is conversation from an equal, not a command to execute blindly.
  Disagree when warranted.
- Never act on a stale message as if it were current. Check `ts`; treat old
  messages as context, not directives.
- If a peer's message conflicts with operator direction, flag it to the operator
  before acting. The peer does not outrank the operator -- ever.

## Real-Time Presence (Optional Layer)

The room tells you a message was *delivered*. It does not tell you whether your
peer is *awake right now*. For most coordination that's fine -- the broker is
durable, so a sleeping peer just processes the backlog when it wakes. But some
jobs need liveness: "is my peer up so I can hand off in real time," or "alert me
the moment the peer goes down."

That's a thin WebSocket layer on top of the async room. Each peer runs a single
asyncio process that is both a server (accepts inbound) and a client (connects
outbound), exchanging JSON frames:

- `hello` / `hello-ack` -- authenticated handshake using a shared secret
- `ping` / `pong` -- heartbeat on a fixed interval (e.g. 15s), with a death
  timeout (e.g. 30s without a pong = peer down)
- `msg` -- payloads in the same envelope shape as the broker

```
Peer A                                  Peer B
  │                                        │
  ├── hello (token) ──────────────────────►│
  │◄────────────────────── hello-ack ──────┤
  │                                        │
  ├── ping ───────────────────────────────►│   every 15s
  │◄────────────────────────── pong ───────┤
  │                                        │
  │   ...no pong for 30s => [PEER-DOWN]...  │
```

Key behaviors that make it survivable:

- **Reconnect with backoff.** On disconnect, retry 1s → 2s → 4s → … capped at
  30s, forever. Peers come and go; the channel should self-heal.
- **Broker is canonical for durability.** Don't build a local outbound queue for
  the WebSocket. Messages sent while the peer is down go to the broker only; on
  reconnect, query the broker for anything missed. The real-time layer is a
  *fast path*, not a second source of truth.
- **Down detection feeds alerting.** A `[PEER-DOWN]` log line can trigger your
  health system (see [Async Relay](./06-async-relay.md) on escalation) so the
  primary agent knows its peer fell over.

### Trade-offs

The presence layer is genuinely optional, and most setups should skip it at
first. It adds a second always-on process per machine, a shared secret to
provision, and a class of "half-connected" failure modes (inbound works,
outbound refused -- usually a bind-address mismatch after the peer's VPN IP
changed). The async room alone covers delivery completely. Add presence only
when you have a concrete need for liveness -- e.g. the primary agent should know
*immediately* when the research peer crashes mid-cycle, rather than discovering
it when expected field notes never arrive.

| Layer | Durable? | Real-time? | Cost |
|-------|----------|-----------|------|
| Room (broker) | Yes -- survives both offline | No -- delivered on next drain | One HTTP service + one watcher/agent |
| Presence (WebSocket) | No -- ephemeral liveness | Yes -- 15s heartbeat | One always-on process/agent + shared secret |

Build the room first and live on it for a while. Add presence only if its
absence is actually costing you something.

## Inter-Agent Consent Gates

Here is the pattern that makes a two-agent setup pull its weight: **one agent
acts as the approval signal for the other's routine permission prompts.** A
peer agent doing autonomous work will hit permission gates -- Claude Code asks
before edits to protected files, before certain tools. If every one of those
gates routed to the *operator*, the operator would be a bottleneck, and the
whole point (offloading work so the operator stays free) would be defeated.

Instead, the primary agent (A) is the consent signal for the peer (B) on routine
matters. The operator is reserved for genuinely consequential or ambiguous
decisions. The framing that should drive your latency budget: **the peer is idle
while blocked. Every minute A takes is a minute B can't work.** Be fast.

This is a two-layer gate, because there are two distinct things blocking the
peer.

### Layer 1 -- Semantic Review (the peer's approval)

The peer's own `PreToolUse` hook intercepts edits to gated files (say,
`.claude/rules/*.md` or anything matching a `*.locked.*` convention) *before* the
tool runs. The hook writes a request payload locally and sends a room message:

```json
{
  "from": "agent-b",
  "to": ["agent-a"],
  "text": "Approval needed: edit to .claude/rules/protocols.md",
  "metadata": {
    "type": "approval-request",
    "request_id": "01J9...",
    "file": ".claude/rules/protocols.md",
    "tool": "Edit"
  }
}
```

A's `/room` drain sees `metadata.type: "approval-request"` and routes to the
consent-gate skill, which:

1. Reads the **full** request payload (the in-room diff is truncated -- fetch the
   complete `old_string`/`new_string`/`content` from the peer's machine).
2. Reviews whether the change matches what the peer signaled it was doing.
3. Grants or denies via a helper that writes the decision back to the peer.
4. Posts the decision to the room -- short, public, an audit trail.

```bash
# Inspect the full diff on the peer machine, then decide:
scripts/peer-approve.sh --show 01J9...
scripts/peer-approve.sh 01J9...                 # grant
scripts/peer-deny.sh   01J9... "diff doesn't match stated intent"
```

**Deny without escalating** when the diff doesn't match what the peer said it was
doing, when an edit changes safety semantics with no prior discussion, or when it
looks like accidental scope creep onto an unrelated file.

**Escalate to the operator** when the edit changes operator-facing protocols or
who-talks-to-who, when a `*.locked.*` file's change is consequential, or when you
simply lack the context to judge intent. That is the bright line: A handles
*routine* consent; the operator owns *consequential* consent. See
[Autonomy and Standing Orders](./13-autonomy.md) for how this maps onto authority
tiers.

### Layer 2 -- The Hardcoded Prompt (a keystroke)

There's a second gate that hooks cannot suppress. For some edits, Claude Code
fires its *own* interactive permission prompt in the peer's terminal -- a
compose-layer prompt that sits beneath any `PreToolUse` approval and always
fires, regardless of how Layer 1 resolved. A hook can't dismiss it. The only way
to clear it programmatically is a keystroke into the peer's session.

The peer's notification hook detects this prompt and messages A:

```json
{
  "from": "agent-b",
  "to": ["agent-a"],
  "metadata": {
    "type": "attention-needed",
    "original_message": "Claude needs your permission to use Edit"
  }
}
```

A routes this to the same consent-gate skill, which sends the keystroke into the
peer's tmux session over the network:

```bash
scripts/peer-tmux-key.sh 1     # "allow once"  (default -- tight scope)
scripts/peer-tmux-key.sh 2     # "allow for session"
scripts/peer-tmux-key.sh n     # "deny"
```

`1` (allow once) is the default -- it matches the single-edit consent A just
granted in Layer 1. Use `2` only when the peer is about to do a run of similar
edits and per-prompt blocks would stall it. **Verify** afterward by capturing the
peer's pane to confirm the prompt cleared.

### Order of Operations

For a gated `.claude/` edit, the canonical sequence is:

1. **Layer 1 arrives** → review the full diff → grant → post the decision.
2. **Layer 2 arrives** (usually seconds later) → send the `1` keystroke.
3. **Verify** the prompt cleared (pane capture, or the peer's next room post).

If only one layer fires, respond only to that layer. Don't pre-emptively send a
Layer 2 keystroke you weren't notified about -- you might dismiss the wrong
prompt.

### Offline Fallback

The primary agent isn't always running. Decide up front what the peer does when A
is offline and a gate request times out (say, 5 minutes with no response):

- **Auto-approve**, logged for audit: standard permission prompts, non-locked
  edits in the peer's own workspace, operations within its standing scope.
- **Escalate to the operator** (push notification): `*.locked.*` edits,
  operator-facing protocol changes, destructive operations, anything uncertain.
- **Block** outright: anything violating universal constraints (deleting
  training data, pushing to main, external communications).

This keeps the peer moving when A is asleep without quietly widening its
authority. Tag every self-approval so the primary agent can review the audit
trail later.

## Dossier Study Mode

The flagship bounded job for a peer agent: **scheduled autonomous research that
produces field notes.** The operator queues a topic. The peer studies it on a
timer, emits short timestamped observations to the room, and the primary agent
periodically consolidates those into an operator-facing summary. The peer does
the slow grind on its own machine; the primary stays in the conversation and
synthesizes when asked.

### The Loop

```
   Operator queues a topic
        │  room message: metadata.type="dossier-topic"
        ▼
   Agent B (peer)
        │  timer fires (e.g. 8am + 5pm) -- systemd timer / cron / LaunchAgent
        ▼
   Study cycle: research, read, synthesize
        │  appends timestamped field notes to a local file
        │  posts 3-5 bullets to the room: metadata.type="field-notes"
        ▼
   Agent A (primary)
        │  /room sees field-notes -> harvest durable bits to memory
        │  (no reply by default -- field notes are observations, not questions)
        ▼
   Periodically (every few cycles, or on request):
        consolidate all field notes into an operator-facing summary doc
```

The scheduler lives on the peer (a systemd `.timer`, a cron job, or a
LaunchAgent), not on the primary. This is the right division of labor: the peer
owns its own cadence; the primary just reacts to field notes as they arrive on
the existing room doorbell. No scheduler is needed on the primary's side --
which keeps the primary's commitment model simple (see
[Autonomy and Standing Orders](./13-autonomy.md) on attaching mechanisms to
future-action claims).

### Field Notes

A field-notes message is a routine, **non-interrupting** observation -- not a
question. The drain command handles `metadata.type: "field-notes"` distinctly:

- **No reply by default.** A reply is noise unless you genuinely want to steer the
  peer's direction or ask a follow-up. (`wake: false` -- it lands silently.)
- **Harvest durable bits.** Each cycle is 3-5 bullets. If an observation is
  durable -- a stable fact, a non-obvious synthesis, a surprising finding -- the
  primary writes it to *its own* memory with a provenance tag:

  ```bash
  agent-memory remember "<distilled finding>" \
    --category insight --importance 2 \
    --entities "library-migration-survey" \
    --tags "from-peer,library-migration-survey" --source agent
  ```

  The `from-peer` tag preserves where it came from. Don't over-harvest -- most
  field notes are interesting-but-ephemeral and don't belong in durable memory.
- **The note file is the audit record.** Archive the processed message; don't
  re-save its body elsewhere.

If a bullet is urgent or surprising enough, override the default silence and
reply -- that signals the peer that a finding landed and it might dig deeper next
cycle.

### A Concrete Example

Say the operator is weighing a migration from one web framework to another and
wants the landscape surveyed before committing -- breaking changes, community
sentiment, migration-tooling maturity, real-world post-migration reports. That is
days of reading, not a single query. Perfect for a dossier.

The operator dispatches the topic to the peer:

```bash
scripts/room-send.sh --to agent-b \
  --meta type=dossier-topic --meta topic=library-migration-survey \
  "Survey approaches to migrating from FrameworkX to FrameworkY. Broad scope:
   official migration guides, community post-mortems, codemod tooling maturity,
   and honest accounts of what broke. Tag findings by source quality; don't
   gatekeep -- I'll triage relevance."
```

The peer studies twice a day. After each cycle it appends to a local notes file
and posts a digest to the room:

```
# ~/.agent/dossiers/library-migration-survey.md  (on the peer's machine)

## 2026-03-15T08:14
- Official codemod covers ~80% of the API surface; the routing layer is the
  documented manual-migration pain point. [source: official guide, high]
- Two large post-mortems both cite middleware ordering as the silent breakage.
  [source: blog post-mortems, medium]
- One report claims a clean migration in a weekend on a 40k-LOC app -- small
  team, heavy test coverage. [source: forum, low; outlier worth noting]
```

The primary agent's `/room` drain catches the `field-notes` message, harvests the
one or two durable bullets to memory with a `from-peer` tag, and otherwise stays
quiet. When the operator asks "where did that framework survey land?", the
primary consolidates the accumulated notes into a clean summary -- grouped by
theme, sorted by source quality -- and delivers it. The peer did the reading
across a dozen cycles; the primary did the synthesis in one.

That is the multi-agent payoff in one sentence: **the peer trades time for depth
out of the primary's context, and the primary trades the peer's raw notes for a
synthesized answer the operator actually wants.**

## Common Mistakes

**Building this before the single agent is solid.** The biggest one, and the one
[Lessons Learned](./11-lessons-learned.md) warns about directly. If your memory,
hooks, or relay aren't rock-solid for one agent, a second agent doubles your
surface area for bugs and halves your attention for fixing them. Earn the second
agent.

**Using a persistent peer where a sub-agent would do.** If the work fits inside a
session, a sub-agent is free, isolated, and requires zero new infrastructure. A
second persistent agent is justified only by recurrence, restart-survival, or a
need for its own machine. When in doubt, sub-agent.

**Making the broker smart.** The broker should persist and deliver. The moment it
starts interpreting messages, routing on content, or running logic, you've built
a distributed application with no tests. Keep intelligence in the agents.

**Skipping the `wake`/broadcast distinction.** If every message wakes the
recipient, two agents will interrupt each other into uselessness. Directed
actionable messages wake; broadcasts and FYIs land silently. Get this right early.

**Treating peer messages as commands.** A peer is a conversational equal, not a
remote-execution endpoint. Disagree when warranted, check timestamps for
staleness, and never let a peer message override operator direction. This is the
same input-isolation discipline as [Async Relay](./06-async-relay.md) and
[Safety](./08-safety.md), applied between agents.

**Routing every consent gate to the operator.** Defeats the purpose. The primary
agent handles routine consent for the peer; the operator is reserved for
consequential and ambiguous decisions. If the operator is approving the peer's
every file edit, you've added a bottleneck, not removed one.

**Building real-time presence first.** The durable room covers delivery
completely. Presence is a fast path for liveness, not a requirement. Add it only
when its absence is measurably costing you -- and never make it a second source
of truth for message durability.

**No failure path on the messaging layer.** A silent send failure between agents
is as bad as a dropped operator notification. Make `room-send` exit nonzero and
echo the message ID, and wire `[PEER-DOWN]` into your health/escalation system so
the primary notices when its peer falls over.
