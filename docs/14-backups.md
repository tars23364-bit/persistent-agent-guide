# Backups & Durability

A persistent agent accumulates something no other software on the machine has: months of operator-specific state that exists nowhere else. The code is in git. The model is downloadable. But the memory graph, the tuned configuration, the task history, the accreted decisions — those live on one disk, and that disk will eventually fail. This chapter covers how to back up the irreplaceable state without backing up the regenerable bulk, where to send the copies, and how to verify a backup is actually a backup and not just a hope.

## The Problem

When the primary machine dies, here is what you do and do not lose.

**Not lost** — anything under version control. The agent's source directory (`~/your-agent/`) is a git repo: `CLAUDE.md`, the rules files, hooks, workers, scripts, skills, commands. If you have been pushing to a remote (you should be — see the cold-start git push in [OS Integration](04-os-integration.md)), this survives a dead disk for free.

**Lost forever, if you did nothing** — the state directory (`~/.agent/`) and the memory store. This is not in git, by design ([Memory Architecture](02-memory.md) explains why logs and runtime state stay out of version control). It contains:

- **The graph memory store** — months of slowly-accreted preferences, decisions, insights, and entity links. This is the single most valuable artifact the agent owns. You cannot rebuild it. It was created one fact at a time over hundreds of sessions.
- **Task files and history** — in-flight work, completed-task context, the commitments the agent is tracking.
- **Learnings and reflections** — the corrections and patterns the self-improvement system has captured ([Self-Improvement](10-self-improvement.md)).
- **Tuned file-based state** — handoff conventions, pulse history, toggle defaults.

The asymmetry is the whole point. Code and checkpoints are *reproducible*: re-clone, re-download, re-train. The memory graph is *not*. A backup strategy that treats both the same wastes time and disk on the reproducible half while putting the irreplaceable half at the same risk as everything else. Back up what you cannot rebuild.

## What to Back Up vs. What to Skip

| State | Back up? | Why |
|-------|----------|-----|
| Graph memory store | **Yes — and offsite** | Irreplaceable, accreted over months. The crown jewels. |
| `~/.agent/` state dir (tasks, learnings, handoffs, pulse) | **Yes** | Not in git, not reproducible. |
| Agent source tree (`~/your-agent/`) minus excludes | **Yes** | In git, but a local snapshot speeds full-machine recovery. |
| Operator-specific working dirs (notes, project docs) | **Yes** | Often not in git; irreplaceable if hand-written. |
| Model checkpoints / training artifacts | **No — exclude** | Large and reproducible. Stored elsewhere (training host, object storage) or re-trainable. |
| `node_modules/`, `.venv/`, `venv/`, `__pycache__/` | **No — exclude** | Regenerable from lockfiles. Pure bulk. |
| `*.pyc`, `.DS_Store`, caches | **No — exclude** | Junk. |
| Socket files, transient flags (`*.sock`, speaking/suppress flags) | **No — exclude** | Meaningless off the live machine. |
| Secret files, credential dirs, API tokens | **No — exclude, hard rule** | A backup that contains secrets leaks them to a second host or the cloud. See [Safety](08-safety.md). |

The two exclusion categories below each have a war story attached. Read them before you tune your own exclude list — both were learned the expensive way.

### Large regenerable files: a correctness *and* a hardware lesson

The first backup run on the reference system OOM-crashed. The cause was not obvious. macOS ships `openrsync`, not GNU `rsync`, and `openrsync` builds the **entire file list in memory before transferring a single byte**. Point it at a tree containing tens of GB of model checkpoints on a RAM-constrained machine and the file-list allocation alone is enough for the kernel's memory pressure killer (jetsam) to take the process out. The backup never even started copying.

The fix was the same as the correct design choice: **exclude the large, regenerable directory.** Those checkpoints were reproducible — they also existed on the training host and could be re-trained. Excluding that one directory dropped the backup from ~65 GB to ~7 GB and took the run time from "crashes" to about five minutes.

Two takeaways:

1. **Exclude reproducible bulk on principle**, not just to save space. Checkpoints, caches, virtualenvs, and `node_modules` belong to other recovery paths (lockfiles, re-download, re-train). Your backup is for the irreplaceable state.
2. **Know which `rsync` you're running.** If the destination is missing GNU `rsync`, install it — `brew install rsync` on macOS, your package manager on Linux. GNU `rsync` streams the file list incrementally and does not have the same up-front RAM spike. On a memory-constrained primary, this is not optional.

### Secrets: exclude them the same way git does

A backup of an agent that touches API keys, tokens, or network device credentials will happily copy those secrets to a second machine and an offsite target — multiplying the places a leak can happen. Treat the backup exclude list as an extension of your `.gitignore` and your secrets discipline ([Safety](08-safety.md)):

