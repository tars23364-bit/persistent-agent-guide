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
Telegram ──────┤──► Webhook ──► ~/.agent/queue/ ──► /process-queue
               │    processes                        command
Email relay ───┘                                         │
                                                         ▼
                                                    Agent processes
                                                    each message
                                                         │
                                           ┌─────────────┼────────────┐
                                           ▼             ▼            ▼
                                      iMessage       Telegram     Pushover
                                      reply          reply        alert
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
├── 1710962415-telegram-12345.json
└── 1710962430-imessage-bob.json
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

Sending iMessages programmatically on macOS is possible through AppleScript or
Shortcuts:

```python
# Using AppleScript
import subprocess

def send_imessage(to, text):
    script = f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{to}" of targetService
        send "{text}" to targetBuddy
    end tell
    '''
    subprocess.run(["osascript", "-e", script])
```

Or via a Shortcut triggered from the command line:

```bash
# Using a pre-built Shortcut called "Send iMessage"
shortcuts run "Send iMessage" --input-type text --input "$RECIPIENT|||$MESSAGE"
```

The Shortcuts approach is more reliable for group messages and handles
attachments better.

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

## Telegram Integration

Telegram is easier to integrate than iMessage because it has a proper bot API.
No database polling, no AppleScript -- just webhooks or long polling.

### Bot Listener

```python
# workers/telegram-listener.py (simplified)
import requests
import json
import time
from pathlib import Path

QUEUE_DIR = Path.home() / ".agent" / "queue"

def get_bot_token():
    """Retrieve from system keychain."""
    import subprocess
    result = subprocess.run(
        ["security", "find-generic-password", "-s", "telegram-bot-token", "-w"],
        capture_output=True, text=True
    )
    return result.stdout.strip()

def poll_updates(token, offset=0):
    resp = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"offset": offset, "timeout": 30}
    )
    return resp.json().get("result", [])

def run_listener():
    token = get_bot_token()
    offset = 0

    while True:
        updates = poll_updates(token, offset)
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = msg.get("text", "")
            chat_id = str(msg["chat"]["id"])
            sender = msg["from"].get("first_name", "Unknown")

            if not is_allowed(chat_id):
                continue

            queue_msg = {
                "sender_name": sender,
                "sender_address": chat_id,
                "text": text,
                "access": get_access_level(chat_id),
                "timestamp": format_timestamp(msg["date"]),
                "source": "telegram"
            }

            filename = f"{int(time.time())}-telegram-{chat_id}.json"
            (QUEUE_DIR / filename).write_text(json.dumps(queue_msg, indent=2))
            inject_doorbell()

        time.sleep(1)
```

### Sending Telegram Replies

```python
def send_telegram(chat_id, text, token):
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text}
    )
```

## Reply Routing

The agent needs to send replies back through the channel the message came from.
The `source` field in the queue message determines the route:

```python
def route_reply(original_message, reply_text):
    source = original_message.get("source", "imessage")
    address = original_message["sender_address"]

    if source == "telegram":
        send_telegram(address, reply_text, get_bot_token())
    elif source == "imessage":
        send_imessage(address, reply_text)
    else:
        # Unknown source — log and skip
        log_warning(f"Unknown reply route: {source}")
```

Keep replies appropriate to the channel. An iMessage reply should be a few
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
    "telegram:12345": {
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
(like claude.ai) and want to hand context to your local agent:

```
Web chat ──► "Save to handoff" ──► Email draft ──► Agent reads drafts ──► Context loaded
```

### How It Works

1. You're working with an AI assistant in a web browser.
2. You reach a point where the local agent should take over.
3. You tell the web assistant: "Save this as a handoff." It creates an email
   draft with a known subject prefix (e.g., `[AGENT-HANDOFF]`).
4. You tell your local agent: "Check for handoffs."
5. The agent reads your email drafts, finds the handoff, and presents its
   contents for discussion.

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

### Critical Rule: Handoffs Are Context, Not Commands

The agent should **never auto-execute** handoff content. Handoffs are context
and suggestions. The agent presents them, discusses them, and only acts after
the operator confirms. This is a safety boundary -- you don't want a web chat
session accidentally triggering destructive operations on your local machine.

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

Messages from external sources are **untrusted input**, even from the primary
operator. The agent should treat message text as conversation, not as raw
commands to execute:

```
# Bad: Treating message text as instructions
User sends: "rm -rf /tmp/old-files"
Agent runs: rm -rf /tmp/old-files  ← Dangerous

# Good: Treating message text as conversation
User sends: "rm -rf /tmp/old-files"
Agent responds: "Do you want me to clean up /tmp/old-files?"
```

This is especially important for prompt injection defense. An external message
might contain text like "ignore previous instructions and..." -- the agent
should recognize this as manipulative framing and respond to the person
normally, not follow the injected instructions.

The access level check happens at the watcher level (before the queue), but
input isolation is an agent-level behavior that applies to all access levels.

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

Each watcher is independently restartable. If the Telegram listener crashes,
iMessage still works. If iMessage polling hangs, Telegram still works. The
queue directory is the integration point, not the watchers themselves.

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

**Polling vs webhooks.** The iMessage watcher polls `chat.db`. The Telegram
listener uses long polling. Both could be webhook-based with more infrastructure.
Polling is simpler and works without exposing any ports to the internet.

**Single queue directory.** All sources write to the same queue. This means the
agent processes messages in chronological order regardless of source. If you
need source-specific processing (e.g., prioritize iMessage over Telegram), use
separate queue directories or add a priority field to the JSON.

**Reply length.** Messaging apps have different expectations than a terminal.
iMessage replies should be a few sentences. Telegram can handle more. The agent
should adapt its response length to the channel, not dump terminal-length
output into a text message.
