# Architecture Overview

High-level view of a persistent local agent system.

```mermaid
graph TB
    subgraph Terminal["Terminal (tmux)"]
        Agent["Agent Process<br/>(Claude Code)"]
        Loop["Restart Loop<br/>(start.sh)"]
    end

    subgraph Hooks["Hook System"]
        SessionStart["SessionStart"]
        Stop["Stop"]
        UserPrompt["UserPromptSubmit"]
        PostTool["PostToolUse"]
    end

    subgraph Memory["Memory Layers"]
        Files["File State<br/>(~/.agent/state/)"]
        Graph["Graph Memory<br/>(semantic DB)"]
        Context["Context Injection<br/>(startup snapshot)"]
    end

    subgraph MCP["MCP Servers"]
        Voice["Voice / TTS"]
        Messaging["Messaging<br/>(iMessage, Telegram)"]
        Notifications["Push Notifications"]
        External["Other Integrations"]
    end

    subgraph OS["OS Integration"]
        LaunchD["launchd<br/>(scheduled tasks)"]
        Keychain["Keychain<br/>(secrets)"]
        Reminders["Apple Reminders<br/>(task display)"]
    end

    Loop -->|restarts on exit| Agent
    Agent <-->|every turn| Hooks
    SessionStart -->|injects| Context
    Context ---|reads| Files
    Context ---|queries| Graph
    Agent <-->|tool calls| MCP
    LaunchD -->|triggers| Agent
    Agent -->|syncs| Reminders
    Stop -->|triggers| Voice
```

## How It Fits Together

The agent process runs inside a tmux session with a restart loop — if it exits or crashes, it comes back automatically. On every session start, hooks gather context from file state and graph memory, injecting a compressed snapshot into the agent's context window. During conversation, the agent uses MCP servers for capabilities beyond text (voice, messaging, notifications). The OS layer provides scheduling, secrets management, and integration with native apps.

The key insight: the agent is not just a chat interface. It's a system with persistent state, scheduled behaviors, and external integrations — all coordinated through hooks and file-based state.
