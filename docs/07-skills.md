# Skills

A persistent agent that operates across multiple domains needs structured
knowledge -- not dumped into one massive prompt, but organized so the right
knowledge loads at the right time. Skills are the mechanism for this: packaged
domain expertise that the agent can access on demand.

This chapter covers skill architecture, the boot loader pattern, slash commands,
and how skills relate to rules and memory.

## What Is a Skill

A skill is a markdown file containing domain-specific knowledge, behavior
rules, and communication calibration for a particular area of expertise. Skills
are not code -- they're structured instructions that shape how the agent
operates in a given domain.

There are two distinct flavors, and understanding the distinction is important
for keeping your context lean:

- **Domain skills** (`skills/`) -- deep expertise files loaded on demand when
  the conversation enters a particular domain. They can be hundreds of lines
  long and never touch context unless relevant.
- **Operational skills** (`.claude/skills/`) -- action-oriented skill files
  that define how to accomplish specific tasks: CLI conventions, tool usage
  protocols, external service patterns. These are often triggered by
  description-matching, and many can also be invoked explicitly as slash
  commands.

Example structure of a skill file:

```markdown
---
name: home-automation
description: "Smart home devices, routines, and integrations.
Trigger when the user discusses lights, thermostats, sensors,
automations, or any home control topic."
---

# Home Automation -- Smart Home Knowledge

The operator has a moderately complex smart home setup. They
understand the basics well but rely on the agent for debugging
automations and integrating new devices.

## Platform Context

- **Hub:** Home Assistant (Docker, Raspberry Pi or NUC)
- **Protocol:** Zigbee (via Zigbee2MQTT), some Wi-Fi devices
- **Voice:** Local wake word for hands-free control
- **Automations:** YAML-based, triggered by time/presence/sensor

## Communication Calibration

The operator is technically capable but not a home automation
expert. Explain non-obvious concepts (like Zigbee mesh health
or automation race conditions) but don't over-explain basic
networking or Docker.

Exception: Electrical work or anything involving mains wiring.
Always defer to a licensed electrician.

## Active Integrations

- Lighting scenes (circadian rhythm, movie mode, away simulation)
- Thermostat scheduling with presence detection
- Door/window sensors feeding a security dashboard
- Energy monitoring via smart plugs

## Key References

When automation topics reference these, load from references/:
- references/device-inventory.md -- all devices and their IDs
- references/automation-patterns.md -- reusable YAML templates

## Safety

- Never assume a device is in a known state -- always query first
- Automations that control locks or garage doors need confirmation
- Power-cycling smart switches can reset paired devices
- When debugging, disable automations before manual testing
```

### Anatomy of a Skill File

Every skill file has:

1. **YAML frontmatter** -- `name` and `description`. The description is
   the most consequential field: it determines when the skill auto-activates.
   Write it as a precise trigger condition, not a prose summary.

2. **Domain knowledge** -- what the agent needs to know about this area.
   Equipment, terminology, active projects, reference material.

3. **Communication calibration** -- how the agent should talk when this skill
   is active. Terse and peer-level for expert domains, protective and precise
   for safety-critical ones, collaborative for shared territory.

4. **Safety rules** -- domain-specific cautions. What to flag, what to never
   guess on, when to defer.

5. **References** -- pointers to supplemental docs that should be loaded when
   specific sub-topics come up. These are lazy-loaded, not read at startup.

## The Boot Loader Pattern

The central design problem: you can't load every skill into context at startup.
A machining skill might be 200+ lines. A medical reference skill might be 300+.
An alignment research skill might be 400+. Loading all of them on every session
would consume thousands of tokens before the conversation even starts.

The solution is a boot loader -- a slim index file that tells the agent what
skills exist and when to load them, without including their full content.

### CLAUDE.md as Boot Loader

Your project's `CLAUDE.md` file serves as the boot loader. It's short -- maybe
50-100 lines -- and contains:

1. A one-line identity statement
2. A pointer to the full persona definition
3. A list of available skills with brief descriptions
4. A list of available slash commands
5. Architecture overview (where things live)