- Exclude any pre-redaction backup files (e.g. `*.presweep-bak`) — they may contain unmasked secrets.
- Exclude project subdirectories that hold device configs or API keys.
- Do **not** add `~/.ssh`, credential config dirs, or token stores as backup *sources* in the first place. The safest secret is the one the backup job never sees.

## Architecture

Two targets, two purposes. A nightly full-tree snapshot to a second machine on the LAN gives you fast, whole-system recovery. A daily copy of just the memory store to an offsite target protects the one asset you can never rebuild against a site-level loss (theft, fire, both machines on the same surge).

```
PRIMARY MACHINE (~/your-agent, ~/.agent, memory store, working dirs)
   │
   ├── nightly  ─── rsync over ssh ───►  BACKUP HOST (second machine on LAN)
   │   (full tree, minus excludes)        /home/user/agent-backup/
   │                                      → fast full recovery
   │
   └── daily   ─── copy memory only ──►  OFFSITE TARGET (object storage / cloud drive)
       (graph store snapshot)             → survives loss of both LAN machines
                                          → the irreplaceable asset, doubly protected
```

The LAN snapshot is broad and fast to restore from but lives in the same building. The offsite copy is narrow (just the memory graph) but survives a disaster that takes out the whole site. Together they cover the two failure modes that actually happen: a single disk dies (restore from LAN) and a site-level loss (restore memory from offsite, rebuild the rest from git).

## The rsync Snapshot

A single bash script, run nightly by launchd. It rsyncs each source directory to a matching subdir on the backup host, applies the exclude list, logs a per-source summary, and records a `FAILED` line on any non-zero exit so failures are never silent.

```bash
#!/usr/bin/env bash
# backup-to-host.sh — nightly rsync of agent state to a LAN backup host
#
# Sources: ~/your-agent, ~/.agent, plus operator working dirs, plus memory store
# Target:  user@backup-host:/home/user/agent-backup/
#
# EXCLUSIONS:
#   - *.presweep-bak              (may contain pre-redaction secrets)
#   - <project>/secrets-or-creds/ (device configs / API keys)
#   - <project>/checkpoints/      (large, reproducible, stored elsewhere)
#   - node_modules/ .venv/ venv/ __pycache__/ *.pyc .DS_Store (bulk/junk)
#   - transient flags + socket files
#
# NOT a source (intentional — stays off backup): ~/.ssh, credential config dirs

set -euo pipefail

REMOTE_HOST="user@backup-host"
REMOTE_BASE="/home/user/agent-backup"
LOG_FILE="${HOME}/.agent/logs/backup-to-host.log"

mkdir -p "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# Args: <local-src> <remote-subdir>
rsync_source() {
    local src="$1" dest_name="$2"
    local dest="${REMOTE_HOST}:${REMOTE_BASE}/${dest_name}/"

    if [[ ! -e "$src" ]]; then
        log "SKIP $src — does not exist locally"
        return 0
    fi

    log "rsync: $src → $dest"

    local out exit=0
    out=$(rsync \
        -az \
        --delete \
        --delete-excluded \
        -e ssh \
        --exclude='*.presweep-bak' \
        --exclude='secrets/' \
        --exclude='checkpoints/' \
        --exclude='node_modules/' \
        --exclude='.venv/' \
        --exclude='venv/' \
        --exclude='__pycache__/' \
        --exclude='*.pyc' \
        --exclude='.DS_Store' \
        --exclude='*.sock' \
        --exclude='*.socket' \
        --stats \
        "$src/" "$dest" 2>&1) || exit=$?

    if [[ $exit -ne 0 ]]; then
        log "FAILED rsync $src → $dest (exit $exit)"
        echo "$out" | tee -a "$LOG_FILE"
        return $exit
    fi

    local summary
    summary=$(echo "$out" | grep -E \
        'Number of files|files transferred|Total.*file size|Literal data' | head -6)
    log "OK — $dest_name:"
    echo "$summary" | while IFS= read -r line; do log "  $line"; done
}

log "======================================== backup START"
OVERALL=0
rsync_source "${HOME}/your-agent"  "agent-src"   || OVERALL=$?
rsync_source "${HOME}/.agent"      "agent-state" || OVERALL=$?
rsync_source "${HOME}/notes"       "notes"       || OVERALL=$?
rsync_source "${HOME}/.agent-memory" "memory"    || OVERALL=$?

if [[ $OVERALL -ne 0 ]]; then
    log "FAILED backup completed with errors (exit $OVERALL)"
else
    log "backup COMPLETE — all sources synced"
fi
log "======================================== backup END"
exit $OVERALL
```

### Notes on the rsync flags

