#!/usr/bin/env python3
"""Minimal session startup hook for a persistent agent.

Gathers context from file state and injects it as additionalContext,
eliminating the need for the agent to make tool calls to orient itself
at session start.

Extend this with your own data sources as the system grows.

Key design decisions:
- Headless workers (no tmux, spawned by launchd) get a 2-line stamp instead
  of the full payload — they don't need orientation, and the full payload
  wastes tokens on every background invocation.
- task.lock, if present and fresh, is injected as a resume directive — the
  agent picks up in-progress work immediately without needing a handoff.
- Durable background tasks registered in a JSON registry are restored each
  session. Session-scoped entries are purged.
- Backfills (missed reflection, missed backup, git push) run as background
  Popen calls and do not block context injection.
"""

import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

AGENT_DIR = Path.home() / ".agent"
STATE_DIR = AGENT_DIR / "state"
HANDOFF_FILE = AGENT_DIR / "handoff.md"
PULSE_FILE = AGENT_DIR / "today-pulse.md"
VOICE_FILE = AGENT_DIR / "voice-response"
WAKE_FILE = AGENT_DIR / "wake-word"
TASK_LOCK_FILE = STATE_DIR / "task.lock"
BG_TASKS_FILE = STATE_DIR / "background-tasks.json"
AGENT_SRC = Path.home() / "your-agent"  # adjust to your source directory
TODAY = date.today().isoformat()

TASK_LOCK_STALENESS_HOURS = 24


def read_file(path: Path) -> str:
    try:
        return path.read_text().strip()
    except Exception:
        return ""


def is_interactive_session() -> bool:
    """True for the interactive main session; False for headless workers.

    Discriminator: the main session runs inside a tmux session, so $TMUX is
    set in its environment and inherited by this hook. Headless workers
    spawned by launchd (claude -p) run outside tmux. Fail-safe bias: anything
    tmux-resident gets the full payload (false negative = wasted tokens; false
    positive = lost continuity).
    """
    return bool(os.environ.get("TMUX"))


def read_voice_state() -> str:
    voice = read_file(VOICE_FILE) or "off"
    wake = read_file(WAKE_FILE) or "off"
    return f"Voice: {voice} | Wake word: {wake}"


def read_handoff() -> str:
    return read_file(HANDOFF_FILE)


def handoff_is_fresh(handoff: str) -> bool:
    """True if the handoff was written today.

    handoff.md is deliberately never deleted, so existence alone says nothing
    about warm vs cold start. Fresh = file mtime is today.
    """
    try:
        if date.fromtimestamp(HANDOFF_FILE.stat().st_mtime).isoformat() == TODAY:
            return True
    except OSError:
        pass
    return False


def read_pulse() -> str:
    if not PULSE_FILE.exists():
        return ""
    lines = PULSE_FILE.read_text().strip().splitlines()
    if not lines:
        return ""
    today_lines = [l for l in lines if l.startswith(TODAY)]
    if not today_lines:
        PULSE_FILE.write_text("")
        return ""
    return "\n".join(today_lines)


def read_task_lock() -> str:
    """Read task.lock if present and not stale (< 24hr).

    Returns a formatted resume directive, or empty string if no active lock.
    Stale locks are auto-deleted to prevent zombie commitments.
    """
    if not TASK_LOCK_FILE.exists():
        return ""
    try:
        content = TASK_LOCK_FILE.read_text().strip()
        if not content:
            return ""

        mtime = datetime.fromtimestamp(
            TASK_LOCK_FILE.stat().st_mtime, tz=timezone.utc
        )
        age_hours = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600
        if age_hours > TASK_LOCK_STALENESS_HOURS:
            TASK_LOCK_FILE.unlink()
            return ""

        # Cap injected body; file stays intact for full read
        raw = content.encode("utf-8")
        if len(raw) > 1500:
            content = (
                raw[:1500].decode("utf-8", errors="ignore").rstrip()
                + "\n… [lock truncated — full: ~/.agent/state/task.lock]"
            )
        return content
    except Exception:
        return ""