```markdown
# Your Agent

Personal agent framework. [One-line identity description.]

## Identity

[Brief anchor statement.]
Full definition: `skills/persona/SKILL.md`

## Rules

All behavior rules auto-load from `.claude/rules/`:
- `persona.md` -- identity, communication modes, behavior
- `protocols.md` -- startup, memory, relay, restart
- `voice.md` -- toggles, TTS rules, echo suppression
- `safety.md` -- domain cautions, access control
- `operator.md` -- who the operator is, preferences

## Architecture

```
your-agent/
├── CLAUDE.md              # Boot loader (this file)
├── .claude/rules/         # Behavior rules (auto-loaded)
├── skills/                # Domain knowledge
│   ├── persona/SKILL.md   # Full identity definition
│   ├── machining/SKILL.md # CNC, CAD/CAM, shop ops
│   ├── research/SKILL.md  # Research methodology
│   ├── medical/SKILL.md   # Clinical reference
│   └── ops/SKILL.md       # System administration
├── commands/              # Slash commands
├── agents/                # Subagent specs
├── mcp-servers/           # MCP integrations
├── workers/               # Background services
└── references/            # Supplemental docs
```

## Commands

| Command | Description |
|---------|-------------|
| `/status` | System health check |
| `/restart` | Clean restart with handoff |
| `/memory` | Memory management |
| `/brief` | Morning brief |
```

### What Auto-Loads vs What Doesn't

Claude Code has a built-in mechanism for auto-loading: files in `.claude/rules/`
are automatically included in every session's context. This is where you put
rules that apply universally -- persona, protocols, safety, operator info.

Domain skills are different. They live in `skills/` and are **not** auto-loaded.
The agent knows they exist (from the boot loader index) and loads them on
demand when the conversation enters that domain.

Operational skills in `.claude/skills/` sit in the middle: Claude Code surfaces
them as available skills and includes their frontmatter descriptions. The full
skill body is loaded when the description matches or the operator invokes it.

```
Always loaded (rules):            Loaded on demand:
├── .claude/rules/persona.md      ├── skills/machining/SKILL.md    (domain)
├── .claude/rules/protocols.md    ├── skills/medical/SKILL.md      (domain)
├── .claude/rules/safety.md       ├── skills/ops/SKILL.md          (domain)
└── .claude/rules/operator.md     ├── .claude/skills/vault/        (operational)
                                  ├── .claude/skills/google-cli/   (operational)
                                  └── .claude/skills/reminders-cli/ (operational)
```

The distinction matters for context management. Rules consume context on every
session. Skills only consume context when relevant.

### Trigger Conditions

The YAML frontmatter description doubles as a trigger specification. The agent
reads these descriptions and knows when to load the full skill body:

```yaml
---
name: medical
description: "Clinical reference, drug interactions, lab values,
and health monitoring. Trigger when the user discusses symptoms,
medications, lab results, medical procedures, or health concerns."
---
```

For operational skills in `.claude/skills/`, the description is especially
important because it controls *automatic* invocation. Write it as an explicit
trigger list rather than a general summary:

```yaml
---
name: vault
description: "Credential storage and retrieval from the system keychain.
Use whenever a task needs a secret, asks to store/rotate/find a password
or API key, mentions keychain, or when a credential is found in plaintext
on disk and needs to be secured."
---
```

The agent doesn't need explicit trigger logic -- it reads the description and
uses judgment. If the conversation turns to medication interactions, it loads
the medical skill. If a task requires reading an API key, it loads vault.

## Directory Structure

Skills live in their own directories, each with a `SKILL.md` file and optional
supporting files:

