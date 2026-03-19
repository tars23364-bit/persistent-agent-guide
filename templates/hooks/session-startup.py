#!/usr/bin/env python3
"""Minimal session startup hook for a persistent agent.

Gathers context from file state and injects it as additionalContext.
Extend this with your own data sources as the system grows.
"""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

AGENT_DIR = Path.home() / ".agent"
HANDOFF_FILE = AGENT_DIR / "handoff.md"
TODAY = date.today().isoformat()


def read_file(path: Path) -> str:
    try:
        return path.read_text().strip()
    except Exception:
        return ""


def read_handoff() -> str:
    content = read_file(HANDOFF_FILE)
    if not content:
        return ""
    return content


def main():
    try:
        stdin_data = json.loads(sys.stdin.read())
    except Exception:
        stdin_data = {}

    parts = []

    # Handoff from previous session
    handoff = read_handoff()
    if handoff:
        parts.append(f"## Handoff\n{handoff}")
        try:
            HANDOFF_FILE.unlink()
        except Exception:
            pass

    # Add your own context sources here:
    # - Task index
    # - Operator awareness / attunement
    # - Voice/toggle state
    # - Brief availability
    # - Graph memory recall

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