- **`-az`** — archive mode (preserve timestamps, permissions, symlinks) plus compression over the wire. Compression matters on a LAN only marginally, but it is free for text-heavy state.
- **`--delete` and `--delete-excluded`** — make the backup a *mirror*, not an accumulating pile. A file deleted on the primary is deleted on the backup. **This is a double-edged sword:** if a bug or a bad command nukes your memory store on the primary, the next nightly run faithfully nukes the backup copy too. A mirror protects against disk failure, *not* against bad writes. If you want protection against accidental deletion, add a versioned or snapshot-style target (a second timestamped copy, or a filesystem with snapshots like ZFS/APFS on the backup host) — do not rely on the mirror alone for that.
- **`--stats`** — emits the file-count and byte-count summary the script greps for. This is your verification surface (next section).
- **Per-source subdirs** — mirroring each source into its own named subdir (`agent-src/`, `agent-state/`, `memory/`) keeps the backup browsable and lets a partial restore target one tree without touching the others.

## Offsite Memory Snapshot

The LAN snapshot already includes the memory store, but it lives in the same building as the primary. The memory graph is the one thing worth a second, independent copy offsite. Because it is small (a single graph DB file or a compact directory), a daily push to object storage or a cloud drive costs almost nothing.

```bash
#!/usr/bin/env bash
# memory-offsite.sh — daily snapshot of the graph memory store to an offsite target
set -euo pipefail

MEM_DIR="${HOME}/.agent-memory"
STAMP=$(date '+%Y-%m-%d')
ARCHIVE="/tmp/agent-memory-${STAMP}.tar.gz"
LOG_FILE="${HOME}/.agent/logs/memory-offsite.log"
mkdir -p "$(dirname "$LOG_FILE")"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

log "memory-offsite START"

# Compact the store into a single dated archive
tar -czf "$ARCHIVE" -C "$(dirname "$MEM_DIR")" "$(basename "$MEM_DIR")"

# Push to the offsite target. Replace this command with your provider's CLI
# (object storage CLI, a cloud-drive uploader, or a custom MCP-backed tool).
# The point is: one small file, one daily upload, kept for N days of history.
if upload-to-offsite "$ARCHIVE" "agent-memory/${STAMP}.tar.gz"; then
    log "OK — uploaded agent-memory/${STAMP}.tar.gz ($(du -h "$ARCHIVE" | cut -f1))"
    rm -f "$ARCHIVE"
else
    log "FAILED — offsite upload of ${STAMP}.tar.gz"
    rm -f "$ARCHIVE"
    exit 1
fi

log "memory-offsite END"
```

Two design choices worth calling out:

- **Dated archives, not a single overwriting file.** Keeping `2026-03-14.tar.gz`, `2026-03-15.tar.gz`, ... gives you point-in-time recovery. If a corruption slips into the store and you don't notice for two days, you can still restore from before it happened. Prune to a rolling window (e.g. last 30 daily + last 12 monthly) so the offsite cost stays bounded.
- **Generalize the upload step.** Whether the offsite target is object storage, a cloud drive, or a custom integration is irrelevant to the strategy. What matters is that it is *off the LAN* and the upload *fails loudly* on error.

## Scheduling with launchd

Both jobs run on a schedule via launchd (systemd timers on Linux — same shape, see [OS Integration](04-os-integration.md)). Here is the nightly snapshot:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.your-agent.backup</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/you/your-agent/workers/backup/backup-to-host.sh</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>3</integer>
        <key>Minute</key><integer>30</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/you/.agent/logs/backup.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/you/.agent/logs/backup.err</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>/Users/you</string>
    </dict>
</dict>
</plist>
```

Load it with `launchctl load ~/Library/LaunchAgents/com.your-agent.backup.plist`.

Two scheduling gotchas, both covered more generally in [Lessons Learned](11-lessons-learned.md):

- **The wake-from-sleep timing gotcha.** launchd does **not** queue missed runs while the machine is asleep, and a `StartCalendarInterval` job scheduled for 3:30 AM will instead fire *whenever the machine next wakes* if it was asleep at 3:30 — possibly mid-morning while the operator is using it, dragging disk and network. Two mitigations: install GNU `rsync` so the run is cheap whenever it fires, and (better) add a **deduplication guard** so a delayed run that overlaps the next scheduled run does not double-execute.
- **Deduplication.** Same pattern as every other scheduled job — a date-stamped marker file. A backup that fires twice in a day is wasteful but harmless; one that fires four times because the machine woke, slept, and woke again is just noise in the logs. The cold-start hook can also trigger a backfill backup if it notices no successful run today, which means the dedup guard is load-bearing:

```bash
RAN_FILE="${HOME}/.agent/state/backup-last-run"
TODAY=$(date '+%Y-%m-%d')
if [[ -f "$RAN_FILE" && "$(cat "$RAN_FILE")" == "$TODAY" ]]; then
    log "Already backed up today — skipping"
    exit 0