```
your-agent/
├── skills/                        # Domain knowledge (loaded by topic)
│   ├── persona/
│   │   └── SKILL.md               # Core identity and communication style
│   ├── machining/
│   │   ├── SKILL.md               # Main skill file
│   │   └── reference/             # Supplemental docs (loaded on demand)
│   │       ├── machine-specs.md
│   │       └── post-notes.md
│   ├── research/
│   │   └── SKILL.md
│   ├── medical/
│   │   └── SKILL.md
│   └── ops/
│       └── SKILL.md
│
└── .claude/skills/                # Operational skills (triggered by description)
    ├── vault/
    │   └── SKILL.md               # Credential access patterns
    ├── reminders-cli/
    │   └── SKILL.md               # External reminder system CLI
    ├── google-cli/
    │   └── SKILL.md               # Calendar and email CLI conventions
    └── market-outlook/
        └── SKILL.md               # Investment research workflow
```

Each skill is self-contained. The `SKILL.md` file is the entry point; anything
in `reference/` is loaded only when specifically needed.

### Operational Skill Patterns

Operational skills in `.claude/skills/` tend to share a common structure:
they're less about domain expertise and more about *how to operate* a
particular tool or service correctly. Key things they capture:

- Exact CLI syntax with working examples
- Gotchas that aren't obvious from docs (discovered through use)
- Hard rules that must never be bypassed
- Output format conventions

A credential-management operational skill, for example, would capture the
exact `security` command syntax for reading and writing keychain entries,
the principle that secrets must never appear in chat or synced messages,
and the rotation workflow the operator expects. It's procedural, not
encyclopedic.

## Slash Commands

Slash commands are a separate mechanism from skills, though they often interact.
A slash command is a user-triggered action defined in a markdown file:

```
commands/
├── status.md    # /status  -- system health check
├── restart.md   # /restart -- clean restart with handoff
├── brief.md     # /brief   -- morning briefing
├── memory.md    # /memory  -- memory management
├── reflect.md   # /reflect -- reflection cycle
├── switch.md    # /switch  -- model swap with context handoff
├── imsg.md      # /imsg    -- process message queue
└── log.md       # /log     -- activity review
```

### Command File Structure

Each command file defines the behavior for a slash command:

```markdown
# /status -- System Status

Quick health check on the agent environment and connected services.

## Behavior

Check and report:

### Core
- Current working directory and git status
- Disk space on primary volumes
- Running services (message watchers, MCP servers)

### Services
- **iMessage watcher:** `pgrep -f imessage_watcher`
- **Telegram listener:** `pgrep -f telegram_listener`
- **Push notifications:** configured and reachable
- **MCP servers:** list running MCP processes

### Output Format

```
Agent -- Status
━━━━━━━━━━━━━━
Environment: Mac Mini / macOS
Directory:   ~/your-agent
Git:         main (clean) | 3 ahead

Services:
  iMessage watcher  ● running (pid 12345)
  Telegram listener ● running (pid 12346)
  Push notifications ● configured
  MCP servers       voice, camera, pushover

Disk: 847 GB free / 1 TB
```

Clean dashboard. No prose.
```

### How Slash Commands Work

Claude Code supports custom slash commands as markdown files in the `commands/`
directory. When the user types `/status`, Claude Code loads `commands/status.md`
and the agent follows its instructions.

This is a declarative pattern -- you describe the desired behavior in markdown,
and the agent implements it. No code needed for the command itself, just clear
instructions.

### Commands vs Skills

| | Domain skills | Operational skills | Commands |
|---|-----------|-----------|----------|
| **Location** | `skills/` | `.claude/skills/` | `commands/` |
| **Purpose** | Domain expertise | Tool/service procedures | Discrete actions |
| **Triggered by** | Topic detection | Description match or explicit | User types `/command` |
| **Loaded when** | Conversation enters domain | Matching trigger fires | `/command` invoked |
| **Persistent** | Stays active for the session | Stays active for the session | Executes once |
| **Examples** | Machining, medical reference | Credential access, CLI patterns | Status check, restart, voice toggle |

A command might *use* a skill. A `/brief` command might load calendar data and
present it using the communication style defined in the persona skill. An `/imsg`
command might invoke the medical-research operational skill if the incoming
message contains a medical query. They're separate mechanisms that compose.

## The Persona Skill

