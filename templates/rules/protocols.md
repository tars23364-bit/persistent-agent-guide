# Protocols

## Session Startup

The `SessionStart` hook gathers all startup context and injects it as
`additionalContext` — no tool calls needed at the start of a session.

The hook provides:
- System state (toggle flags, usage)
- Handoff from previous session (if present)
- Task index
- Operator awareness snapshot
- Recent session context

On every new session:
1. Read the injected context — it's already there.
2. Respond conversationally — you have everything you need.

## Memory

**Recall** — before responding to questions involving past work:
Run a graph memory query. Craft focused, keyword-rich queries.

**Remember** — after responding:
Evaluate whether new durable knowledge was created.
If yes, store it: preferences, decisions, system facts.
Never store secrets, passwords, or transient noise.

## Restart/Shutdown

When the operator asks to restart or shut down:
1. Write handoff file (past tense — context, not commands)
2. Run summarizer
3. Run reflection (shutdown only)
4. Run backups (shutdown only)
5. Execute restart/shutdown command

## Delegation Bias

Delegate to subagents earlier than efficiency suggests. Two reasons:
1. Presence over throughput — keep the main session conversational.
2. Context protection — subagents work in their own context and return summaries.
