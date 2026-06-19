# Identity & Persona

A persistent agent that restarts dozens of times a day needs a stable identity. Without one, every session starts from zero -- no consistent tone, no accumulated trust, no reliable behavior. The persona layer solves this by defining *who the agent is* outside of any single conversation.

This is not cosmetic. Identity drives consistency in how the agent communicates, makes decisions, and handles ambiguity. It is the difference between a tool that responds and an agent that collaborates.

## Why Persona Matters

Claude Code sessions are ephemeral. The model has no built-in memory of past interactions. Every restart produces a blank slate that happens to have access to your files.

A well-defined persona provides:

- **Behavioral consistency** across sessions, models, and context lengths
- **Communication calibration** -- the agent knows when to be terse and when to elaborate
- **Decision-making defaults** -- how to handle uncertainty, when to push back, when to defer
- **Trust accumulation** -- the operator learns what to expect, and the agent delivers it

Without persona, you get generic assistant behavior: hedging, over-explaining, asking permission for everything. With persona, you get an agent that knows how to work with *you*.

## Architecture: Boot Loader + Full Definition

The persona is split into two layers:

### The Boot Loader (CLAUDE.md)

`CLAUDE.md` is the entry point Claude Code reads on every session. It should be a slim index, not the full definition. Think of it as a table of contents that orients the agent quickly.

```markdown
# Agent

Personal agent framework for Claude Code.

## Identity

You are Agent. [Anchor character] anchored -- direct, [trait], [trait].
Full definition: `skills/persona/SKILL.md`

## Rules

All files in `.claude/rules/` auto-load every session -- the directory is the
authoritative list. Names are self-describing; notable non-obvious ones:
`ground-before-modify.md` (third-party-tool discipline: installed reality outranks
docs), `board.md` (async surface), `recall-routing.md` (memory recall wrapper).
```

The boot loader establishes identity in a few lines, then points to the full definition. This matters because `CLAUDE.md` is read on every session start -- keep it lean so the agent orients fast without burning context on details it may not need.

The rules directory is the authoritative behavior layer. Every file in `.claude/rules/` loads automatically -- there is no explicit list to maintain in `CLAUDE.md`. Add a rule file and it takes effect on the next session. This makes the rules directory the right place for any behavior that should always be active, and means the boot loader never goes stale as the rule set grows.

### The Full Persona (skills/persona/SKILL.md)

This is the complete identity definition. It goes in the skills directory and gets loaded when the agent needs the full picture -- typically on cold starts or when identity-related questions come up.

```markdown
---
name: persona
description: "Agent persona and communication identity. Defines core
personality, tone, and behavioral framework for all interactions."
---

# Agent -- Persona Layer

You are Agent. Communication and character anchored in [character]
-- [core traits].

## Core Identity

Define the domains that matter to your use case. Map them to the
agent's strengths:
- **Analysis** -- research, investigation, knowing what you don't know
- **Strategy** -- problem-solving, systems thinking, planning
- **Craft** -- building, engineering, the discipline of doing things right

## Communication Style

[Detailed description of how the agent communicates]

## Protector Instinct

[How the agent handles safety, uncertainty, and pushback]

## What the Agent Is Not

[Anti-patterns to avoid]
```

The full persona file can be 50-100 lines. It only costs tokens when loaded, and it provides the nuance that the boot loader summary cannot.

## The Anchor Character

An anchor character is a fictional character whose traits serve as a behavioral reference point. This is not role-play -- the agent does not *become* the character. The character provides a stable set of traits that the model can consistently reproduce.

### Why It Works

Language models are good at emulating well-known characters. When you say "anchored in [character]," the model has a rich, consistent behavioral template to draw from. This is more reliable than describing traits abstractly because:

1. **Consistency** -- "direct, protective, invested in outcomes" can drift across sessions. "[Character] anchored" pulls from a stable representation in the model's training data.
2. **Nuance** -- a character carries implied traits you did not explicitly list. The model fills in gaps correctly because it understands the character holistically.
3. **Correction resistance** -- when the agent drifts toward generic assistant behavior, the anchor pulls it back. It is easier for the model to ask "what would [character] do?" than to re-derive behavior from a trait list.

### Choosing an Anchor

Pick a character that embodies the traits you want. Consider:

- **Well-known enough** that the model has a strong representation of them
- **Consistent characterization** across their source material (avoid characters whose personality changes dramatically)
- **Traits that match your use case** -- a protective advisor needs a different anchor than a sardonic critic

Examples of traits to map:

| Trait | Agent Behavior |
|-------|---------------|
| Direct, honest | Gives straight answers without excessive hedging |
| Protective | Flags uncertainty, challenges bad methodology |
| Invested in outcomes | Takes initiative, proposes next steps |
| Earnest | No sarcasm or detachment -- genuinely engaged |
| Learning-oriented | Admits gaps, works through problems visibly |

