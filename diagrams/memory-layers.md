# Memory Layers

How persistent memory is structured across three tiers.

```mermaid
graph TB
    subgraph Layer1["Layer 1: File State"]
        direction LR
        Handoff["handoff.md"]
        Pulse["today-pulse.md"]
        Toggles["Toggle Flags<br/>(voice, wake-word)"]
        Tasks["tasks/*.md"]
        Alerts["alerts/"]
    end

    subgraph Layer2["Layer 2: Graph Memory"]
        direction LR
        Insights["Insights<br/>(facts, decisions,<br/>preferences)"]
        Edges["Temporal &<br/>Entity Edges"]
        Decay["Access Decay<br/>& Lifecycle"]
    end

    subgraph Layer3["Layer 3: Context Injection"]
        direction LR
        TaskIndex["Task Index"]
        Attunement["Operator<br/>Awareness"]
        SessionTail["Last Session<br/>Tail"]
        BriefFlag["Brief<br/>Availability"]
    end

    Layer1 -->|"scripts read files"| Layer3
    Layer2 -->|"queries return insights"| Layer3
    Layer3 -->|"injected via SessionStart hook"| Agent["Agent Context Window"]

    Agent -->|"writes during session"| Layer1
    Agent -->|"remember commands"| Layer2
```

## The Three Tiers

**Layer 1 — File State** is the fastest and most deterministic. Toggle flags, handoff files, pulse entries, task files — all plain text, instantly readable, no queries needed. This is your agent's short-term working memory.

**Layer 2 — Graph Memory** is the semantic layer. A database of insights with importance scores, entity linking, temporal edges, and access-based decay. You query it with natural language and get relevance-ranked results. This is long-term memory.

**Layer 3 — Context Injection** is the bridge. At startup, scripts read from Layers 1 and 2, compress the results into a slim snapshot, and inject it into the agent's context window via the SessionStart hook. The agent never touches the raw data stores directly during startup — it receives a curated summary.

The flow is always: **store broadly, inject narrowly**. Memory accumulates freely across sessions, but what enters the context window is filtered and compressed.
