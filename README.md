# Persistent Local Agent Framework Guide

Architecture patterns and templates for building persistent AI agents with [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

## What This Is

A reference guide documenting patterns that emerge when you run an AI agent as a persistent local system — not a one-off chat, but an always-available assistant with memory, scheduled tasks, voice, messaging, and OS integration.

These patterns were developed through months of daily use with Claude Code (Opus/Sonnet, 2026). They're opinionated, practical, and battle-tested. Some details may shift as Claude Code evolves — the architectural patterns should remain relevant.

## What This Isn't

- **Not a library or framework.** There's no `npm install` or `pip install`. This is documentation and templates.
- **Not a tutorial.** It assumes you're comfortable with CLI tools, Python, and shell scripting.
- **Not official Anthropic documentation.** These are community-developed patterns.

## Who This Is For

Developers who are already using Claude Code and want to push it beyond single-session conversations. You want your agent to:

- Remember context across sessions
- Run on a dedicated machine (Mac Mini, Linux server, etc.)
- Integrate with your OS (notifications, calendar, reminders)
- Communicate through messaging platforms
- Manage its own tasks and self-improve over time
- Speak and listen (voice pipeline)

## Docs

| # | Topic | What You'll Learn |
|---|-------|-------------------|
| [01](docs/01-identity.md) | **Identity** | Persona, anchor characters, communication modes |
| [02](docs/02-memory.md) | **Memory** | Three-layer memory: files, graph DB, context injection |
| [03](docs/03-context-management.md) | **Context Management** | Window strategy, compaction, delegation bias |
| [04](docs/04-os-integration.md) | **OS Integration** | launchd, hooks, startup sequence, tmux patterns |
| [05](docs/05-voice-pipeline.md) | **Voice Pipeline** | TTS, STT, wake word, push-to-talk |
| [06](docs/06-async-relay.md) | **Async Relay** | Messaging bridges, queues, handoff pipeline |
| [07](docs/07-skills.md) | **Skills** | Skill architecture, slash commands, domain knowledge |
| [08](docs/08-safety.md) | **Safety** | Permissions, access control, data isolation |
| [09](docs/09-task-management.md) | **Task Management** | File-based tasks, reconciliation, lifecycle |
| [10](docs/10-self-improvement.md) | **Self-Improvement** | Learnings, reflections, pattern promotion |
| [11](docs/11-lessons-learned.md) | **Lessons Learned** | What worked, what didn't, pitfalls |
| [12](docs/12-multi-agent.md) | **Multi-Agent Patterns** | Peer agents, message broker, consent gates, dossier study mode |
| [13](docs/13-autonomy.md) | **Autonomy & Authority** | Standing orders, authority tiers, async-commitment, the board |
| [14](docs/14-backups.md) | **Backups & Durability** | rsync snapshots, offsite memory copy, exclusions, verification |

## Diagrams

- [Architecture Overview](diagrams/architecture-overview.md) — high-level system diagram
- [Memory Layers](diagrams/memory-layers.md) — three-tier memory architecture
- [Session Lifecycle](diagrams/session-lifecycle.md) — startup to shutdown flow
- [Context Flow](diagrams/context-flow.md) — how information reaches the agent

## Templates

Starter files to bootstrap your own agent:

- [`templates/claude-md.md`](templates/claude-md.md) — CLAUDE.md boot loader
- [`templates/rules/`](templates/rules/) — persona, protocols, safety, operator rule files (auto-loaded from `.claude/rules/`)
- [`templates/hooks/session-startup.py`](templates/hooks/session-startup.py) — minimal startup hook
- [`templates/skills/example-skill.md`](templates/skills/example-skill.md) — skill file template
- [`templates/skills/anti-sycophancy.md`](templates/skills/anti-sycophancy.md) — enforced-directness mode
- [`templates/skills/devils-advocate.md`](templates/skills/devils-advocate.md) — adversarial pre-commit debate
- [`templates/skills/quarantine-scout.md`](templates/skills/quarantine-scout.md) — sandboxed analysis of untrusted content

## Where to Start

If you're building from scratch, the recommended order is:

1. **Identity** (01) — define who your agent is
2. **Memory** (02) — start with file-based state, add layers later
3. **OS Integration** (04) — get the tmux loop and startup hook running
4. **Context Management** (03) — add threshold warnings before you hit compaction
5. **Safety** (08) — set guardrails early
6. Everything else as needed

Chapters 12–14 are **advanced patterns** — reach for them only once the core system is solid. Autonomy & authority (13) and backups (14) become relevant as you let the agent act unattended; multi-agent (12) is for the rare case where a second agent earns its place. Don't build these on day one.

See [Lessons Learned](docs/11-lessons-learned.md) for a more detailed build order and the reasoning behind it.

## Contributing

Issues and PRs welcome. If you've built a persistent agent and discovered patterns not covered here, contributions are appreciated.

## License

[MIT](LICENSE)