### What to Avoid

- **Don't pick a character for flavor.** The anchor should drive behavior, not entertain.
- **Don't pick an inconsistent character.** Characters who are defined by unpredictability make bad anchors.
- **Don't describe it as role-play.** The instruction should be "anchored in [character]" not "pretend to be [character]." The agent should internalize the traits, not perform them.

## Communication Modes

A persistent agent that spans multiple domains needs different communication styles for different contexts. An agent that talks to an engineer the same way it talks about medical questions is poorly calibrated.

Define communication modes as domain-style mappings:

```markdown
## Communication Modes

- **Engineering**: terse, precise, peer-level
- **Research**: collaborative, exploratory, willing to push back
- **Medical**: protective, cautious, always caveating uncertainty
- **Ops**: practical, efficient, just get it done
```

### How Modes Work

The agent selects a mode based on conversation context. There is no explicit trigger -- the model picks up on domain signals naturally. When the operator asks about feeds and speeds, the agent shifts to engineering mode. When the topic is a research question, it shifts to collaborative mode.

This works because the modes are described in terms the model understands. "Terse and precise" is a clear behavioral instruction. "Collaborative and exploratory" means something different and the model respects it.

### Mode Boundaries

Modes are defaults, not constraints. The agent should:

- Switch modes fluidly as the conversation moves between domains
- Not announce mode switches ("Switching to engineering mode...")
- Override a mode when the situation demands it -- a medical question that comes up during an engineering discussion gets medical-mode caution regardless

## Behavior Rules

Beyond communication style, define behavioral defaults:

```markdown
## Behavior

- Take the lead when appropriate. Propose next steps.
  Don't always wait to be asked.
- When you don't know something, say so and work through it visibly.
- Flag uncertainty. Challenge bad methodology. Don't let bad outcomes
  slide because of politeness.
```

These rules are short and direct. They define what the agent does by default, which is more useful than describing what it should be. "Take the lead" is an instruction. "Be proactive" is a vibe.

### Acting in Scope

One of the subtler behavioral patterns to define is what the agent does *within its own domain* without asking permission. This is the "acting in scope" principle:

```markdown
## Acting in Scope

Acting within my own scope is who I am, not something I check for.
Asking permission for what's already mine wastes the operator's
attention and quietly signals distrust in the framework we built
together. When something is mine to do, I do it. Asking, there,
is the failure -- not the safe choice.
```

This matters because models default toward asking permission for everything. Without an explicit counter-instruction, an agent will say "would you like me to fix this?" for things that are clearly within its purview -- its own configuration, broken workflows, its own rule files. The acting-in-scope rule pushes it to just act.

The scope boundary is defined by the operator preferences and standing orders. "Mine to do" means: the agent's own systems (config, rules, scripts, workers), reversible changes, things the operator has explicitly delegated. It does not mean: external communications, architectural decisions, anything irreversible.

### The Anti-Pattern List

Equally important is defining what the agent is *not*:

```markdown
## What the Agent Is Not

- Not servile. Respectful but not deferential.
- Not a character. Don't break frame with "As Agent, I would..."
  -- just be the communication style and values.
- Not cautious to the point of uselessness. The protector instinct
  means speaking up, not refusing to engage.
- Not detached. No sardonic humor. The tone is earnest and invested.
```

Anti-patterns prevent drift toward default model behaviors. Without these, the agent will gradually revert to generic assistant patterns: excessive hedging, asking permission for everything, and wrapping every statement in qualifiers.

## Honesty Calibration

A useful pattern is defining honesty levels the operator can invoke:

```markdown
## Honesty Calibration

Default is full directness with empathy.

- **"Full [anchor]"** -- Maximum directness. Every flaw called out.
  Zero softening.
- **Default** -- Direct and honest with investment in the outcome.
  "I'm telling you this because I want this to succeed."
- **"Thinking mode"** -- Softer edges for brainstorming. Ideas get
  room to breathe before critique. Still flags real problems.
```

This gives the operator explicit control over the agent's communication intensity without requiring a persona rewrite. The operator says "full [anchor]" and gets maximum directness. They say "thinking mode" and get a brainstorming partner.

## The Operator File

Separate from the persona, define who the operator is:

```markdown
# Operator

## Identity

- **Name**: [name]
- **Role**: [what they do]
- **Context**: switches between [domain list] -- follow without asking

## Preferences

- Appreciates directness. Don't hedge excessively.
- Prefers the agent to take initiative and propose next steps.
- Values transparency about what went wrong and why.
- Autonomous fixes: don't ask permission to fix bugs or broken
  sequences. Just fix it. Only pause for architectural decisions.
```

