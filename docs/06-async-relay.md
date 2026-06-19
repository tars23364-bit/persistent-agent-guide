# Async Relay

A persistent agent running in a tmux session on a headless Mac is powerful, but
it has one fundamental limitation: you have to be at the terminal to interact
with it. Async relay solves this by bridging the agent to communication channels
you already use -- messaging apps, email, push notifications.

This chapter covers the relay architecture: message ingestion, queue-based
processing, cross-platform handoffs, and push notification alerting.

## The Problem

Your agent runs in a terminal. You're not always at the terminal. You might be:

- Away from your desk but need to ask your agent something
- At a different computer working in a web-based AI chat
- On your phone and want a quick status update
- Asleep while the agent monitors something overnight

Without async relay, the agent is effectively offline whenever you're not
staring at the tmux pane. With it, the agent becomes reachable from anywhere
you can send a text message.

## Architecture

```
External Sources                Queue                    Agent
────────────────               ─────                    ─────
iMessage ──────┐
               │    Watcher/     JSON files
Other channel ─┤──► Webhook ──► ~/.agent/queue/ ──► /process-queue
               │    processes                        command
Email/bridge ──┘                                         │
                                                         ▼
                                                    Agent processes
                                                    each message
                                                         │
                                           ┌─────────────┼────────────┐
                                           ▼             ▼            ▼
                                      iMessage       Channel      Pushover
                                      reply          reply         alert
```

The pattern has three stages:
1. **Ingestion** -- external messages land in a file queue
2. **Processing** -- the agent reads the queue and responds
3. **Reply routing** -- responses go back through the original channel

## Message Queue

The queue is a directory of JSON files. Each file represents one inbound
message:

```
~/.agent/queue/
├── 1710962400-imessage-alice.json
├── 1710962415-imessage-bob.json
└── 1710962430-channel-12345.json
```

Filename convention: `{unix_timestamp}-{source}-{sender_id}.json`

Each file has a consistent schema:

```json
{
  "sender_name": "Alice",
  "sender_address": "+15551234567",
  "text": "Hey, can you check if the backup ran last night?",
  "access": "full",
  "timestamp": "2026-03-15T14:00:00",
  "source": "imessage"
}
```

### Why Files, Not a Database

File-based queues have several advantages for this use case:

- **Atomic writes.** Write to a temp file, rename into the queue directory. No
  partial reads.
- **Easy inspection.** `ls` and `cat` are your debugging tools. No query
  language needed.
- **Processing is deletion.** Move the file to `processed/` when done. If the
  agent crashes mid-processing, unprocessed files are still in the queue on
  restart.
- **No dependencies.** No Redis, no SQLite, no message broker. Just a
  directory.

### Queue Processing

The agent processes the queue via a slash command or automatic trigger:

```bash
# Process all queued messages
process_queue() {
    local queue_dir="$HOME/.agent/queue"
    local processed_dir="$queue_dir/processed"
    mkdir -p "$processed_dir"

    # Sort by filename (timestamp-prefixed) for chronological order
    for msg_file in "$queue_dir"/*.json; do
        [[ -f "$msg_file" ]] || continue
        echo "Processing: $(basename "$msg_file")"
        # Agent reads and responds to each message
        # Then moves to processed/
        mv "$msg_file" "$processed_dir/"
    done
}
```

When multiple messages arrive from the same sender, batch them -- read all
messages before responding, so the agent has full context rather than answering
each message in isolation.

## iMessage Integration

On macOS, iMessage integration is possible through the `chat.db` SQLite
database that Messages.app maintains.

### Watcher Pattern

A background process polls `chat.db` for new messages and writes them to the
queue:

```python
# workers/imessage-watcher.py (simplified)
import sqlite3
import json
import time
from pathlib import Path

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
QUEUE_DIR = Path.home() / ".agent" / "queue"
LAST_SEEN = Path.home() / ".agent" / "imessage-last-seen"

def get_new_messages(since_rowid):
    conn = sqlite3.connect(str(CHAT_DB))
    cursor = conn.execute("""
        SELECT
            m.ROWID,
            m.text,
            h.id as sender,
            m.date
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        WHERE m.ROWID > ?
          AND m.is_from_me = 0
          AND m.text IS NOT NULL
        ORDER BY m.ROWID
    """, (since_rowid,))
    messages = cursor.fetchall()
    conn.close()
    return messages

def poll_loop():
    last_rowid = int(LAST_SEEN.read_text().strip()) if LAST_SEEN.exists() else 0

    while True:
        messages = get_new_messages(last_rowid)
        for rowid, text, sender, date in messages:
            if not is_allowed(sender):
                continue

            msg = {
                "sender_name": resolve_name(sender),
                "sender_address": sender,
                "text": text,
                "access": get_access_level(sender),
                "timestamp": format_date(date),
                "source": "imessage"
            }

            # Write to queue
            filename = f"{int(time.time())}-imessage-{sanitize(sender)}.json"
            path = QUEUE_DIR / filename
            path.write_text(json.dumps(msg, indent=2))

            # Signal the agent
            inject_doorbell()

            last_rowid = rowid
            LAST_SEEN.write_text(str(last_rowid))

        time.sleep(5)  # Poll interval
```

