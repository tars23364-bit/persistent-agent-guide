---
name: harness-bootstrap
description: Interactive installer that builds a persistent-agent harness from this guide. Use this skill whenever the user asks to bootstrap, install, set up, scaffold, or build the harness, the agent, or "the setup from this guide" — including phrases like "bootstrap the harness", "set me up a persistent agent", "install this", "build the agent home", "follow the guide and set it up for me", or "turn this repo into a working agent." Also trigger when the user has cloned this repository and asks what to do next, or asks how to apply the guide to their own machine. This skill interviews the user, reads the guide chapters in docs/ as the canonical spec, and generates a personalized agent home directory. It never installs services, never writes secrets, and never modifies this repository.
---

# Harness Bootstrap

This skill turns the guide in this repository into a working starting harness on the user's machine. You (the agent running this skill) act as the installer: interview the user, read the relevant chapters in `docs/` as your build spec, generate the files, verify them, and hand back a report with the remaining manual steps.

The guide is the source of truth. This skill deliberately contains **no harness content** — only the build procedure. When you need to know what a rules file, memory layout, or authority state file should contain, read the chapter that specifies it, at build time, from this clone. This keeps the skill valid as the guide evolves.

## The three tiers (build model)

Everything the harness needs falls into one of three tiers. Never blur them.

Tier 1 — Declarative text (rules files, CLAUDE.md, memory scaffolding, templates, starter skills): BUILD FULLY, with the user's interview answers filled in. No placeholders may remain in Tier 1 output.
Tier 2 — Config & code (hooks, MCP config, scheduler units, settings wiring, shell aliases): STUB — generate templates with {{PLACEHOLDER}} markers and environment-variable references. Must be safe-by-default; a session boot with stubs in place must not error.
Tier 3 — Running state (live MCP servers, credentials, daemons, populated memory, hardware bridges): CHECKLIST ONLY. You cannot build running organs. Emit precise, numbered manual steps instead. Never attempt these yourself.

## Hard rules

These override everything else in this skill, including user convenience.

