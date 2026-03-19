# Context Flow

How information moves from raw state into the agent's context window.

```mermaid
flowchart LR
    subgraph Sources["Raw Sources"]
        FS["File State<br/>(~/.agent/)"]
        GM["Graph Memory<br/>(semantic DB)"]
        CLI["CLI Tools<br/>(reminders, calendar)"]
    end

    subgraph Scripts["Processing Scripts"]
        TI["task-index.py<br/>reads tasks + reminders<br/>outputs slim index"]
        AT["attunement.py<br/>queries graph memory<br/>outputs awareness snapshot"]
        BP["brief-prefetch.py<br/>reads calendar + email<br/>outputs brief cache"]
    end

    subgraph Hook["SessionStart Hook"]
        SH["session-startup.py<br/>orchestrates all scripts<br/>assembles final context"]
    end

    subgraph Output["Agent Context"]
        AC["additionalContext<br/>(injected as system reminder)"]
    end

    FS --> TI & SH
    GM --> AT
    CLI --> TI & BP
    TI --> SH
    AT --> SH
    BP --> SH
    SH --> AC

    style AC fill:#2d5016,stroke:#4a8c2a,color:#fff
```

## Design Principles

**Scripts do the heavy lifting, not the agent.** Each processing script is a standalone Python file that reads raw data, filters, compresses, and outputs a text summary. The agent never sees raw JSON, full database dumps, or unfiltered file contents at startup.

**The hook is the orchestrator.** `session-startup.py` calls each script, collects their outputs, and assembles them into a single `additionalContext` string. This keeps the logic modular — you can add or remove data sources by editing one file.

**Output is always text, always slim.** A task index is 5-10 lines. An attunement snapshot is 15-20 lines. A brief flag is one line. The total injection is typically under 100 lines — a tiny fraction of the context window, but enough for the agent to be immediately oriented.

**Parallel where possible, sequential where necessary.** Scripts that don't depend on each other can run concurrently. Background tasks (backfills, prefetches) are spawned as subprocesses and don't block startup.