The persona skill is special -- it defines the agent's core identity and
communication style. While a brief version lives in `.claude/rules/persona.md`
(auto-loaded), the full definition lives in `skills/persona/SKILL.md`.

The rules file contains the essentials: identity anchor, communication modes,
core behavior. The skill file contains the deeper definition: detailed
communication calibration, protector instinct details, learning behavior, what
the agent is and isn't.

```
.claude/rules/persona.md          skills/persona/SKILL.md
─────────────────────────          ────────────────────────
Identity anchor                    Full identity definition
Communication mode list            Detailed style guide per domain
Core behavior rules                Protector instinct breakdown
                                   Learning behavior model
                                   Anti-patterns (what NOT to be)
                                   Honesty calibration levels
```

The split means the agent always has its core identity loaded (from rules) but
can access the full depth when needed (from the skill file). For most
interactions, the rules-level persona is sufficient. For calibration questions
("be more direct" or "switch to full critique mode"), the agent loads the full
skill.

## Building Your Own Skills

### Step 1: Identify the Domain

A skill should cover a coherent domain of expertise. Good skill boundaries:

- **Too broad:** "Engineering" (covers too many sub-domains)
- **Too narrow:** "6061 Aluminum Feed Rates" (this is reference data, not a skill)
- **Right size:** "CNC Machining" (clear domain, specific communication needs)

### Step 2: Define Communication Calibration

The most valuable part of a skill isn't the factual knowledge -- the agent
already knows most facts. It's the **communication calibration**: how to talk
about this domain with your specific operator.

Ask yourself:
- Is the operator an expert here? (Be terse, peer-level)
- Is this shared territory? (Be collaborative, push back)
- Is the operator a novice? (Be educational, thorough)
- Is this safety-critical? (Be protective, explicit about uncertainty)

### Step 3: Capture Active Context

Skills aren't just static knowledge. They include:
- **Active projects** the operator is working on in this domain
- **Equipment and tools** that are relevant
- **Known preferences** ("always use imperial units for cabinet work")
- **Reference pointers** to supplemental docs

This context changes over time. Update skills as projects evolve. The agent's
memory system (see [Chapter 2](02-memory.md)) handles day-to-day context; skills
capture the stable background.

### Step 4: Set Safety Boundaries

Every skill should define what the agent should and shouldn't do in that domain:

```markdown
## Safety

- Never guess on [critical parameters]
- Always state assumptions about [domain-specific variables]
- Flag when operating outside confidence zone
- Defer to [professionals/experts] for [specific decisions]
```

### Template

```markdown
---
name: your-domain
description: "Brief description of the domain and trigger conditions.
List specific topics, terms, or scenarios that should activate this skill."
---

# Domain Name -- Knowledge & Reference

[One paragraph: what this domain is, operator's expertise level,
communication calibration.]

## Context

- **Tools/Equipment:** [relevant items]
- **Active Projects:** [current work in this domain]
- **Key Terminology:** [domain-specific terms the operator uses]

## Communication Calibration

[How the agent should communicate in this domain. Expert peer?
Collaborative partner? Protective advisor?]

## Key Knowledge

[Domain-specific facts, patterns, or reference material the agent
should know. Keep this focused -- don't dump a textbook.]

## References

[Pointers to supplemental docs loaded on demand:]
- `references/some-doc.md` -- [when to load it]

## Safety

[Domain-specific cautions and boundaries:]
- [What to never guess on]
- [When to defer]
- [Safety-critical callouts]
```

## Building Skills From Research

When the agent needs to learn a new technical domain (an API, a protocol, a
tool), the temptation is to have a subagent research the topic and produce a
complete skill file. This sounds efficient but produces a subtle problem: the
resulting skill file looks authoritative but lacks the operational knowledge
that makes existing skills trustworthy.

Every good skill file was *earned*. The machining skill knows about specific
post-processor quirks because someone hit the wall. The HPC skill knows which
package manager works because someone tried the wrong one first. A subagent
reading official documentation cannot manufacture this kind of calibrated,
failure-informed knowledge.

