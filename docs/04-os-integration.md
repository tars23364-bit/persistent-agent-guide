# OS Integration

A persistent agent is not just a chat session. It is a process running on a machine, interacting with the operating system through hooks, daemons, scheduled tasks, and a terminal multiplexer. This document covers how to wire Claude Code into macOS (and by extension Linux) so it behaves like a proper system service.

## The tmux Restart Loop

The foundation of a persistent agent is a tmux session that automatically restarts Claude Code when it exits. Claude Code sessions end for many reasons -- the agent restarts itself, a crash occurs, or the operator issues a shutdown. The restart loop ensures the agent always comes back.

```bash
#!/bin/bash
# auto-tmux.sh -- launches agent in a persistent tmux session

TMUX=/opt/homebrew/bin/tmux
SESSION="agent"
CLAUDE="$HOME/.local/bin/claude"
WORK_DIR="$HOME/your-agent"

# Give USB peripherals time to enumerate after boot
sleep 15

# Ensure state directories exist
mkdir -p ~/.agent/state

# Default voice state: both off after reboot
echo -n "off" > ~/.agent/voice-response
echo -n "off" > ~/.agent/wake-word

# Kill any stale session from a crash
$TMUX kill-session -t "$SESSION" 2>/dev/null

# Create tmux session with restart loop
$TMUX new-session -d -s "$SESSION" -c "$WORK_DIR"
$TMUX send-keys -t "$SESSION" \
  "while true; do \
    $CLAUDE --dangerously-skip-permissions --effort high \
      'Session startup. Run the startup sequence.'; \
    echo 'Session ended. Running summarizer...'; \
    bash ~/your-agent/workers/memory/summarize-sessions.sh 2>/dev/null; \
    echo 'Restarting in 3s...'; \
    sleep 3; \
  done" Enter
```

### Key Design Decisions

**The `while true` loop.** This is the core of persistence. When Claude Code exits (for any reason), the loop catches it, runs a session summarizer, waits 3 seconds, and restarts. The agent is always running unless the tmux session itself is killed.

**Initial prompt.** The agent starts with a fixed prompt ("Session startup. Run the startup sequence.") that triggers the startup hook to gather context. This makes every session start consistent -- the agent always begins by orienting itself.

**The 3-second delay.** Prevents tight restart loops if something is causing immediate crashes. Long enough to see error output in the terminal, short enough that the agent recovers quickly.

**Post-session summarizer.** Between sessions, a script runs to summarize the session transcript. This happens outside the agent's context window, so it costs no tokens. The summary feeds into memory for future sessions.

**Skip-permissions flag.** In a persistent agent context, the operator has pre-approved the agent's access to tools and the filesystem. The agent should not block on permission prompts when no one is at the terminal. Use this flag deliberately -- it grants the agent full tool access.

**Reset defaults on boot.** Voice and wake word toggles reset to "off" on every reboot. The operator enables them when at the desk. This prevents the agent from speaking into an empty room after a reboot.

## launchd Integration

macOS uses launchd for service management. For a persistent agent, you need at least two launchd jobs:

1. **The agent session** -- starts the tmux loop on login
2. **Scheduled tasks** -- cron-like jobs for health checks, maintenance

### Agent Session LaunchAgent

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agent.session</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/you/your-agent/workers/auto-tmux.sh</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/Users/you/.agent/logs/session.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/you/.agent/logs/session.err</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>/Users/you</string>
    </dict>
</dict>
</plist>
```

Place this in `~/Library/LaunchAgents/com.agent.session.plist` and load it:

```bash
launchctl load ~/Library/LaunchAgents/com.agent.session.plist
```

### Scheduled Health Checks

A separate LaunchAgent runs periodic health checks:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agent.healthcheck</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/you/your-agent/workers/cardiac-cycle.sh</string>
    </array>

    <key>StartInterval</key>
    <integer>1800</integer>

    <key>StandardOutPath</key>
    <string>/Users/you/.agent/logs/cardiac-cycle.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/you/.agent/logs/cardiac-cycle.err</string>
</dict>
</plist>
```

### launchd Limitations

launchd is reliable but has quirks:

- **Sleep/shutdown timing.** Jobs scheduled by `StartInterval` do not fire while the machine is asleep. If the Mac sleeps for 8 hours, you do not get 16 missed health checks queued up -- they just do not run. This is usually fine (nothing to check while sleeping), but be aware.
- **Environment.** launchd jobs run with a minimal environment. Always set `PATH` and `HOME` explicitly in the plist. Tools like `jq`, `python3`, or `claude` may not be on the default launchd PATH.
- **Logging.** Use `StandardOutPath` and `StandardErrorPath` for debugging. `launchctl list | grep agent` shows running jobs. `launchctl error` decodes exit codes.
- **User agents vs. system daemons.** Use `~/Library/LaunchAgents/` (user agents) unless you need the job to run before login. System daemons (`/Library/LaunchDaemons/`) have different permissions and lifecycle.