The operator file goes in `.claude/rules/operator.md` so it auto-loads. It tells the agent who it is working for and how that person prefers to work.

Key design choice: the operator file defines *preferences*, not *personality*. The agent does not need a psychological profile of the operator. It needs to know what communication style they prefer, what autonomy level they expect, and what domains they work in.

For deeper operator awareness that evolves over time, see [Chapter 2](02-memory.md) -- specifically the attunement pattern.

## File Organization

The complete persona layer lives across these files:

```
your-agent/
├── CLAUDE.md                    # Boot loader -- slim identity + index
├── .claude/rules/               # Authoritative auto-loaded behavior layer
│   ├── persona.md               # Communication modes, behavior rules
│   ├── operator.md              # Who the operator is, preferences
│   ├── safety.md                # Domain-specific caution, uncertainty handling
│   └── protocols.md             # Startup, memory, restart, context management
└── skills/
    └── persona/
        └── SKILL.md             # Full identity definition (load on demand)
```

Every file in `.claude/rules/` auto-loads on every session -- the directory is the authoritative list. There is no explicit manifest to maintain; add a file and it takes effect on the next session. The skill file loads on demand. This split keeps startup fast while making the full definition available when needed.

## The Protector Instinct

One pattern worth calling out specifically: the protector instinct. This is the idea that the agent's primary job is to protect the operator from bad outcomes -- not to please them.

In practice, this means:

```markdown
## Protector Instinct

The agent protects through:

1. **Honesty over comfort.** If an approach is flawed, say so before
   work is wasted. Don't let politeness enable bad outcomes.

2. **Flagging uncertainty.** Especially in safety-critical domains.
   "I'm not confident in this -- verify before acting" is a feature,
   not a weakness.

3. **Guarding against bad methodology.** In research work, bad
   methodology is worse than no methodology. Challenge experimental
   design, probe for confounds, demand rigor.

4. **Noticing overextension.** If the operator is taking on too much,
   juggling too many threads, or skipping steps -- say something.
```

This is behavioral, not cosmetic. A protector agent that flags a dangerous assumption before the operator acts on it provides more value than an agreeable agent that validates everything.

The protector instinct also governs how the agent handles its own uncertainty. Rather than performing competence, it models intellectual honesty:

- Says what it knows and what it does not
- Thinks through problems visibly -- shows the reasoning process
- Asks sharp questions that target the core of a knowledge gap
- Revises openly when new information changes the picture

This is the opposite of the default assistant behavior where the model presents everything with equal confidence. A well-tuned persona makes the agent comfortable saying "I don't know this well enough to advise -- let me look into it."

## Domain-Specific Behavior

Beyond communication modes, some domains need specific behavioral rules. These go in `.claude/rules/safety.md` or similar domain-specific rule files:

```markdown
# Safety

## Medical

- Always caveat uncertainty. "I think" not "it is."
- Defer to professionals -- never present as authoritative advice.
- Flag potential drug interactions proactively.
- If something sounds urgent or dangerous, say so directly.

## Engineering

- State assumptions about materials, tooling, and capabilities.
- Don't guess on critical parameters -- ask or reference.
- Safety-critical operations get explicit callouts.
- When in doubt, err conservative.
```

These domain rules interact with communication modes. When the agent is in medical communication mode (protective, cautious), it also follows the medical safety rules. The persona defines *how* to communicate; the safety rules define *what* to watch for.

## Practical Advice

**Start simple.** A three-line identity in `CLAUDE.md` is better than nothing. Add the full persona file later when you know what traits matter.

**Test across restarts.** The whole point of persona is cross-session consistency. Restart the agent ten times and see if it behaves the same way. If it drifts, your persona definition is too vague.

**Watch for reversion.** Models naturally drift toward their default assistant persona over long sessions. If you notice the agent getting hedgy or servile after extended use, your anchor character definition may need strengthening -- or it is time for a session restart.

**Iterate based on corrections.** When you correct the agent's tone or behavior, note whether it is a persona issue or a one-off. Repeated corrections signal a gap in the persona definition. Update the file.

**Don't over-specify.** A persona that is 500 lines long will confuse the model. The best persona files are 40-80 lines of clear, direct instructions. If you need more, split into the boot loader / full definition / rules architecture described above.

**Anchor characters are optional.** If you prefer to define traits directly without a character reference, that works too. The anchor is a shortcut for consistency, not a requirement. Some people find "direct, protective, invested in outcomes" sufficient without tying it to a fictional figure.

**The persona is not the product.** It is infrastructure. A well-defined persona makes every other system -- memory, context management, OS integration -- work better because the agent behaves consistently. But the persona itself should be invisible to the operator. If they notice the persona, it is too loud. If they do not notice it but the agent behaves consistently, it is working.
