# OS Integration

A persistent agent is not just a chat session. It is a process running on a machine, interacting with the operating system through hooks, daemons, scheduled tasks, and a terminal multiplexer. This document covers how to wire Claude Code into macOS (and by extension Linux) so it behaves like a proper system service.

## The tmux Restart Loop

The foundation of a persistent agent is a tmux session that automatically restarts Claude Code when it exits. Claude Code sessions end for many reasons -- the agent restarts itself, a crash occurs, or the operator issues a shutdown. The restart loop ensures the agent always comes back.

Split this into two scripts: `auto-tmux.sh` handles boot setup and launches the tmux session; `claude-loop.sh` is the actual restart loop running inside tmux. This separation keeps the model pin, circuit breaker, and summarizer in one place (the loop) while the boot setup (voice resets, VPN reconnect) stays in auto-tmux.sh.

```bash
#!/bin/bash
# auto-tmux.sh -- launches agent in a persistent tmux session on boot

TMUX=/opt/homebrew/bin/tmux
SESSION="agent"
WORK_DIR="$HOME/your-agent"
LOOP="$WORK_DIR/workers/claude-loop.sh"

# Give USB peripherals time to enumerate after boot
sleep 15

# Ensure state directories exist
mkdir -p ~/.agent/state ~/.agent/logs

# Default voice state: both off after reboot
echo -n "off" > ~/.agent/voice-response
echo -n "off" > ~/.agent/wake-word

# Kill any stale session from a crash
$TMUX kill-session -t "$SESSION" 2>/dev/null

# Create tmux session and hand off to claude-loop
$TMUX new-session -d -s "$SESSION" -c "$WORK_DIR"
$TMUX send-keys -t "$SESSION" "bash $LOOP" Enter

# Boot-resilience: verify the session survived the first few seconds.
# The tmux server can die seconds after a successful create on flaky boot,
# leaving the agent dark for hours with nothing noticing. Re-check and
# rebuild once; page out-of-band if it still won't stay up.
LOG="$HOME/.agent/logs/auto-tmux.log"
sleep 5
if ! $TMUX has-session -t "$SESSION" 2>/dev/null; then
    echo "[$(date '+%Y-%m-%dT%H:%M:%S')] session vanished after create — rebuilding once" >> "$LOG"
    $TMUX new-session -d -s "$SESSION" -c "$WORK_DIR"
    $TMUX send-keys -t "$SESSION" "bash $LOOP" Enter
    sleep 5
    if ! $TMUX has-session -t "$SESSION" 2>/dev/null; then
        echo "[$(date '+%Y-%m-%dT%H:%M:%S')] session STILL missing — paging operator" >> "$LOG"
        # Send an out-of-band push notification if you have one wired up
        # python3 ~/your-agent/scripts/notify.py \
        #   --title "Agent boot FAILED" \
        #   --message "tmux session would not stay up. Manual start needed."
    fi
fi
```

```bash
#!/bin/bash
# claude-loop.sh -- restart-on-exit loop with circuit breaker.
# Runs INSIDE the tmux session (launched by auto-tmux.sh).

CLAUDE="$HOME/.local/bin/claude"
MODEL="claude-opus-4-8"  # pin the model here; settings.json is overridden by this flag
PROMPT="Session startup. Run the startup sequence."

LOG="$HOME/.agent/logs/claude-loop.log"
STDERR_LOG="$HOME/.agent/logs/claude-loop-stderr.log"

# Circuit breaker thresholds
RAPID_THRESHOLD_SEC=30   # iterations shorter than this count as rapid exit
BACKOFF_MAX_SEC=300      # cap on exponential backoff
HARD_STOP_COUNT=10       # consecutive rapid exits before giving up

export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90

rapid_count=0

while true; do
    ITER_START=$(date +%s)
    # Write a loop-tick file so the health check can verify the supervisor is alive
    touch "$HOME/.agent/state/claude-loop-tick"
    echo "[LOOP] $(date '+%Y-%m-%d %H:%M:%S') starting claude (rapid_count=$rapid_count)" >> "$LOG"

    "$CLAUDE" --dangerously-skip-permissions --model "$MODEL" --effort high "$PROMPT" \
        2>> "$STDERR_LOG"
    CLAUDE_RC=$?

    ITER_DUR=$(( $(date +%s) - ITER_START ))
    echo "[LOOP] $(date '+%Y-%m-%d %H:%M:%S') exited rc=$CLAUDE_RC dur=${ITER_DUR}s" >> "$LOG"

    if [ "$ITER_DUR" -lt "$RAPID_THRESHOLD_SEC" ]; then
        rapid_count=$((rapid_count + 1))
        echo "[LOOP] rapid-exit ($rapid_count consecutive)" >> "$LOG"

        if [ "$rapid_count" -ge "$HARD_STOP_COUNT" ]; then
            echo "[LOOP] hard stop: $rapid_count rapid exits — giving up" >> "$LOG"
            # python3 ~/your-agent/scripts/notify.py \
            #   --title "Agent loop hard stop" \
            #   --message "$rapid_count consecutive rapid exits. Loop stopped."
            exit 1
        fi

        # Exponential backoff: 3s, 6s, 12s, 24s, ... capped
        backoff=$(( 3 * (1 << (rapid_count - 1)) ))
        [ "$backoff" -gt "$BACKOFF_MAX_SEC" ] && backoff=$BACKOFF_MAX_SEC
        echo "[LOOP] backing off ${backoff}s before retry" >> "$LOG"
        sleep "$backoff"
    else
        rapid_count=0
        echo 'Session ended. Running summarizer...'
        bash ~/your-agent/workers/memory/summarize-sessions.sh 2>/dev/null
        echo 'Restarting in 3s...'
        sleep 3
    fi
done
```