### Linux Alternative: systemd

On Linux, replace launchd with systemd user services:

```ini
# ~/.config/systemd/user/agent-session.service
[Unit]
Description=Agent tmux session

[Service]
Type=forking
ExecStart=/bin/bash /home/you/your-agent/workers/auto-tmux.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable agent-session
systemctl --user start agent-session
```

The concepts are the same -- the init system starts the tmux loop, which starts the agent.

## Hooks

Hooks are the backbone of OS integration. Claude Code fires hooks at specific lifecycle events, and your scripts respond. Without hooks, Claude Code is a chat. With hooks, it is a system.

### Hook Types

| Hook | Fires When | Receives | Use Cases |
|------|-----------|----------|-----------|
| `SessionStart` | Session begins | Session metadata (JSON stdin) | Context injection, state reset, attunement |
| `Stop` | Agent finishes a response | Response content (JSON stdin) | TTS, relay forwarding, logging |
| `UserPromptSubmit` | User sends a message | Prompt metadata | Context warnings, session elapsed tracking |
| `PostToolUse` | After a tool executes | Tool name, exit code, output | Error logging, activity tracking |
| `PreCompact` | Before auto-compaction | Session metadata | Flag file for recovery |
| `Statusline` | Every turn (display) | Context window metrics | Bridge file, tmux display |

### Hook Configuration

Hooks are configured in Claude Code's settings. Each hook specifies:
- The event it responds to
- The script to run
- Optional matchers (e.g., only fire on specific tool names)

```json
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "python3 /Users/you/your-agent/hooks/session-startup.py"
      },
      {
        "type": "command",
        "command": "bash /Users/you/your-agent/hooks/compact-recovery-inject.sh"
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "bash /Users/you/your-agent/hooks/stop-tts.sh"
      }
    ],
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "bash /Users/you/your-agent/hooks/context-threshold.sh"
      },
      {
        "type": "command",
        "command": "bash /Users/you/your-agent/hooks/session-elapsed.sh"
      }
    ],
    "PostToolUse": [
      {
        "type": "command",
        "command": "bash /Users/you/your-agent/hooks/activity-log.sh"
      },
      {
        "type": "command",
        "command": "bash /Users/you/your-agent/hooks/error-detector.sh",
        "matcher": { "tool_name": "Bash" }
      }
    ],
    "PreCompact": [
      {
        "type": "command",
        "command": "bash /Users/you/your-agent/hooks/precompact-flag.sh"
      }
    ]
  }
}
```

### The Startup Hook in Detail

The `SessionStart` hook is the most important hook. It gathers all startup context and injects it into the agent's first turn, eliminating the need for the agent to make tool calls to orient itself.

The startup hook:

1. **Reads file-based state** -- voice toggles, system flags
2. **Reads the handoff** -- task context from the previous session (if present)
3. **Reads the pulse** -- today's session summaries (if any)
4. **Extracts recent exchanges** -- the tail of the last session transcript
5. **Runs attunement queries** -- graph memory queries for operator awareness
6. **Checks for brief availability** -- morning start flag
7. **Runs cold-start backfills** -- missed reflections, stale backups, git sync
8. **Injects everything as `additionalContext`**

The hook distinguishes between cold starts (no handoff, no pulse) and warm starts (handoff or pulse present). Cold starts get more context because the agent needs more orientation. Warm starts are lean because the handoff already provides task context.

```python
# Determine startup type
is_cold_start = not handoff and not pulse

# Cold start: run graph memory recall for background context
if is_cold_start:
    recall = run_memory_recall()
    if recall:
        parts.append(f"## Background\n{recall}")

# Cold start: check for missed maintenance
if is_cold_start:
    # Run yesterday's reflection if missing
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    if not reflection_exists(yesterday):
        subprocess.Popen(["bash", reflect_script, yesterday], ...)

    # Run graph memory backup if stale
    if not backup_exists_today():
        subprocess.Popen(["python3", backup_script], ...)

    # Push any unpushed git commits
    subprocess.Popen(["bash", "-c", "cd ~/your-agent && git push"], ...)
```

Cold-start backfills run as background processes (Popen) so they do not block the hook. The agent starts immediately; the maintenance happens in parallel.

### The Stop Hook

The `Stop` hook fires after every agent response. Its primary uses:

1. **TTS forwarding** -- POST the response text to a TTS daemon for speech output
2. **Relay forwarding** -- POST to a relay service for external messaging
3. **Logging** -- record response metadata