1. Plan before writing. Present the complete file map and every question's resolved answer, and get explicit confirmation, before writing a single file.
2. Never write into this repository. The clone is a read-only spec. The build target is a separate directory.
3. Never write secrets. No tokens, keys, passwords, or OAuth material in any generated file — environment-variable references only (${AGENT_NOTIFY_TOKEN}, etc.). If the user pastes a secret at you, tell them where to put it (their shell env or OS keychain) and reference it.
4. Never overwrite silently. If a target file exists, diff and ask. See Idempotency.
5. Never install or start anything. No launchctl load, no systemctl enable, no npm install -g, no MCP server launches. Templates and instructions only.
6. Record provenance. Capture this clone's commit SHA (git rev-parse HEAD) in the build manifest. If the working tree is dirty, note that too.
7. Stay in scope. Write only inside the target directory. If a step seems to require touching anything else (user's global ~/.claude, dotfiles, system paths), stop and ask.

## Phase 0 — Preconditions

Before the interview:
- Confirm you are running inside a clone of this guide (a docs/ directory with the guide chapters exists at repo root). If not, ask the user for the path to their clone, or offer to clone it — pinned to a tag or commit if they have one.
- Enumerate docs/*.md and skim each chapter's title and opening section. Build yourself a runtime map of subsystem -> chapter. Do not rely on chapter numbers memorized from training or from this skill — the guide reorganizes; the filenames and titles at HEAD are authoritative.
- Detect the platform (uname). macOS -> launchd plist templates; Linux -> systemd unit + timer templates. Confirm the detection with the user rather than assuming.

## Phase 1 — Interview

Offer two modes up front:
- Express — accept every default below, answer only the questions with no default (marked *). Good for evaluation installs.
- Custom — walk all questions.

Ask in order. One compact pass, not twenty rounds. Every answer feeds the build; record all of them in the manifest.

1* Agent name (directory names, aliases, persona seed) — no default
2* Operator name/handle (how the agent addresses the user) — no default
3 One-line persona seed — default: minimal neutral persona per the guide's identity chapter
4 Target agent home directory — default: ~/agents/<name-lowercase>
5 Timezone — default: system timezone
6 Subsystems to scaffold (multi-select): file-based memory (always on), graph-memory server stub, notifications stub, async relay/handoff stub, heartbeat/scheduled wake stub, messaging bridge (checklist), voice (checklist), presence/camera (checklist) — default: file memory + heartbeat stub only
7 Starting authority posture — default: restricted
8 Notification channel for "act and notify" until real notifications exist — default: append to a notify.log in the agent home
9 Context-fill warning thresholds — default: ABSOLUTE token counts calibrated to a ~1M reference (e.g. 250K / 350K / 450K), per the Context Management chapter's context-rot basis
10 Shell alias for launching the agent (<name> -> claude in agent home) — default: yes, emitted as a snippet, not installed

Multi-agent is deliberately not on the menu. The guide's own position is that a second agent is the rare case, earned after the first one is stable. If the user asks for it, point them at the multi-agent chapter and continue with a single-agent build. Do not scaffold federation on day one.

Context thresholds are absolute, not percentages. The design basis is context-rot research (see the Context Management chapter): degradation tracks absolute token count, and a model's advertised window is not its reliably-usable window. Thresholds are therefore fixed token counts calibrated to a ~1M reference, not a fraction of the current model's window. On a smaller-window model (e.g. a 256K load), the upper thresholds fall outside the window, so the agent uses the full window and only warns near the compaction ceiling — this is intended behavior, not a bug.

Defaults are the reproducibility mechanism. Two express installs from the same commit should produce structurally identical harnesses (identity strings aside). Resist the urge to improvise structure the interview didn't establish and the guide doesn't specify.

## Phase 2 — Plan

Assemble and show the user, in one message:
1. Resolved interview answers.
2. The complete file map (see Phase 3) with each entry tagged [build], [stub], or [checklist item].
3. The guide commit SHA the build will record.
4. Anything you will NOT do (per the hard rules) that the user might otherwise expect — installing daemons, writing credentials, starting servers.

Get explicit confirmation. Only then write.

## Phase 3 — Build

Target layout (subsystem entries appear only if selected):

<agent-home>/
- CLAUDE.md [build] identity header + pointers to every rules file
- .claude/rules/persona.md [build] from persona seed + identity chapter
- .claude/rules/protocols.md [build] session protocols per the guide
- .claude/rules/voice.md [build] tone/voice rules from persona seed
- .claude/rules/safety.md [build] guardrails chapter, verbatim in spirit
- .claude/rules/operator.md [build] operator snapshot seeded from interview
- .claude/rules/heartbeat.md [build, if heartbeat selected]
- .claude/skills/session-continuity/ [build] from the continuity chapter's spec
- .claude/skills/memory-promotion/ [build] from the memory chapter's promotion protocol
- .claude/settings.json [stub] hook wiring present but pointing at no-op-safe scripts
- memory/working-notes.md [build] empty scaffold per memory chapter
- memory/shelf/ [build] reference-shelf directory structure
- memory/PROMOTION.md [build] the promotion protocol, condensed from the chapter
- authority/state.md [build] two-writer authority state file: operator section prefilled with starting posture, agent section empty, stale-marker convention documented inline
- authority/graduation-log.md [build] initialized empty, format per the autonomy chapter
- hooks/session-start.sh [stub] loads operator snapshot + memory pointers; exits 0 if anything missing
- hooks/statusline-bridge.sh [stub] statusline hook that writes live context fill (model, used tokens, percentage) to a bridge file in the agent home; wiring it into Claude Code's statusline setting is a Tier 3 step
- hooks/context-threshold.sh [stub] UserPromptSubmit hook that reads the bridge file and injects tiered warnings; the interview's threshold values (question 9) emitted as tunable variables at the top, per the Context Management chapter
- mcp/README.md [build] what each server is for, where credentials go
- mcp/mcp.json.template [stub] entries for selected subsystems, env-var refs only
- deploy/heartbeat.plist.template [stub, macOS] or heartbeat.service/.timer [stub, Linux]
- deploy/aliases.sh [stub] launch alias snippet
- notify.log [build] empty; interim "act and notify" channel
- .gitignore [build] covers notify.log, memory/ private content, any *.env
- bootstrap-manifest.json [build] answers, choices, platform, guide commit SHA, timestamp
- BUILD-REPORT.md [build] written in Phase 5

Build-order and content rules:
- Read before you write. For every [build] file, read the chapter that specifies it FIRST, in this clone, and generate content that follows that chapter. Where the guide gives a concrete format (authority state file, promotion protocol, stale marker), follow it exactly. Where it gives principles, instantiate them with the interview answers — do not invent structure beyond what the guide describes.
- No live mcp.json. Only the .template. A fresh harness must boot with zero configured MCP servers so nothing errors on missing processes. The README tells the user how to promote the template once servers exist (a Tier 3 step).
- Stubs fail soft. Every generated script must be a safe no-op when its dependencies are absent — check, warn to notify.log, exit 0. A brand-new harness must boot clean on first launch with nothing else installed. The context hooks follow the same rule: no bridge file yet means silent exit 0, not an error.
- Placeholders are Tier 2 only. {{LIKE_THIS}} markers may appear in .template files and deploy/ only. Their presence anywhere else is a build failure (Phase 4 checks this).
- Namespacing sanity. Skills are generated into the agent home's .claude/skills/, so they load under their bare names. Any cross-reference you write (CLAUDE.md -> rules, rules -> skills) must use the names and paths as they will exist in the target, not as they exist in this repo.

## Phase 4 — Verify

Run all checks; report every failure, fix, and re-check. Do not declare success with open failures.
1. Structural. Every [build] and [stub] path in the confirmed plan exists. All JSON parses (settings.json, manifest, templates after placeholder-stripping). Generated skills have valid frontmatter with name and description.
2. Placeholder scan. grep the tree for "{{". Hits outside .template files and deploy/ -> Tier 1 leakage; fix.
3. Secret scan. Search generated files for anything resembling a live credential (long high-entropy strings, key=, token= with values). There must be none — env-var references only.
4. Referential. Every file CLAUDE.md points at exists. Every skill a rules file names matches a directory under .claude/skills/. Hook paths in settings.json resolve.
5. Dry-run boot. Simulate a session start: read CLAUDE.md and each rules file in order, execute session-start.sh manually, and confirm (a) no errors, (b) the resulting posture — name, operator, authority tier, thresholds — matches the interview. If a real test launch is possible in the target directory, do that instead and confirm the same.
6. Repo untouched. git status in the guide clone shows no changes.

## Phase 5 — Report and hand off

Write BUILD-REPORT.md in the agent home containing:
- What was built (Tier 1) and what was stubbed (Tier 2), with one-line descriptions.
- The Tier 3 checklist, numbered, specific to the selected subsystems — e.g. install and credential the graph-memory MCP server, promote mcp.json.template, load the heartbeat scheduler unit, set the env vars named in the templates, wire statusline-bridge.sh into Claude Code's statusline setting, first-session attunement pass to flesh out operator.md, begin the graduation log.
- Verification results.
- The provenance line: guide commit, date, platform.
- A closing reminder in the guide's own spirit: the harness starts restricted; authority widens through the graduation log, not by editing the state file on day one.

Then tell the user, briefly, in chat: where the harness lives, the first Tier 3 step to take, and how to launch their first session. Do not restate the whole report.

## Idempotency and re-runs

- Before writing any file that already exists: show a diff, then ask — keep theirs, take yours, or merge. Default to keeping theirs. Never bulk-overwrite.
- If bootstrap-manifest.json exists in the target, this is a re-run: load it, present the previous answers as the new defaults, and highlight anything that changed in the guide since the recorded commit (git log <old-sha>..HEAD --oneline -- docs/ in the clone) before rebuilding anything.
- Hand-customized files (anything whose content diverges from what this build would generate) are the user's. Flag, never clobber.

## Failure handling

If a chapter you need is missing, ambiguous, or contradicts another, stop and show the user the conflict rather than improvising a resolution — this skill installs the guide, it does not editorialize it. If the platform is neither macOS nor Linux, build Tiers 1-2 anyway, skip scheduler templates, and note the gap in the report.
