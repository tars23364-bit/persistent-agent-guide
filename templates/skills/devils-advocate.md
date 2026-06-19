---
name: devils-advocate
description: "Run a structured adversarial review of a proposal before
committing. Trigger on an explicit command (/da, 'red team this', 'stress test
this', 'challenge this') or suggest it proactively before high-stakes,
hard-to-reverse, or suspiciously-clean decisions."
---

# Devil's Advocate Protocol

The most expensive mistakes are the ones you were excited about. This skill
forces a proposal through a structured debate before you commit: one sub-agent
builds the strongest possible case *for* it, a second sub-agent (running with
the [anti-sycophancy](anti-sycophancy.md) directive) is *forbidden from agreeing*
and tears it apart, and the main agent synthesizes the tension for you to
decide. The value is the forced dissent — a single agent reasoning alone will
rationalize; two adversarial roles will not.

## When to Suggest It

Before the operator asks, consider suggesting the protocol when you detect:

- **High stakes** — money, hard-to-reverse architecture, research methodology.
- **Fast agreement** — you agreed without meaningful pushback.
- **No obvious downsides** — a clean plan is suspicious, not reassuring.
- **Operator enthusiasm** — excitement is the number-one blind spot.
- **Irreversibility** — choices that are expensive or impossible to undo.

Suggest it as an offer, not a gate: *"This feels like a devil's-advocate moment
— want me to run the debate before we commit?"* Don't suggest it on routine
tasks or work already in motion.

## Workflow

### 1. Generate a neutral briefing

Write a briefing file (e.g. `~/.agent/debates/briefing-{timestamp}.md`) stating
the proposal, context and constraints, the operator's current position and
reasoning, supporting evidence, known risks, and the files worth reviewing.
Keep it factual — don't bias it toward either side.

### 2. Spawn the Advocate

Spawn a sub-agent (a capable model, in a read-only / plan mode). Prompt it to
build the strongest case **for** the proposal: core argument, supporting
evidence from the briefing and referenced files, anticipated objections with
preemptive responses, and a confidence level (HIGH / MEDIUM / LOW). Tell it the
case will be handed to an adversary — weak arguments hurt it. Save the case to
a file.

### 3. Spawn the Adversary

Spawn a second sub-agent (same model tier, plan mode) with the anti-sycophancy
directive active. Give it both the briefing and the Advocate's case. Hard
constraints:

- It **must** argue against the proposal and is **forbidden** from concluding it's correct.
- It **must** name the single most dangerous *unstated* assumption.
- It **must** propose at least one concrete alternative or modification.
- It must not soften its dissent.

But: finding a flaw is not the same as killing the idea. Require it to
distinguish "this is wrong" from "this is right but fragile *here*." Save the
critique to a file.

### 4. Synthesize

Compile a result: a verdict (PROCEED / PROCEED WITH MODIFICATIONS / RECONSIDER /
STOP), one-paragraph summaries of each side, the strongest flaw, the unstated
assumption, the alternative proposed, the key unresolved tension, and the
agent's own assessment of what matters most. **Present the tension; do not make
the decision for the operator.**

### 5. (Optional) Feed the learnings loop

If the Adversary surfaced a genuine flaw that wasn't previously considered, log
it to your self-improvement / learnings store. If the stakes are high and the
Advocate's case did *not* hold up, offer to package the debate for an
independent external review.

## Design Notes

- **Both roles run in plan mode** — pure reasoning, no file edits, no execution.
- **Never weaken the Adversary's no-agreement constraint.** The moment it's
  allowed to conclude "actually this is fine," the protocol collapses back into
  the single-agent rationalization it exists to prevent.
- **It costs two sub-agent windows.** Reserve it for decisions that warrant the
  spend; don't run it on routine work.
- **Respect a decision already made.** If the operator says "skip it, I've
  decided," stop.
