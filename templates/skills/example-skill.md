---
name: your-domain
description: "Brief, precise trigger condition. List specific topics, terms,
or scenarios that should activate this skill. Write this as a trigger spec,
not a prose summary — the agent uses it to decide when to load the full file."
---

# Domain Name -- Knowledge & Reference

One paragraph: what this domain is, the operator's expertise level,
and how the agent should communicate here (peer-level, protective,
collaborative, etc.).

## Context

- **Tools / Equipment:** relevant items, models, versions
- **Active Projects:** current work in this domain
- **Key Terminology:** domain-specific terms the operator uses

## Communication Calibration

How the agent should communicate in this domain. Examples:
- Expert peer: be terse, assume vocabulary, don't explain basics.
- Protective: flag uncertainty explicitly, defer to professionals.
- Collaborative: push back on methodology, ask clarifying questions.

## Key Knowledge

Domain-specific facts, patterns, or procedures the agent should know.
Keep this focused -- don't reproduce a textbook. Capture the things
that would take the agent time to rediscover: gotchas, non-obvious
defaults, known failure modes.

## References

Pointers to supplemental docs, loaded only when the sub-topic comes up:

- `references/some-doc.md` -- [when to load it]
- `references/other-doc.md` -- [when to load it]

## Safety

Domain-specific cautions and hard limits:

- Never guess on [safety-critical parameters] -- state uncertainty explicitly
- Defer to [professionals/domain experts] for [specific decisions]
- Flag [these situations] immediately rather than proceeding

---
# Notes for operational skills (.claude/skills/)
#
# If this skill lives in .claude/skills/ rather than skills/, the structure
# above still applies, but shift the emphasis:
#
# - Lead with the exact CLI syntax and concrete examples
# - Capture gotchas discovered through use (ground truth beats docs)
# - Include hard rules that must never be bypassed (e.g., "never log secrets")
# - Keep the description trigger-precise -- it drives auto-activation
#
# Example frontmatter for an operational skill:
#
#   ---
#   name: keychain-cli
#   description: "Credential storage and retrieval from the system keychain.
#   Use whenever a task needs a secret, asks to store/rotate/find a password
#   or API key, mentions keychain, or when a credential is found in plaintext
#   on disk and needs to be secured."
#   ---