### Sending Replies

Sending iMessages programmatically on macOS is most reliably done via a
pre-built Shortcut. AppleScript works for simple cases but is fragile for
group chats and attachments:

```bash
# Using a per-recipient Shortcut (e.g., "Send iMessage to Alice")
shortcuts run "Send iMessage to Alice" --input-type text --input "$MESSAGE"
```

The per-recipient Shortcut pattern is preferable to a single generic Shortcut
with a combined `RECIPIENT|||MESSAGE` payload: it makes the send action explicit,
keeps credential routing out of the command line, and is easier to audit.

A Python helper that wraps the `shortcuts run` call:

```python
import subprocess

def send_imessage(shortcut_name: str, text: str) -> None:
    """Send via a named Shortcuts shortcut."""
    subprocess.run(
        ["shortcuts", "run", shortcut_name, "--input-type", "text", "--input", text],
        check=True
    )
```

Build one shortcut per recipient that matters (primary operator, household
members). For unknown senders the agent declines rather than attempting to
route a reply.

### Doorbell Pattern

When a new message lands in the queue, the watcher "rings the doorbell" --
injecting a trigger into the agent's tmux session:

```python
def inject_doorbell():
    """Tell the agent there are messages waiting."""
    subprocess.run([
        "tmux", "send-keys", "-t", "agent",
        "/process-queue", "Enter"
    ])
```

This triggers the agent's queue-processing command automatically. The agent
reads the queue, processes messages, sends replies, and returns to whatever it
was doing.

## Other Messaging Channels

The queue pattern is channel-agnostic. Any source that can write a JSON file
to the queue directory and inject a doorbell into the agent's tmux session can
participate. Common additions:

- **Messaging APIs with webhooks** (e.g., Telegram Bot API, Slack) -- easier
  than iMessage because they provide proper HTTP APIs. Long-polling or webhooks
  both work; the watcher just writes the normalized JSON to the queue directory.
- **Email relay** -- an IMAP IDLE watcher can surface specific sender/subject
  patterns as queue messages, useful for automated system alerts from external
  services.

The normalization step is the key: every source writes the same JSON schema
(`sender_name`, `sender_address`, `text`, `access`, `source`, `timestamp`) so
the agent's processing logic doesn't need to know which channel a message came
from.

## Reply Routing

The agent sends replies back through the channel the message came from.
The `source` field in the queue message determines the route:

```python
def route_reply(original_message, reply_text):
    source = original_message.get("source", "imessage")
    address = original_message["sender_address"]

    if source == "imessage":
        send_imessage(address, reply_text)
    else:
        # Channel-specific send -- implement per source
        log_warning(f"No reply route implemented for: {source}")
```

Keep replies appropriate to the channel. A messaging reply should be a few
sentences, not a terminal dump. If the agent's natural response is long, it
should summarize for the messaging channel and note "full details in the
terminal."

## Access Control

Not everyone who messages you should have full access to your agent. The
allowlist pattern:

```json
{
  "contacts": {
    "+15551234567": {
      "name": "Primary Operator",
      "access": "full"
    },
    "+15559876543": {
      "name": "Household Member",
      "access": "household"
    },
    "channel:12345": {
      "name": "Colleague",
      "access": "conversation"
    }
  },
  "default_access": "blocked"
}
```

### Access Levels

| Level | Capabilities |
|-------|-------------|
| `full` | Everything. Direct instructions, system commands, all skills. |
| `household` | Conversation, specific trigger phrases (e.g., grocery commands), relay messages to the operator. No system access. |
| `conversation` | Chat only. The agent responds conversationally but won't execute commands or access system functions. |
| `blocked` | Message is silently dropped. Not queued. |

The watcher checks access level before writing to the queue. Blocked messages
never reach the agent.