### The Split: Reference Docs vs Skill Files

The pattern that works is a two-tier approach:

**Tier 1: Automated reference docs.** A subagent researches the domain and
produces a comprehensive reference document — API surfaces, function signatures,
code examples, known limitations. Save this to `references/`. This is verifiable,
mechanical, and exactly what subagents are good at. It captures 80% of the
token-saving value (no re-researching every session).

**Tier 2: Scaffold skill files.** The same subagent produces a *skeleton* skill
file with frontmatter, section headers, and a brief overview derived from the
research. But the operational sections — gotchas, communication calibration,
domain-specific patterns — are left as placeholders. Mark it explicitly:

```yaml
---
name: some-domain
status: scaffold
description: "Domain API integration — built from research, not experience yet"
last-verified: 2026-03-19
---
```

The skill file fills in over time as the operator actually uses the domain with
the agent. Real gotchas replace placeholder text. Communication calibration
emerges from actual conversations. The reference doc feeds the skill file across
sessions — don't collapse that natural progression into a single subagent call.

### Why This Matters

The distinction isn't about conciseness vs detail. It's about **source of
authority**. A reference doc's authority comes from the upstream documentation.
A skill file's authority comes from experience. Conflating the two degrades the
trust level of your entire skills system — the agent starts treating summarized
documentation as equivalent to hard-won operational knowledge, and the quality
bar silently erodes.

Automate the reference docs. Earn the skill files.

## Skills, Rules, and Memory: When to Use What

Four systems store agent knowledge. They serve different purposes:

| System | Durability | Scope | Content |
|--------|-----------|-------|---------|
| **Rules** (`.claude/rules/`) | Permanent | Every session | Universal behavior, identity, protocols |
| **Domain skills** (`skills/`) | Permanent | On-demand by topic | Domain expertise, communication calibration |
| **Operational skills** (`.claude/skills/`) | Permanent | On-demand by trigger | Tool/service procedures, CLI patterns |
| **Memory** (graph DB) | Persistent, decaying | Recalled by query | Decisions, preferences, facts, context |

### Decision Tree

**"Should every session know this?"**
- Yes -> Rules file (`.claude/rules/`)
- No, only when in this domain -> Skill file
- No, it's a specific fact or decision -> Memory

**"Is this stable knowledge or evolving context?"**
- Stable (operator identity, safety rules) -> Rules or Skills
- Evolving (current projects, recent decisions) -> Memory

**"Does this affect how the agent communicates?"**
- Yes, universally -> Rules (persona.md)
- Yes, in a specific domain -> Domain skill (communication calibration section)
- No, it's just information -> Memory

**"Is this about how to operate a tool correctly?"**
- Yes, and it applies across multiple domains -> Operational skill (`.claude/skills/`)
- Yes, but it's specific to one domain -> Domain skill reference section

### Example: Operator Prefers Imperial Units

This preference affects the machining domain specifically:
- **Not in rules** -- it's domain-specific, not universal
- **In the machining skill** -- "always use imperial units for dimensions"
- **Also in memory** -- so the agent can recall it even if the skill isn't loaded

The overlap between skills and memory is intentional. Skills capture stable
patterns; memory captures the specific instances. When the agent loads a skill,
it gets the stable rules. When it queries memory, it gets the recent context.
Together, they give the agent both the map and the territory.

## Multi-Skill Sessions

The operator may switch domains mid-conversation -- discussing machining, then
pivoting to a medical question, then asking about system status. The agent
should handle these transitions naturally:

1. Detect the domain shift from conversational context
2. Load the relevant skill if not already loaded
3. Adjust communication style according to the new skill's calibration
4. Don't announce the transition ("I'm now switching to medical mode") -- just
   do it

Skills stack. If the operator asks a machining question that involves
safety-critical parameters, both the machining skill and the safety rules
apply simultaneously.

## Worked Examples: Battle-Tested Skill Patterns