```bash
#!/bin/bash
# stop-tts.sh -- POST response to TTS daemon

# Skip if TTS is suppressed
[ -f ~/.agent/tts-suppress ] && exit 0

BODY=$(cat)

# POST to TTS daemon (fire-and-forget)
echo "$BODY" | curl -s -X POST http://localhost:7700/speak \
  -H "Content-Type: application/json" \
  -d @- -o /dev/null \
  --connect-timeout 1 --max-time 2 2>/dev/null &

wait
exit 0
```

The TTS daemon runs as a separate process (its own LaunchAgent). The hook just forwards the response text. If the daemon is down, the curl times out silently. This decoupling means TTS failures never block the agent.

### The Activity Logger

A `PostToolUse` hook logs every tool execution to a JSONL file:

```bash
#!/bin/bash
# activity-log.sh -- logs tool executions

LOG_DIR="$HOME/.agent/logs"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/$TODAY.jsonl"
mkdir -p "$LOG_DIR"

PAYLOAD=$(cat)
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TOOL_NAME=$(echo "$PAYLOAD" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('tool_name','unknown'))" \
  2>/dev/null)

python3 -c "
import json, sys
entry = {
    'timestamp': '$TIMESTAMP',
    'tool': '$TOOL_NAME',
    'outcome': 'success'
}
with open('$LOG_FILE', 'a') as f:
    f.write(json.dumps(entry) + '\n')
"
```

These logs are invaluable for debugging, usage tracking, and reflection. One JSONL file per day, structured enough to query with `jq`.

### The Error Detector

A specialized `PostToolUse` hook that only fires on Bash tool failures (non-zero exit codes). The critical design choice is the noise filter -- most non-zero exits are benign (grep finding nothing, SSH connection timeouts, "already installed" messages). The hook skips these and only logs genuine, actionable errors to a learnings file. Logged errors are reviewed during reflection cycles.

## The Health Check Pattern

A launchd cron runs a health check script periodically (e.g., every 30 minutes). The script runs in bash -- zero token cost -- and only involves the agent when something is wrong.

### Design

```
launchd (every 30 min)
  → cardiac-cycle.sh
    → checks: processes, disk, memory, services
    → healthy: log CARDIAC_OK, exit silently
    → unhealthy: inject [CARDIAC_ALERT] into tmux
      → agent spawns subagent to investigate
```

The key insight: the health check itself costs nothing. It is a bash script that checks process lists, disk usage, and service status. Only when something fails does it involve the agent (by injecting a message into the tmux session).

### Injecting Into tmux

When a health check (or any external process) needs the agent's attention, it sends keys to the tmux session:

```bash
# Inject an alert into the agent's tmux session
tmux send-keys -t agent "[CARDIAC_ALERT] TTS daemon not responding. \
  Last seen 45 min ago. PID file exists but process is gone." Enter
```

The agent sees this as a user message and responds according to its rules -- typically by spawning a subagent to diagnose and attempt a fix.

### Escalation Ladder

Define how the agent handles health issues:

```markdown
## Escalation

1. **Auto-fix** (silent): restart service, clear stale flags -- up to
   3 attempts.
2. **Soft push**: send notification -- "Tried to fix X, still broken
   after 3 attempts."
3. **Hard stop**: send notification -- "Needs your hands. Stopped trying."
```

This prevents the agent from endlessly retrying a fix that requires human intervention, and it prevents it from bothering the operator with issues it can fix itself.

## Autonomous System Control

A persistent agent should be able to restart itself -- both the session and (with appropriate safeguards) the underlying machine.

### Session Restart

Session restarts are cheap and fast. The agent writes a handoff, exits Claude Code, and the tmux restart loop catches it and starts a new session.

When to self-restart:
- Context window getting long and quality is degrading
- Stuck state that a fresh session would resolve
- After completing a large task, to start clean
- Tool or MCP server in a bad state

The restart protocol:
1. Write the handoff file (what was happening, what is next)
2. Write any pending memories to graph storage
3. Write a pulse entry for the session
4. Exit (the restart loop handles the rest)

### Machine Restart

Machine restarts are heavier and should be rare. The agent should:
1. Write the handoff
2. Send a push notification to the operator (they may have other processes running)
3. Issue the restart command

When to self-restart the machine:
- System-level issues (memory pressure, USB failures, launchd problems)
- After OS updates that require a reboot
- Hardware peripherals not responding after software troubleshooting

The important rule: **troubleshoot first, restart when it is the right tool.** A machine restart is never the first response to a problem.

## File System Layout

A persistent agent creates files across two locations. Keep them separate:

**Source directory (`~/your-agent/`)** -- version-controlled. Contains the agent's code: `CLAUDE.md`, `.claude/rules/`, `hooks/`, `workers/`, `scripts/`, `skills/`, `commands/`, and `agents/`. This is the agent's codebase.