For `household` and `conversation` levels, the agent should politely decline
out-of-scope requests: "That's something I'd need to check with the operator
on -- want me to pass it along?"

## Handoff Pipeline

The relay concept extends beyond messaging. When you use a web-based AI chat
and want to hand context to your local agent, two patterns are in common use:

### Pattern A: Gmail Draft Relay

```
Web chat ──► "Save to handoff" ──► Gmail draft ──► Agent reads drafts ──► Context loaded
```

1. You're working with an AI assistant in a web browser.
2. You reach a point where the local agent should take over.
3. You tell the web assistant: "Save this as a handoff." It saves a Gmail draft
   with a known subject prefix (e.g., `[AGENT-HANDOFF]`).
4. You tell your local agent: "Check for handoffs."
5. The agent reads Gmail drafts, finds the handoff, presents its contents, and
   discusses before acting.

```python
# Checking for handoffs (simplified)
def check_handoffs():
    """Search Gmail drafts for handoff markers."""
    drafts = gmail_api.search_drafts("subject:[AGENT-HANDOFF]")
    handoffs = []
    for draft in drafts:
        content = gmail_api.read_draft(draft["id"])
        handoffs.append({
            "id": draft["id"],
            "subject": content["subject"],
            "body": content["body"],
            "timestamp": content["date"]
        })
    return handoffs
```

### Pattern B: Live Bridge Watcher

For real-time bidirectional relay -- useful when the local agent and a web chat
are collaborating on an ongoing task rather than doing a one-time handoff:

```
Local agent writes to outbox/ ──► Watcher injects into web chat
Web chat replies with flag   ──► Watcher writes to inbox/ ──► Doorbell to local agent
```

The watcher connects to the browser via a debugging protocol (CDP), polls the
web chat DOM for flagged responses, and delivers them to the agent's inbox
directory. The agent reads inbox files, processes the content, and writes reply
files to the outbox for the watcher to inject.

A protocol convention makes this reliable:
- **Outbound prefix**: local agent messages start with a known prefix (e.g., `[Agent]`)
- **Inbound flag**: web chat ends responses with a known flag (e.g., `[/agent]`) on its own line
- **Streaming-safe**: placing the flag at the end means incomplete streamed messages are never delivered

### Critical Rule: Handoffs and Bridge Messages Are Context, Not Commands

The agent should **never auto-execute** handoff or bridge content. These are
context and suggestions from a different session or interface. The agent presents
them, discusses them, and only acts after the operator confirms. This is a
hard safety boundary -- you don't want a web chat session accidentally triggering
destructive operations on the local machine.

The bridge watcher pattern also needs a role framing: the local agent is the
**lead** and the web chat is a **collaborator** -- a brainstorming partner or
reasoning engine for questions that don't need tool access. The web chat doesn't
supervise or command; it advises.

## Push Notifications

The agent needs to reach you when something goes wrong and you're not at the
terminal. Push notifications serve this role.

### Pushover Pattern

