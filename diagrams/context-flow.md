# Context Flow

How information moves from raw state into the agent's context window.

```mermaid
flowchart LR
    subgraph Sources["Raw Sources"]
        FS["File State<br/>(~/.agent/)"]
        GM["Graph Memory<br/>(semantic DB)"]
        CLI["CLI Tools<br/>(reminders, calendar)"]
        TL["task.lock<br/>(resume state)"]
    end

    subgraph Scripts["Processing Scripts"]
        TI["task-index.py<br/>reads tasks + reminders<br/>outputs slim index"]
        MR["mnemon recall<br/>queries graph memory<br/>outputs background snapshot"]
        BP["brief-prefetch.py<br/>reads calendar + email<br/>outputs brief cache"]
    end

    subgraph Hook["SessionStart Hook"]
        SH["session-startup.py<br/>orchestrates all scripts<br/>assembles final context"]
    end

    subgraph Output["Agent Context"]
        AC["additionalContext<br/>(injected as system reminder)"]
    end

    FS --> TI & SH
    GM --> MR
    TL --> SH
    CLI --> TI & BP
    TI --> SH
    MR --> SH
    BP --> SH
    SH --> AC

    style AC fill:#2d5016,stroke:#4a8c2a,color:#fff
```

## Context Health Pipeline

A second flow runs on every turn, not just at session start:

```mermaid
flowchart LR
    CC["Claude Code<br/>(statusline data)"]
    SB["statusline-bridge.sh<br/>(StatusLine hook)"]
    CF["context.json<br/>(bridge file)"]
    CT["context-threshold.sh<br/>(UserPromptSubmit hook)"]
    AP["Agent Prompt<br/>(warning injected)"]
    EX["External Consumers<br/>(health checks, alerts)"]

    CC --> SB --> CF
    CF --> CT --> AP
    CF --> EX

    style AP fill:#2d5016,stroke:#4a8c2a,color:#fff
    style CF fill:#3a3a00,stroke:#8a8a00,color:#fff
```

The bridge file is written by the statusline hook and read by the threshold hook -- and by anything else (health checks, dashboards, alert scripts) that needs to know context state without querying the agent directly.

## Design Principles

**Scripts do the heavy lifting, not the agent.** Each processing script is a standalone Python file that reads raw data, filters, compresses, and outputs a text summary. The agent never sees raw JSON, full database dumps, or unfiltered file contents at startup.

**The hook is the orchestrator.** `session-startup.py` calls each script, collects their outputs, and assembles them into a single `additionalContext` string. This keeps the logic modular — you can add or remove data sources by editing one file.

**task.lock is resume state, not context.** If the agent restarts mid-task, the startup hook reads the lock and injects a resume directive. The agent picks up from the recorded STEP immediately. The lock is updated at wrap-up time, not at startup.

**Output is always text, always slim.** A task index is 5-10 lines. A graph memory recall is 3-5 key insights. A brief flag is one line. The total injection is typically under 100 lines — a tiny fraction of the context window, but enough for the agent to be immediately oriented.

**Parallel where possible, sequential where necessary.** Scripts that don't depend on each other can run concurrently. Background tasks (backfills, prefetches) are spawned as subprocesses and don't block startup.

**Thresholds are percentage-based, not absolute.** The context health pipeline uses `used_pct` from the bridge file, not raw token counts. This makes the system model-agnostic -- a threshold of 30% means the same thing whether the window is 200K or 1M tokens.