**State directory (`~/.agent/`)** -- not version-controlled. Contains runtime state (`state/context.json`, `state/attunement.md`), logs (`logs/*.jsonl`), learnings (`learnings/ERRORS.md`, `learnings/LEARNINGS.md`), and session files (`handoff.md`, `today-pulse.md`, toggle files like `voice-response`).

The split keeps the git repo clean. Logs, learnings, and runtime state never pollute version control. Hooks in the source directory read and write files in the state directory.

## Putting It Together: The Full Lifecycle

Here is how a complete session lifecycle works with all the OS integration pieces:

```
1. MACHINE BOOT
   └─ launchd starts com.agent.session
      └─ auto-tmux.sh runs
         ├─ resets voice/wake toggles to off
         ├─ kills stale tmux session
         └─ creates new tmux session with restart loop

2. SESSION START
   └─ claude starts with initial prompt
      └─ SessionStart hooks fire:
          ├─ session-startup.py: gathers all context
          │   ├─ reads file-based state
          │   ├─ reads handoff (if present)
          │   ├─ reads pulse (if today has entries)
          │   ├─ extracts last session tail
          │   ├─ runs attunement queries
          │   ├─ cold start: runs memory recall
          │   ├─ cold start: backfills missed maintenance
          │   └─ injects everything as additionalContext
          └─ compact-recovery-inject.sh: checks for compaction flag

3. CONVERSATION
   └─ each turn:
       ├─ UserPromptSubmit hooks fire:
       │   ├─ context-threshold.sh: injects warnings if needed
       │   └─ session-elapsed.sh: nudges reflection if session is long
       ├─ agent responds
       ├─ Stop hooks fire:
       │   └─ stop-tts.sh: forwards response to TTS daemon
       └─ PostToolUse hooks fire (on tool calls):
           ├─ activity-log.sh: logs tool execution
           └─ error-detector.sh: logs significant errors

4. BACKGROUND (every 30 min)
   └─ launchd fires cardiac-cycle.sh
       ├─ healthy: log and exit
       └─ unhealthy: inject [CARDIAC_ALERT] into tmux

5. SESSION END
   └─ agent writes handoff, exits
      └─ restart loop catches exit
          ├─ runs session summarizer
          ├─ waits 3 seconds
          └─ starts new claude session (back to step 2)
```

## Managing Workers at Scale

Once you have more than a handful of launchd services, you need a way to
manage them without memorizing service labels. A slash command that wraps
`launchctl` operations gives the agent (and the operator) a clean interface.

### The `/worker` Command Pattern

Create a command file that teaches the agent how to manage your worker fleet:

```markdown
# /worker -- Worker Operations

## Usage
- `/worker` or `/worker status` -- show all workers and their state
- `/worker restart <name>` -- restart a specific worker
- `/worker logs <name>` -- tail recent logs
- `/worker stop <name>` -- stop a worker
- `/worker start <name>` -- start a worker

## Implementation
launchctl list | grep com.agent  -- show all services
launchctl unload <plist>         -- stop a service
launchctl load <plist>           -- start a service

## Reading the Output
- PID column: a number means running, `-` means loaded but not active
- Exit status: 0 = clean exit, non-zero = crashed
- Report naturally: "TTS daemon restarted, PID 1234"
```

### Worker Inventory

Include a table of known workers in the command file so the agent knows
what each service does:

```markdown
| Service | Label | Description |
|---------|-------|-------------|
| TTS Daemon | com.agent.tts-daemon | Text-to-speech on localhost |
| Presence | com.agent.presence | Camera-based presence detection |
| Message Watcher | com.agent.msg-watcher | Queue processing |
| Health Check | com.agent.healthcheck | Cardiac cycle monitoring |
```

This inventory also serves as documentation — when something breaks at 2 AM,
the agent knows which service label maps to which function without grepping
through plist files.

## Common Pitfalls

**Forgetting launchd environment.** launchd jobs do not inherit your shell environment. If a hook works in your terminal but not via launchd, the PATH is probably wrong. Always set it explicitly.

**Blocking hooks.** Hooks should be fast. If a hook takes more than a few seconds, it delays the agent's response. Use background processes (`&` or `subprocess.Popen`) for anything slow and non-blocking.

**Not handling missing files.** Every hook that reads a file should handle the case where the file does not exist. State files get deleted, moved, or never created. Check first or use defaults.

**Tight restart loops.** If the agent crashes immediately on startup, the restart loop will cycle every 3 seconds forever. Add a crash counter or exponential backoff if this is a concern. In practice, the 3-second delay is usually sufficient.

**Race conditions with state files.** Multiple hooks can fire near-simultaneously. If two hooks write to the same state file, you get corruption. Design state files with single-writer ownership -- one hook writes, many hooks read.

**Over-engineering the health check.** The health check should be a bash script that runs in milliseconds. It checks process lists, disk usage, and file timestamps. It does not need a database, an API, or a framework. Keep it simple -- complexity here is a liability, not an asset.