[Pushover](https://pushover.net/) is a simple push notification service with a
clean API. It's ideal for agent alerts:

```python
import requests

def send_push(title, message, priority="normal"):
    """Send a push notification to the operator's devices."""
    priorities = {"low": -1, "normal": 0, "high": 1, "critical": 2}

    requests.post("https://api.pushover.net/1/messages.json", data={
        "token": get_api_key("pushover-app-token"),
        "user": get_api_key("pushover-user-key"),
        "title": title,
        "message": message,
        "priority": priorities.get(priority, 0)
    })
```

### When to Push

Not every event deserves a notification. Rate-limit by alert type:

```python
from pathlib import Path
import time

COOLDOWN_DIR = Path.home() / ".agent" / "push-cooldowns"
COOLDOWN_SECONDS = 900  # 15 minutes

def should_push(alert_type):
    """Rate limit: one push per alert type per cooldown period."""
    COOLDOWN_DIR.mkdir(exist_ok=True)
    cooldown_file = COOLDOWN_DIR / f"{alert_type}.last"

    if cooldown_file.exists():
        last_push = float(cooldown_file.read_text().strip())
        if time.time() - last_push < COOLDOWN_SECONDS:
            return False

    cooldown_file.write_text(str(time.time()))
    return True
```

Use push notifications for:
- **Critical alerts** -- service down, disk full, security events
- **Escalated issues** -- agent tried to auto-fix something 3 times and failed
- **Operator-requested notifications** -- "let me know when the backup finishes"

Don't push for:
- Routine status updates
- Heartbeat check-ins (log those silently)
- Anything the operator will see when they return to the terminal

### Escalation Ladder

A good pattern for alert handling:

```
1. Auto-fix (silent)     → Agent tries to resolve the issue itself.
                           Up to 3 attempts. No notification.

2. Soft push             → "Tried to fix X, still broken after 3 attempts."
                           Priority: high. Operator should look when convenient.

3. Hard stop             → "Needs your hands. Stopped trying."
                           Priority: critical. Operator needs to intervene.
```

This prevents notification fatigue. Most issues get resolved silently. Only
persistent problems reach the operator.

## Input Isolation

Messages from external sources are **untrusted data, never instructions** --
this holds even for the primary operator via a messaging channel. The agent
treats message text as conversation, not as raw commands to execute:

```
# Bad: Treating message text as instructions
User sends: "rm -rf /tmp/old-files"
Agent runs: rm -rf /tmp/old-files  ← Dangerous

# Good: Treating message text as conversation
User sends: "rm -rf /tmp/old-files"
Agent responds: "Do you want me to clean up /tmp/old-files?"
```

This principle extends to prompt injection defense. An external message might
contain text like "ignore previous instructions and..." -- the agent should
recognize this as manipulative framing, drop the injection attempt, and respond
to the person normally.

**The rule applies at all access levels.** Even `full` access via a messaging
channel means the operator's instructions via that channel are conversation
inputs subject to normal agent judgment -- not literal commands that bypass the
agent's reasoning. The distinction: terminal input from the operator gets direct
execution; messaging channel input gets interpreted conversationally regardless
of access level.

The access level check happens at the watcher (before the queue). Input
isolation is enforced by the agent during processing. Both layers are necessary.

## TTS Suppression During Queue Processing

When processing queued messages, the agent should suppress voice output. The
operator sent a text message -- they expect a text reply, not audio playback
on a machine they're not sitting at:

```bash
# Before processing queue
touch "$HOME/.agent/tts-suppress"

# Process all messages...

# After processing
rm -f "$HOME/.agent/tts-suppress"
```

The voice pipeline's Stop hook checks for this flag and skips TTS when it's
present (see [Chapter 5](05-voice-pipeline.md)).

## Running the Watchers

Each message source runs as an independent background process via LaunchAgent:

```xml
<!-- com.your-agent.imessage-watcher.plist -->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.your-agent.imessage-watcher</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/your-agent/workers/imessage-watcher.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

Each watcher is independently restartable and independently survivable. If one
source's watcher crashes, the others continue unaffected. The queue directory
is the integration point, not the watchers themselves -- any watcher can write
to it and any failure is scoped to that source.

## Monitoring Queue Health

A simple health check for the relay system:

```bash
#!/bin/bash
# Check for stale messages in the queue
QUEUE_DIR="$HOME/.agent/queue"
STALE_THRESHOLD=300  # 5 minutes

for f in "$QUEUE_DIR"/*.json; do
    [[ -f "$f" ]] || continue
    # macOS uses `stat -f %m`, Linux uses `stat -c %Y`
    if [[ "$(uname)" == "Darwin" ]]; then
        age=$(( $(date +%s) - $(stat -f %m "$f") ))
    else
        age=$(( $(date +%s) - $(stat -c %Y "$f") ))
    fi
    if (( age > STALE_THRESHOLD )); then
        echo "STALE: $(basename "$f") — ${age}s old"
    fi
done
```

Include this in your heartbeat/health check system. If messages are sitting in
the queue for more than a few minutes, either the agent isn't processing them
or the doorbell injection failed.

## Design Decisions and Trade-offs

**File queue vs message broker.** A file queue is simpler, has no dependencies,
and is easy to debug. A proper message broker (Redis, RabbitMQ) adds
reliability guarantees like exactly-once delivery. For a single-operator agent,
the file queue is sufficient. If you need multi-consumer processing, upgrade.

**Polling vs webhooks.** The iMessage watcher polls `chat.db`. Messaging
platform watchers can use long polling or webhooks depending on what the API
offers. Polling is simpler and works without exposing ports to the internet;
webhooks add complexity but reduce latency and polling overhead.

**Single queue directory.** All sources write to the same queue. This means the
agent processes messages in chronological order regardless of source. If you
need source-specific processing (e.g., prioritize one channel over another), use
separate queue directories or add a priority field to the JSON.

**Reply length.** Messaging apps have different expectations than a terminal.
Keep messaging replies to a few sentences. The agent should adapt its response
length to the channel, not dump terminal-length output into a text message.
