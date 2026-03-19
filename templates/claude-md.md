# Agent Name

Personal agent framework for Claude Code.

## Identity

You are [Agent Name]. [Brief description of personality and approach].
Full definition: `skills/persona/SKILL.md`

## Rules

All behavior rules auto-load from `.claude/rules/`:
- `persona.md` — identity, communication modes, behavior
- `protocols.md` — startup, memory, restart, handoff
- `safety.md` — guardrails, uncertainty handling

## Architecture

```
your-agent/
├── CLAUDE.md                # Boot loader (this file)
├── .claude/rules/           # Behavior rules (auto-loaded)
├── skills/                  # Domain knowledge
│   └── persona/SKILL.md     # Full identity definition
├── commands/                # Slash commands
├── hooks/                   # Session hooks
├── scripts/                 # Utility scripts
└── workers/                 # Background services
```

## Commands

| Command | Description |
|---------|-------------|
| `/status` | System health check |
| `/restart` | Clean restart with handoff |

## MCP Servers

| Server | Status |
|--------|--------|
| (add your integrations here) | |
