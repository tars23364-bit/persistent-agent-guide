# Session Lifecycle

The full flow from boot to shutdown.

```mermaid
flowchart TD
    Boot["Mac Boots<br/>(6:00 AM wake)"] --> TMux["tmux session starts"]
    TMux --> StartSh["start.sh restart loop"]
    StartSh --> Claude["Claude Code launches"]
    Claude --> Hook["SessionStart hook fires"]

    Hook --> Gather["Gather Context"]
    Gather --> Voice["Read voice/toggle state"]
    Gather --> Handoff["Read handoff (if exists)"]
    Gather --> TaskIdx["Build task index +<br/>reconcile reminders"]
    Gather --> Attune["Query graph memory<br/>for operator awareness"]
    Gather --> Backfill["Backfill missed jobs<br/>(reflection, backup)"]

    Voice & Handoff & TaskIdx & Attune --> Inject["Inject as<br/>additionalContext"]
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
        R1["Write handoff"] --> R2["Run summarizer"]
        R2 --> R3["Kill process"]
        R3 -->|"3s delay"| StartSh
    end

    Backfill -.->|"background"| Reflection["Missed reflection<br/>(if needed)"]
    Backfill -.->|"background"| Backup["Missed backup<br/>(if needed)"]
```

## Key Points

**Startup is zero-cost to the agent.** All context gathering happens in the hook before the agent sees its first prompt. No tool calls needed — everything arrives as injected context.

**Shutdown is sequential and ordered.** Handoff first (preserves task thread), then summarizer (captures session knowledge), then reflection (daily self-assessment), then backups, then power off. Each step must complete before the next begins.

**The restart loop is the safety net.** If the agent crashes, exits, or is killed, the loop in start.sh waits 3 seconds and launches a fresh session. The summarizer runs on every exit (including crashes) to preserve knowledge.

**Backfills run in parallel with context injection.** If the machine was powered off when a daily job was supposed to run, the startup hook detects the gap and runs missed jobs (reflection, backup) as background processes. They do not block context injection or session startup -- the agent begins immediately while backfills complete independently.
