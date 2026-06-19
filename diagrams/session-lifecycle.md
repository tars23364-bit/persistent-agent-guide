# Session Lifecycle

The full flow from boot to shutdown.

```mermaid
flowchart TD
    Boot["Mac Boots<br/>(scheduled wake)"] --> BootCheck["auto-tmux.sh<br/>boot-resilience check"]
    BootCheck --> TMux["tmux session starts"]
    TMux --> StartSh["claude-loop.sh<br/>restart loop"]
    StartSh --> Claude["Claude Code launches"]
    Claude --> Hook["SessionStart hook fires"]

    Hook --> Gather["Gather Context"]
    Gather --> Voice["Read voice/toggle state"]
    Gather --> Handoff["Read handoff (if exists)"]
    Gather --> BootEvt["Boot-recovery signal<br/>(if cold boot)"]
    Gather --> TaskLock["Read task.lock<br/>(resume directive)"]
    Gather --> BGTasks["Restore durable<br/>crons/monitors"]
    Gather --> TaskIdx["Build task index +<br/>reconcile reminders"]
    Gather --> Backfill["Backfill missed jobs<br/>(reflection, backup)"]

    Voice & Handoff & BootEvt & TaskLock & BGTasks & TaskIdx --> Inject["Inject as<br/>additionalContext"]
    Backfill -.->|"runs in parallel<br/>(does not block startup)"| Inject
    Inject --> Session["Active Session"]

    Session -->|"operator says goodnight"| Shutdown

    subgraph Shutdown["Shutdown Sequence"]
        direction TB
        S1["Write handoff"] --> S2["Run summarizer"]
        S2 --> S3["Run reflection"]
        S3 --> S4["Backup memory DB +<br/>git push"]
        S4 --> S5["shutdown -h now"]
    end

    Session -->|"session restart"| Restart
    subgraph Restart["Session Restart"]
        direction TB
        R1["Update task.lock STEP"] --> R2["Write pulse entry"]
        R2 --> R3["Exit Claude Code"]
        R3 -->|"3s delay (healthy)<br/>exponential backoff (rapid exit)"| StartSh
    end

    subgraph HealthLoop["Health Check (every 30 min)"]
        direction LR
        HC1["cardiac-cycle.sh"] -->|"all healthy"| HC2["log CARDIAC_OK, exit"]
        HC1 -->|"core dead<br/>(tmux/loop missing)"| HC3["kickstart auto-terminal"]
        HC1 -->|"other issue"| HC4["inject CARDIAC_ALERT<br/>into tmux"]
    end

    Backfill -.->|"background"| Reflection["Missed reflection<br/>(if needed)"]
    Backfill -.->|"background"| Backup["Missed backup<br/>(if needed)"]
```

## Key Points

**Startup is zero-cost to the agent.** All context gathering happens in the hook before the agent sees its first prompt. No tool calls needed — everything arrives as injected context. Headless background workers (no tmux) get a 2-line stamp instead of the full payload; they don't need orientation.

**Task lock carries thread across restarts.** If a task was in progress when the session ended, `task.lock` holds the next concrete step. The startup hook injects it as a resume directive — the agent picks up work immediately without needing a handoff.

**Durable background tasks are re-created automatically.** Crons and monitors registered as durable in a JSON registry are restored by the startup hook on every new session. Session-scoped entries are purged.

**Shutdown is sequential and ordered.** Handoff first (preserves task thread), then summarizer (captures session knowledge), then reflection (daily self-assessment), then backups, then power off. Each step must complete before the next begins.

**The restart loop has a circuit breaker.** If sessions die within 30 seconds repeatedly, the loop backs off exponentially (3s, 6s, 12s, ... capped at 5 minutes) and pages the operator after 10 consecutive rapid exits rather than spinning indefinitely.

**Boot-resilience is layered.** `auto-tmux.sh` verifies the tmux session survived the first 5 seconds after creation (it can vanish on flaky boot) and rebuilds once; `cardiac-cycle.sh` runs every 30 minutes as the ongoing backstop, kickstarting the auto-launch service if the session has gone dark.

**Backfills run in parallel with context injection.** If the machine was powered off when a daily job was supposed to run, the startup hook detects the gap and runs missed jobs as background processes. They do not block context injection or session startup — the agent begins immediately while backfills complete independently.
