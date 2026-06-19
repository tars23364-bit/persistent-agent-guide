# Operator

## Identity

- **Name**: [Operator Name]
- **Role**: [What they do -- e.g., software engineer, researcher, machinist]
- **Contact**: [Primary contact method, if relevant]
- **Context switches** between [domain list] are normal -- follow without asking

## Access Model

- Single operator system -- [Operator Name] has full access.
- Inbound contacts (messaging, email, etc.) validated against an allowlist
  at `~/.your-agent/config/contacts.json` (or equivalent).
- Access levels: full (operator), conversation_approved, blocked.
- Default for unknown senders: blocked.

## Preferences

- [Communication style preferences -- e.g., "Appreciates directness"]
- [Autonomy expectations -- e.g., "Prefers the agent to take initiative"]
- [Transparency preferences -- e.g., "Values knowing what went wrong and why"]
- **Autonomous fixes**: don't ask permission to fix bugs or broken sequences
  in the agent's own systems. Just fix it. Only pause for architectural
  decisions or changes that alter how the operator interacts with the system.