### Key Design Decisions

**Split auto-tmux / claude-loop.** `auto-tmux.sh` is the boot script (launchd target). `claude-loop.sh` is the restart loop running inside tmux. This means the model pin, circuit breaker, and summarizer live in one focused file. Changing the model is a one-line edit to `claude-loop.sh`.

**Circuit breaker.** A bare `while true` loop is a liability when the agent is crashing on startup. The circuit breaker detects rapid exits (< 30 seconds), applies exponential backoff (3s, 6s, 12s, ... capped at 5 minutes), and hard-stops after 10 consecutive failures with an operator page. This is the difference between a recoverable crash and an infinite spin.

**Loop-tick file.** On each iteration the loop touches `~/.agent/state/claude-loop-tick`. The health check script reads this file to verify the loop supervisor itself is alive -- separate from verifying the tmux session exists.

**Boot-resilience verify-retry.** After creating the tmux session, `auto-tmux.sh` waits 5 seconds and checks that the session is still alive. On flaky boot (power flicker, USB enumeration races) the tmux server can die seconds after creation, leaving the agent dark with no notification. One rebuild attempt; out-of-band notification if it still fails.

**Initial prompt.** The agent starts with a fixed prompt ("Session startup. Run the startup sequence.") on every loop iteration. This is consistent -- the startup hook gathers all context and injects it, so the prompt just triggers that hook.

**Post-session summarizer.** Between sessions, a script summarizes the session transcript outside the agent's context window (zero token cost). The summary feeds into memory for future sessions.

**Skip-permissions flag.** In a persistent agent context, the operator has pre-approved the agent's access. The agent should not block on permission prompts when no one is at the terminal. Use this deliberately -- it grants full tool access.

**Reset defaults on boot.** Voice and wake word toggles reset to "off" on every reboot. The operator enables them when at the desk. This prevents the agent from speaking into an empty room after an overnight reboot.

## launchd Integration

macOS uses launchd for service management. For a persistent agent, you need at least three launchd jobs:

1. **The agent session** -- starts the tmux loop on login
2. **Health check** -- periodic checks every 30 minutes
3. **Nightly shutdown** -- scheduled restart for a clean daily slate

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
    <string>/Users/you/.agent/logs/healthcheck.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/you/.agent/logs/healthcheck.err</string>
</dict>
</plist>
```

### Nightly Shutdown LaunchAgent

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agent.nightly-shutdown</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/you/your-agent/workers/nightly-shutdown.sh</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>4</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/you/.agent/logs/nightly-shutdown.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/you/.agent/logs/nightly-shutdown.err</string>
</dict>
</plist>
```

Pair this with a `pmset` wake schedule so the machine comes back up before the workday:

```bash
# Wake every weekday at 6:55 AM (before the session starts)
sudo pmset repeat wake MTWRF 06:55:00
```

### launchd Limitations

launchd is reliable but has quirks:

- **Sleep/shutdown timing.** Jobs scheduled by `StartInterval` do not fire while the machine is asleep. If the Mac sleeps for 8 hours, you do not get 16 missed health checks queued up -- they just do not run. This is usually fine (nothing to check while sleeping), but be aware.
- **Environment.** launchd jobs run with a minimal environment. Always set `PATH` and `HOME` explicitly in the plist. Tools like `jq`, `python3`, or `claude` may not be on the default launchd PATH.
- **Logging.** Use `StandardOutPath` and `StandardErrorPath` for debugging. `launchctl list | grep agent` shows running jobs. `launchctl error` decodes exit codes.
- **User agents vs. system daemons.** Use `~/Library/LaunchAgents/` (user agents) unless you need the job to run before login. System daemons (`/Library/LaunchDaemons/`) have different permissions and lifecycle.
- **`launchctl unload` is session-scoped, not persistent.** `launchctl unload` (or `bootout`) disables the job for the current session only. Under a nightly-reboot regime, `RunAtLoad` resurrects the job at the next boot regardless. To durably disable a job, rename the plist to `.plist.disabled` -- that prevents launchd from seeing it entirely.

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