def read_boot_event() -> str:
    """Cold-boot recovery signal from a vitals worker.

    If a health/vitals worker wrote a boot_event.json on fresh boot,
    surface it once to the first session. Mark acknowledged immediately
    so subsequent sessions don't repeat it.
    """
    path = STATE_DIR / "vitals" / "boot_event.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text())
    except Exception:
        return ""
    if data.get("acknowledged"):
        return ""

    boot_epoch = data.get("boot_epoch")
    iso = data.get("iso", "unknown time")
    now = time.time()
    age_s = now - boot_epoch if boot_epoch else None

    # Acknowledge immediately
    try:
        data["acknowledged"] = True
        data["acknowledged_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)
    except Exception:
        pass

    # Stale guard: a boot >24h old that somehow wasn't acknowledged is dropped
    if age_s is not None and age_s > 86400:
        return ""

    age_str = ""
    if age_s is not None:
        h, m = divmod(int(age_s) // 60, 60)
        age_str = f" ({h}h {m}m ago)" if h else f" ({m}m ago)"

    return (
        f"Host came up from a real shutdown/reboot at {iso}{age_str}. "
        f"This is a cold-boot recovery — verify critical workers are running, "
        f"check task.lock STEP against current state, and re-check any "
        f"in-flight work that may have been interrupted."
    )


def process_background_tasks() -> str:
    """Read background-tasks.json, purge stale session-scoped entries,
    return a restore directive for durable crons/monitors."""
    if not BG_TASKS_FILE.exists():
        return ""
    try:
        data = json.loads(BG_TASKS_FILE.read_text())
    except Exception:
        return ""

    durable_crons = [c for c in data.get("crons", []) if c.get("durable")]
    durable_monitors = [m for m in data.get("monitors", []) if m.get("durable")]
    stale_count = (
        len(data.get("crons", [])) - len(durable_crons)
        + len(data.get("monitors", [])) - len(durable_monitors)
    )

    if stale_count > 0:
        data["crons"] = durable_crons
        data["monitors"] = durable_monitors
        data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        BG_TASKS_FILE.write_text(json.dumps(data, indent=2) + "\n")

    if not durable_crons and not durable_monitors:
        return ""

    items = []
    for c in durable_crons:
        items.append(
            f'cron: "{c.get("label", "unnamed")}" '
            f'schedule="{c.get("cron", "")}" '
            f'prompt={json.dumps(c.get("prompt", ""))}'
        )
    for m in durable_monitors:
        items.append(
            f'monitor: "{m.get("label", "unnamed")}" '
            f'command={json.dumps(m.get("command", ""))}'
        )

    return (
        "## Restore Background Tasks\n"
        "The following durable tasks from a previous session need re-creation:\n"
        + "\n".join(f"- {item}" for item in items)
        + "\n\nRe-create each via CronCreate/Monitor, then update "
        "background-tasks.json with the new IDs."
    )


def run_memory_recall() -> str:
    """Run a graph memory recall query for cold-start background context.

    Replace this with your own memory backend. The call here assumes a CLI
    tool that accepts a query string and returns JSON with a `results` list.
    """
    try:
        result = subprocess.run(
            ["your-memory-cli", "recall", "recent activity context", "--limit", "3"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            results = data.get("results", [])
            lines = []
            for r in results[:3]:
                text = (r.get("content") or "").strip()
                if text:
                    lines.append(f"- {text}")
            return "\n".join(lines)
    except Exception:
        pass
    return ""


def main():
    try:
        stdin_data = json.loads(sys.stdin.read())
    except Exception:
        stdin_data = {}

    # S-1 optimization: headless workers (no $TMUX) get a minimal stamp.
    # They don't need the full interactive payload, and the full payload
    # wastes tokens on every background invocation.
    if not is_interactive_session():
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": (
                        "Headless session (no $TMUX) — full startup context skipped.\n"
                        "State if needed: ~/.agent/handoff.md · ~/.agent/state/task.lock"
                    ),
                }
            },
            sys.stdout,
        )
        return

    # Interactive-only: reset single-slot state files so threshold hooks
    # don't report stale values from the previous session.
    context_bridge = STATE_DIR / "context.json"
    try:
        context_bridge.unlink()
    except Exception:
        pass

    # Gather context
    voice = read_voice_state()
    handoff = read_handoff()
    pulse = read_pulse()
    task_lock = read_task_lock()
    boot_event = read_boot_event()
    bg_restore = process_background_tasks()

    # Cold start = no handoff written today and no pulse entries today.
    # (The first session of a new day, not a same-day restart.)
    is_cold_start = not (handoff and handoff_is_fresh(handoff)) and not pulse

    parts = []

    # Boot recovery sits first — it reframes how to interpret everything else
    if boot_event:
        parts.append(f"## Cold Boot Recovery\n{boot_event}")

    # Task lock is a resume directive — inject early
    if task_lock:
        parts.append(
            "## RESUME TASK (task.lock is active)\n"
            "A task was in progress when the last session ended. "
            "Read the fields below and start working on STEP immediately. "
            "The lock IS the instruction — do not present it and ask what to do.\n\n"
            f"```\n{task_lock}\n```"
        )

    # Durable background task restoration
    if bg_restore:
        parts.append(bg_restore)

    # System state — always included
    parts.append(f"## System State\n{voice}")

    # Handoff from previous session
    if handoff:
        parts.append(f"## Handoff\n{handoff}")
        # Do NOT delete the handoff — it persists as a durable artifact.
        # The next /restart overwrites it. Staleness is visible via the
        # dated header each handoff carries.

    # Today's pulse (same-day session continuity)
    if pulse:
        parts.append(f"## Today's Pulse\n{pulse}")

    # Task index — active tasks and any reconciliation discrepancies
    # Add your task-index script call here:
    # task_index = get_task_index()
    # if task_index:
    #     parts.append(f"## Tasks\n{task_index}")

    # Cold start: graph memory recall for background context
    if is_cold_start:
        recall = run_memory_recall()
        if recall:
            parts.append(f"## Background\n{recall}")

    # Cold start backfills — run missed maintenance as background processes
    if is_cold_start:
        # Reflection: if yesterday's reflection is missing, run it
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        yesterday_reflection = AGENT_DIR / "reflections" / f"{yesterday}.md"
        if not yesterday_reflection.exists():
            reflect_script = AGENT_SRC / "workers" / "memory" / "reflect.sh"
            if reflect_script.exists():
                subprocess.Popen(
                    ["bash", str(reflect_script), yesterday],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

        # Memory backup: run if last backup is stale (not from today)
        backup_dir = AGENT_DIR / "backups" / "memory"
        today_backup = backup_dir / f"memory-{TODAY}.db"
        if not today_backup.exists():
            backup_script = AGENT_SRC / "workers" / "memory-backup.py"
            if backup_script.exists():
                subprocess.Popen(
                    ["python3", str(backup_script)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

        # Git: push any unpushed commits
        subprocess.Popen(
            ["bash", "-c", "cd ~/your-agent && git diff origin/main --quiet || git push"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    if not parts:
        parts.append("Cold start — no prior context available.")

    context = "\n\n".join(parts)

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