fi
# ... run the backup ...
echo "$TODAY" > "$RAN_FILE"
```

## Verification and Failure Alerting

> A backup you have never restored is a hope, not a backup.

Both halves of that sentence cost people their data. The fix is cheap.

**Verify each run.** The `--stats` output already gives you file counts and byte totals per source. Sanity-check them: a backup that suddenly transferred *zero* files for the memory store, or whose total size dropped by 90% overnight, is telling you something broke (a moved directory, a too-aggressive exclude, a `--delete` that mirrored a deletion you did not intend). A lightweight delta check on the logged totals catches these:

```bash
# After the run, compare today's logged file count for the memory store
# against yesterday's. A large unexplained drop is a red flag, not success.
COUNT=$(grep -A2 'memory:' "$LOG_FILE" | grep 'Number of files' | tail -1 | grep -oE '[0-9,]+' | head -1)
log "memory store: $COUNT files this run"
```

**Periodically do a real restore.** Once a quarter, restore the memory store from the backup into a scratch location and confirm the agent can actually read it — load it, run a recall query, check the entry count matches. A dry-run restore (`rsync -n` from the backup host back to a temp dir) confirms the files are reachable; an actual load confirms they are *usable*. The difference between those two is where silent corruption hides.

**Alert on failure — loudly.** The script already logs `FAILED` on non-zero exit. Wire that to a push notification so a failed backup reaches the operator the same way any other critical alert does (see the push-notification pattern in [Lessons Learned](11-lessons-learned.md)):

```bash
# At the end of the backup script:
if [[ $OVERALL -ne 0 ]]; then
    curl -s -X POST https://your-push-endpoint/notify \
        -d "title=BACKUP FAILED" \
        -d "message=Agent backup exited $OVERALL — check backup.log" \
        -d "priority=high" >/dev/null 2>&1 || true
fi
```

Silent backup failure is the worst kind. The disk dies six weeks after the backup quietly stopped working, and you discover the gap only when you need the data. A failed backup must be as loud as a failed health check.

## Common Mistakes

**Backing up the reproducible bulk.** Checkpoints, virtualenvs, caches, and `node_modules` are large and rebuildable. Including them turns a five-minute backup into an hour-long one, and on a RAM-constrained machine with `openrsync` it can OOM-kill the job before it copies anything. Exclude the bulk; back up the irreplaceable.

**Letting secrets into the backup.** A mirror of an agent that handles API keys will copy those keys to the backup host and the cloud unless you exclude them. The backup exclude list is part of your secrets discipline, not separate from it.

**Trusting `--delete` to protect you from yourself.** A mirror defends against disk failure, not against a bad write or an accidental `rm`. If the primary's memory store gets corrupted, the next sync corrupts the backup. Keep dated/versioned offsite snapshots so you can roll back in time.

**One target instead of two.** A LAN-only backup dies with the building. A cloud-only backup is slow to restore a whole system from and may not hold everything. Use both: LAN for fast full recovery, offsite for the irreplaceable memory graph.

**Never testing a restore.** The most common failure mode is a backup that has been "working" for months but produces an unrestorable archive (wrong paths, a partial copy, an exclude that quietly dropped the actual data). Restore it on a schedule. Until you have read the data back successfully, you do not have a backup.

**Silent failure.** A backup job that fails without alerting is worse than no backup, because it gives false confidence. `set -euo pipefail`, a `FAILED` log line, and a push notification on non-zero exit are the minimum. The operator should never learn the backup was broken by needing it.

## Design Decisions and Trade-offs

| Decision | Choice | Trade-off |
|----------|--------|-----------|
| What to back up | Irreplaceable state only | Faster, smaller, must trust git/lockfiles for the rest |
| Mirror vs. accumulate | `--delete` mirror to LAN | Clean and exact; no protection against bad writes |
| LAN vs. offsite | Both, different scopes | Two jobs to maintain; covers both disk-death and site-loss |
| Offsite scope | Memory store only | Tiny and cheap; full recovery still needs git + LAN |
| Schedule | launchd nightly + dedup guard | Misses runs during sleep; the guard prevents double-runs |
| rsync flavor | GNU `rsync`, not `openrsync` | One install on each host; avoids the file-list RAM spike |
| Verification | Stats delta + quarterly restore | Manual discipline; the only thing that proves the backup works |

The throughline: a backup is not the rsync command, it is the *restore you have actually performed*. Optimize the strategy around the asset you cannot rebuild — the memory graph — and make every failure loud. Everything else the agent owns is already in git or already reproducible.