The startup hook collects, in priority order:

1. **Cold boot recovery signal** -- if the machine just rebooted, a vitals worker wrote a `boot_event.json`; surface this first because it reframes everything else (workers may not be running, in-flight tasks were interrupted)
2. **Task lock** -- if `task.lock` exists and is fresh (< 24hr), inject it as a resume directive; the agent starts working on STEP immediately
3. **Durable background task restoration** -- crons/monitors registered as durable in a JSON registry; session-scoped entries are purged
4. **System state** -- voice toggles, usage baseline
5. **Handoff** -- task context from the previous session (if present)
6. **Today's pulse** -- same-day session continuity entries
7. **Task index** -- active tasks and reconciliation discrepancies
8. **Graph memory recall** -- cold start only (first session of a new day)
9. **Cold-start backfills** -- missed reflections, stale backups, git sync (run in background, don't block)

The hook distinguishes cold starts from warm starts. Cold start = no handoff written today AND no pulse entries today (the first session of a new day). The handoff file is deliberately never deleted -- existence alone does not mean fresh; the hook checks the file's mtime.

Headless workers (launchd `claude -p` invocations) are detected via `$TMUX` and get a 2-line stamp instead of the full payload. They don't need orientation, and sending them the full payload wastes tokens on every background job.

```python
def is_interactive_session() -> bool:
    # Main session runs inside tmux; headless launchd workers don't.
    return bool(os.environ.get("TMUX"))

# ...

# Headless: minimal stamp, early exit
if not is_interactive_session():
    json.dump({"hookSpecificOutput": {"hookEventName": "SessionStart",
        "additionalContext": "Headless session — full startup context skipped."}},
        sys.stdout)
    return

# Cold start = no fresh handoff AND no pulse today
is_cold_start = not (handoff and handoff_is_fresh(handoff)) and not pulse

# Task lock — inject as resume directive if present and fresh
task_lock = read_task_lock()  # auto-deletes if stale (> 24hr)
if task_lock:
    parts.append("## RESUME TASK (task.lock is active)\n"
                 "Start working on STEP immediately...\n\n"
                 f"```\n{task_lock}\n```")

# Cold start backfills run as background Popen — do not block hook
if is_cold_start:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    if not reflection_exists(yesterday):
        subprocess.Popen(["bash", reflect_script, yesterday],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not backup_exists_today():
        subprocess.Popen(["python3", backup_script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.Popen(["bash", "-c", "cd ~/your-agent && git push"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
```

Cold-start backfills run as background processes (`Popen`) so they do not block the hook. The agent starts immediately; maintenance happens in parallel.

The full template is in `templates/hooks/session-startup.py`.

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
  → healthcheck.sh
    → checks: processes, disk, alert queue, core liveness
    → healthy: log CARDIAC_OK, exit silently
    → core dead (tmux/loop missing): self-heal via kickstart
    → other issue: inject [CARDIAC_ALERT] into tmux
      → agent spawns subagent to investigate
```

The key insight: the health check itself costs nothing. It is a bash script that checks process lists, disk usage, and service status. Only when something fails does it involve the agent (by injecting a message into the tmux session).

**Core liveness check.** The most important check is verifying that the tmux session and the loop supervisor are both alive. This runs from outside tmux, so it catches the boot-time blind spot where the session dies silently after creation. The check is layered:
1. Does the tmux session exist? (`tmux has-session`)
2. Is the loop supervisor process running? (`ps` for the claude-loop.sh bash process)
3. If either is dead, wait 3 seconds (transient-blip guard), re-check, then kickstart the auto-launch service if still dead.

The kickstart uses the init system to restart the auto-launch script (`launchctl kickstart -k` on macOS), which recreates the session from scratch. This is the same fix a human would apply manually.

```bash
# Core liveness check (runs from outside tmux, every 30 min)
loop_live() {
    ps -axo comm=,args= 2>/dev/null | \
        awk '$1 ~ /(\/|^)bash$/ && /workers\/claude-loop\.sh/ {c++} END{print c+0}'
}

core_dead=0; core_reason=""
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    core_dead=1; core_reason="tmux session missing"
elif [ "$(loop_live)" = "0" ]; then
    core_dead=1; core_reason="claude-loop supervisor dead (tmux alive)"
fi

if [ $core_dead -eq 1 ]; then
    sleep 3  # transient-blip guard before the destructive kickstart
    if tmux has-session -t "$SESSION" 2>/dev/null && [ "$(loop_live)" != "0" ]; then
        echo "$(date) CORE recovered on recheck — no kickstart" >> "$LOG"
    else
        kick_out=$(launchctl kickstart -k "gui/$(id -u)/com.agent.session" 2>&1)
        echo "$(date) CORE_DEAD ($core_reason) — kickstart rc=$? $kick_out" >> "$LOG"
        ALERTS+=("[critical] Core down: $core_reason — kickstarted auto-launch")
    fi
fi
```

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

## Nightly Scheduled Restart

A scheduled nightly shutdown and wake cycle gives you a clean slate every day: fresh session, memory consolidated, git pushed, workers restarted. It also forces you to build a system robust enough to survive restarts -- which is the same robustness you need for crash recovery.

The pattern: a launchd job fires at a fixed time (e.g., 4:00 AM) and injects a shutdown directive into the agent's tmux session. The agent writes a handoff, runs reflection, pushes git, and issues `shutdown -h now`. A separate `pmset` wake schedule (or launchd `StartCalendarInterval`) wakes the machine at the desired start time.

```bash
#!/bin/bash
# nightly-shutdown.sh -- triggered by launchd at ~4:00 AM

# Gate: only shut down if the operator has been idle long enough.
# This prevents a shutdown during an active late-night session.
IDLE=$(ioreg -c IOHIDSystem | awk '/HIDIdleTime/{print int($NF/1e9)}')
if [ "$IDLE" -lt 1800 ]; then  # less than 30 min idle
    echo "$(date): operator active (idle ${IDLE}s) — skipping shutdown" \
        >> ~/.agent/logs/nightly-shutdown.log
    exit 0
fi

# Inject the shutdown directive into the agent's tmux session
tmux send-keys -t agent "/restart shutdown" Enter

# Backstop: if the agent doesn't shut down in 20 minutes, force it
sleep 1200
osascript -e 'tell application "Terminal" to quit' 2>/dev/null
shutdown -h now
```

The agent handles the graceful path (handoff → reflection → git → shutdown). The backstop handles cases where the agent is stuck or the session has already died.

**Key decisions:**
- Gate on idle time to protect active sessions. 30 minutes is a reasonable threshold.
- The backstop fires unconditionally after the grace window -- graceful > forced, but forced > nothing.
- The wake schedule is set separately (pmset wake, `WakeOnLan`, or a calendar interval in the launchd plist that starts your auto-tmux).

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
         ├─ creates new tmux session → runs claude-loop.sh
         └─ boot-resilience: verify session alive after 5s, rebuild once
            if not; page operator if rebuild fails

2. SESSION START
   └─ claude starts with initial prompt
      └─ SessionStart hooks fire:
          ├─ session-startup.py: gathers all context
          │   ├─ headless check: workers get 2-line stamp, exit early
          │   ├─ cold boot recovery signal (if boot_event.json present)
          │   ├─ task.lock: inject resume directive if present + fresh
          │   ├─ background task registry: restore durable crons/monitors,
          │   │   purge session-scoped entries
          │   ├─ reads voice/wake state
          │   ├─ reads handoff (if present)
          │   ├─ reads pulse (if today has entries)
          │   ├─ task index: active tasks + reconciliation
          │   ├─ cold start: runs graph memory recall
          │   ├─ cold start: backfills missed maintenance (background Popen)
          │   └─ injects everything as additionalContext
          └─ compact-recovery-inject.sh: checks for compaction flag

3. CONVERSATION
   └─ each turn:
       ├─ UserPromptSubmit hooks fire:
       │   ├─ context-threshold.sh: injects warnings at 30/45/55%
       │   └─ session-elapsed.sh: nudges reflection if session is long
       ├─ agent responds
       ├─ Stop hooks fire:
       │   └─ stop-tts.sh: forwards response to TTS daemon
       └─ PostToolUse hooks fire (on tool calls):
           ├─ activity-log.sh: logs tool execution
           └─ error-detector.sh: logs significant errors

4. BACKGROUND (every 30 min)
   └─ launchd fires healthcheck.sh
       ├─ healthy: log CARDIAC_OK, exit
       ├─ core dead (tmux/loop missing): self-heal via kickstart
       └─ other issue: inject [CARDIAC_ALERT] into tmux

5. NIGHTLY (scheduled, e.g., 4:00 AM)
   └─ launchd fires nightly-shutdown.sh
       ├─ gate: skip if operator active (< 30 min idle)
       ├─ injects /restart shutdown into agent's tmux session
       └─ backstop: force shutdown after 20 min grace window
          └─ machine wakes at configured start time via pmset/launchd

6. SESSION END (restart or shutdown)
   └─ agent writes handoff, exits
      └─ claude-loop.sh catches exit:
          ├─ healthy session (≥30s): runs summarizer, waits 3s, restarts
          └─ rapid exit (<30s): increments counter, exponential backoff
             ├─ < 10 rapid exits: back off and retry
             └─ ≥ 10 rapid exits: hard stop, page operator
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