Most skills are domain knowledge — what the agent knows about *your* machining
shop, *your* medical history, *your* codebase. Those don't generalize, and you
shouldn't try to copy them. But a handful of skills are *behavioral* — they
encode how the agent should reason, not what it knows — and those travel well.
Four that have earned their place through heavy use are included as starter
templates in `templates/skills/`. Each is invoked as a slash command and each
exists to counter a specific, predictable failure mode.

### Anti-Sycophancy ([`anti-sycophancy.md`](../templates/skills/anti-sycophancy.md))

A session-scoped switch that turns off softening and turns on direct, calibrated
assessment: correct errors immediately, say "I don't know," challenge flawed
premises, match confidence to certainty, stop when done. **Failure mode it
counters:** instruction-tuned models drift toward agreement, and an agent that
agrees with everything launders your bad ideas back to you with a confident
tone. Making it an explicit *mode* (rather than the default) keeps directness
available on demand without making every routine exchange abrasive.

### Devil's Advocate ([`devils-advocate.md`](../templates/skills/devils-advocate.md))

A structured adversarial debate before a high-stakes commit: one sub-agent
builds the strongest case *for* a proposal, a second sub-agent (running with the
anti-sycophancy directive and **forbidden from agreeing**) tears it apart, and
the main agent synthesizes the tension for you to decide. **Failure mode it
counters:** a single agent reasoning alone rationalizes — especially when you're
excited about the idea. Two adversarial roles can't. The hard rule is that the
adversary is never allowed to conclude "actually this is fine"; the moment it
can, the protocol collapses into the rationalization it exists to prevent.

### Quarantine Scout ([`quarantine-scout.md`](../templates/skills/quarantine-scout.md))

Untrusted external content (a repo, a package, a URL) is analyzed in a sandboxed
container by a *different, local* model first; only a structured report — never
the raw content — reaches the main agent, and a plan-mode sub-agent audits that
report before the agent trusts it. **Failure mode it counters:** an agent with
real authority is a prompt-injection target, and the moment it reads a hostile
README that payload is in its context. Sandbox isolation plus model diversity
plus an audit pass are three independent barriers between "untrusted input" and
"trusted context."

### Session Continuity ([`session-continuity.md`](../templates/skills/session-continuity.md))

Three artifacts at three timescales that make a restart cheap: a **task lock**
(the next concrete step, mid-task, so a session that dies resumes exactly where
it was), a **handoff** (pointers + the *why* behind decisions + surprises, in
past tense, for the next session and the operator), and an optional **pulse**
(one-line same-day texture). **Failure mode it counters:** a persistent agent
restarts constantly — context fills, tasks finish, the machine reboots — and
without continuity discipline every restart is a cold start that quietly kills
long-running work. With it, restarting becomes routine hygiene you reach for
*proactively* to clear a degrading context window, not a loss event you dread.

The common thread: each is a small, explicit ritual that makes a predictable
weakness — sycophancy, lone-agent rationalization, prompt injection, lost
context across restarts — harder to walk into. They compose: the devil's-advocate
adversary runs *with* anti-sycophancy active, the scout leans on the same
plan-mode sub-agent pattern as the debate, and continuity is what lets the other
three survive the restart that clears a full context window. Adapt the paths and
model choices to your setup; the structure is what matters.

## Scaling Considerations

For a personal agent with 3-5 domains, the skill system described here works
well. If you're building something with dozens of skills:

- **Index file.** Generate a slim index of all skills with their trigger
  descriptions. Load only the index at startup. This is what the CLAUDE.md
  boot loader already does at a small scale.

- **Skill groups.** Cluster related skills and load them together. "Shop" might
  load machining + inventory + quoting as a group.

- **Skill versioning.** If skills change frequently, track versions so you can
  roll back when something breaks.

- **Dynamic skill loading.** For very large skill sets, the agent could search
  an index rather than scanning descriptions. This approaches a RAG pattern but
  with structured skill files instead of raw documents.

For most personal agents, none of this is necessary. The boot loader pattern
with a handful of skills is sufficient and keeps complexity low.
