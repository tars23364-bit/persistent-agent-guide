# Voice Pipeline

A persistent agent that only communicates through text in a terminal is useful
but limited. Adding voice input and output transforms the interaction model --
you can talk to your agent while your hands are busy, and it can speak alerts
without you watching a screen.

This chapter covers the full voice pipeline: text-to-speech output, speech-to-text
input, wake word detection, push-to-talk, and the critical problem of echo
suppression.

## Architecture Overview

The voice system has two independent halves:

```
Voice Input                          Voice Output
─────────────                        ─────────────
Wake word (Porcupine) ─┐             Agent response
Push-to-talk (PTT) ────┤                  │
                        ▼                  ▼
                   Speech-to-text     Stop hook (POST)
                   (Whisper/API)           │
                        │                  ▼
                        ▼             TTS daemon (:7700)
                   Text injected      (local model)
                   into tmux               │
                        │                  ▼
                        ▼             Audio playback
                   Agent processes         │
                   as normal text          ▼
                                      Echo suppression flag
```

Input and output are controlled by separate toggles. This separation matters:
you might want voice output without wake word listening, or push-to-talk input
without the agent speaking back.

One design clarification worth calling out early: **PTT is purely an input
method.** It just delivers transcribed text into the agent session. Whether the
agent speaks back is controlled entirely by the Voice Response toggle, not by
how the input arrived.

## The TTS Daemon Pattern

The core design decision: TTS runs as a **separate HTTP daemon**, not inside the
agent process. This matters for several reasons:

1. **Zero token cost, zero API calls.** The agent never calls a "speak" tool for
   normal responses. A Stop hook POSTs the response text to the daemon after each
   turn. The daemon handles synthesis entirely locally -- no cloud API involved.
2. **Decoupled lifecycle.** The daemon survives session restarts. Voice doesn't
   break when the agent reboots.
3. **Toggle independence.** The daemon checks the voice toggle file before
   speaking. If voice is off, it returns immediately. The agent doesn't need to
   know or care about the current toggle state.
4. **Selective override.** The agent can still call a `speak` MCP tool directly
   for forced alerts (e.g., urgent escalations when voice is off), but this is
   the exception. Normal conversation goes through the hook exclusively.

### Daemon Implementation

The daemon is a lightweight HTTP server running on localhost. The essential
structural skeleton -- endpoints, toggle checks, speaking flag, quiet hours,
and follow-up listen signal -- looks like this:

```python
# workers/tts-daemon/server.py
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import json
import subprocess
import threading
import time

HOST = "127.0.0.1"
PORT = 7700

TOGGLE_FILE    = Path.home() / ".agent" / "voice-response"
SPEAKING_FLAG  = Path.home() / ".agent" / "voice-speaking"
FOLLOWUP_FLAG  = Path.home() / ".agent" / "voice-followup"
QUIET_OVERRIDE = Path.home() / ".agent" / "voice-quiet-override"

# Quiet hours: no unforced speech between 22:00 and 07:00 local time.
# A toggle left ON overnight must not speak autonomous output.
QUIET_START, QUIET_END = 22, 7
QUIET_OVERRIDE_TTL = 10 * 3600  # stale flags auto-deleted

_playback_lock = threading.Lock()
_playback_proc = None  # current afplay/mpv subprocess

class TTSHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/speak":
            self._handle_speak()
        elif self.path == "/stop":
            self._handle_stop()
        else:
            self.send_response(404); self.end_headers()

    def _handle_speak(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length))
        text   = body.get("last_assistant_message", body.get("text", ""))
        force  = body.get("force", False)

        # Check voice toggle
        if not force:
            toggle = TOGGLE_FILE.read_text().strip() if TOGGLE_FILE.exists() else "off"
            if toggle != "on":
                self._ok({"status": "skipped", "reason": "voice off"})
                return

        # Check quiet hours (unless forced or override flag present + fresh)
        if not force and self._in_quiet_hours():
            self._ok({"status": "skipped", "reason": "quiet hours"})
            return

        # Set echo-suppression flag, synthesize, play
        Path(SPEAKING_FLAG).touch()
        try:
            audio = synthesize_speech(text)   # local TTS model (see below)
            play_audio(audio)
            # Follow-up listen: if response is a question, open mic for 5s
            if text.strip().endswith("?") and TOGGLE_FILE.exists():
                Path(FOLLOWUP_FLAG).write_text("5")
        finally:
            Path(SPEAKING_FLAG).unlink(missing_ok=True)

        self._ok({"status": "ok"})

    def _handle_stop(self):
        with _playback_lock:
            global _playback_proc
            if _playback_proc:
                _playback_proc.terminate()
        Path(SPEAKING_FLAG).unlink(missing_ok=True)
        self._ok({"status": "stopped"})

    def _in_quiet_hours(self):
        hour = time.localtime().tm_hour
        in_window = hour >= QUIET_START or hour < QUIET_END
        if not in_window:
            return False
        # Honor override flag if fresh (within TTL)
        if Path(QUIET_OVERRIDE).exists():
            age = time.time() - Path(QUIET_OVERRIDE).stat().st_mtime
            if age < QUIET_OVERRIDE_TTL:
                return False
            Path(QUIET_OVERRIDE).unlink(missing_ok=True)
        return True

    def _ok(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(data)

    def log_message(self, *_): pass  # silence HTTP logs

def synthesize_speech(text):
    """Synthesize via local model (Kokoro) or fall back to cloud provider."""
    # Kokoro (mlx-community/Kokoro-82M-bf16) example:
    #   from kokoro_mlx import generate
    #   samples, sr = generate(text, voice="bf_emma", lang_code="b", speed=0.9)
    #   return samples_to_wav_bytes(samples, sr)
    # Cloud fallback: ElevenLabs, OpenAI TTS, etc. -- same interface.
    raise NotImplementedError("wire in your TTS backend here")

def play_audio(audio_data):
    """Play audio through system speakers (cancel any current playback first)."""
    global _playback_proc
    import tempfile, sys
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_data); fname = f.name
    with _playback_lock:
        if _playback_proc:
            _playback_proc.terminate()
        player = ["afplay", fname] if sys.platform == "darwin" else ["mpv", "--no-video", "--really-quiet", fname]
        _playback_proc = subprocess.Popen(player)
    _playback_proc.wait()

if __name__ == "__main__":
    server = HTTPServer((HOST, PORT), TTSHandler)
    print(f"TTS daemon running on {HOST}:{PORT}")
    server.serve_forever()
```

**Key design points from this implementation:**

- The Stop hook POSTs the `last_assistant_message` field (the Claude Code Stop
  event payload shape) -- the daemon extracts text from that field.
- Quiet hours are enforced inside the daemon, not the hook. A deliberate
  overnight-speech override (`touch ~/.agent/voice-quiet-override`) is honored
  for up to 10 hours; stale flags are deleted automatically.
- Playback is cancellable: a new `/speak` request kills current playback before
  starting the next one ("drop semantics").
- The follow-up listen flag is written as a file containing the duration in
  seconds; the wake-word listener polls for it separately.

### Running the Daemon

Use a LaunchAgent so the daemon starts at login and survives terminal restarts:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.your-agent.tts-daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/your-agent/workers/tts-daemon/server.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/tts-daemon.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/tts-daemon.log</string>
</dict>
</plist>
```

## The Stop Hook: Automatic Speech

The key integration point is a Claude Code **Stop hook** that fires after every
agent response. This hook forwards the Stop event payload (which contains the
last assistant message) to the TTS daemon. The daemon is responsible for all
toggle and quiet-hours logic -- the hook itself is thin:

```bash
#!/bin/bash
# hooks/stop-tts.sh
# Fires after every assistant response via Claude Code Stop hook.
# The Stop hook receives the full Stop event as JSON on stdin.

# Don't speak responses from non-voice channels (messaging, heartbeat, etc.)
[ -f "$HOME/.agent/tts-suppress" ] && exit 0

# Read the Stop event payload from stdin once, then fire-and-forget to daemon.
# The daemon extracts last_assistant_message and handles toggle/quiet-hours.
BODY=$(cat)

curl -s -X POST http://localhost:7700/speak \
  -H "Content-Type: application/json" \
  -d "$BODY" \
  -o /dev/null \
  --connect-timeout 1 \
  --max-time 2 \
  2>/dev/null &

# Don't block the agent waiting for speech to finish.
exit 0
```

Register it in your `.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "bash /path/to/your-agent/hooks/stop-tts.sh"
      }
    ]
  }
}
```

The important detail: the hook runs in the background (`&`) and uses aggressive
curl timeouts. If the TTS daemon is down the hook exits quickly and the agent
is unaffected. Voice failure is never a blocking failure.

## Voice Toggle System

Two independent toggles control voice behavior, stored as simple text files:

| Toggle | File | Controls |
|--------|------|----------|
| Voice Response | `~/.agent/voice-response` | TTS output (speak responses) |
| Wake Word | `~/.agent/wake-word` | Wake word listening (Porcupine) |

Each file contains the text `on` or `off`. This is more explicit than a
file-exists convention and readable by any language without ambiguity.

```bash
# Toggle voice response
toggle_voice() {
    local flag="$HOME/.agent/voice-response"
    local current
    current=$(cat "$flag" 2>/dev/null || echo "off")
    if [[ "$current" == "on" ]]; then
        echo -n "off" > "$flag"
        echo "Voice response: OFF"
    else
        echo -n "on" > "$flag"
        echo "Voice response: ON"
    fi
}

# Toggle wake word
toggle_wake() {
    local flag="$HOME/.agent/wake-word"
    local current
    current=$(cat "$flag" 2>/dev/null || echo "off")
    if [[ "$current" == "on" ]]; then
        echo -n "off" > "$flag"
        echo "Wake word: OFF"
    else
        echo -n "on" > "$flag"
        echo "Wake word: ON"
    fi
}
```

### Boot Defaults

Both toggles reset to OFF on system reboot. Your tmux startup script handles
this:

```bash
# In your auto-start script
echo -n "off" > "$HOME/.agent/voice-response"
echo -n "off" > "$HOME/.agent/wake-word"
```

The operator enables voice manually when they sit down at the desk. This
prevents the agent from speaking into an empty room after a reboot.

### Quiet Hours

Even when the Voice Response toggle is ON, the daemon enforces a quiet-hours
window (e.g., 22:00–07:00 local) where unforced speech is refused. This matters
because the toggle is session-state that an operator might leave enabled
overnight -- a background alert at 2am would otherwise speak aloud.

The quiet-hours gate lives inside the daemon, not the hook. Normal responses
are silently skipped. Deliberate escalations (urgent alerts) can bypass it with
a `force: true` field in the POST body.

A deliberate override flag (`touch ~/.agent/voice-quiet-override`) lets the
operator allow overnight speech for a bounded window (e.g., 10 hours). The
daemon checks the flag's mtime; stale flags are auto-deleted rather than honored
indefinitely.

## Voice Input: Wake Word + Push-to-Talk

Voice input has two modes that work independently:

### Wake Word Detection

Uses [Porcupine](https://picovoice.ai/platform/porcupine/) for local, offline
wake word detection. When the wake word is heard, the system:

1. Plays a short acknowledgment tone
2. Starts recording audio
3. Sends audio to a speech-to-text service (Whisper API, local Whisper, etc.)
4. Injects the transcribed text into the tmux session as if the operator typed it

```python
# workers/wake-word/listener.py (simplified)
import pvporcupine
import pyaudio
import struct
from pathlib import Path

TOGGLE_FILE = Path.home() / ".agent" / "wake-word"
SPEAKING_FLAG = Path.home() / ".agent" / "voice-speaking"

def run_listener():
    porcupine = pvporcupine.create(
        access_key="YOUR_PICOVOICE_KEY",
        keyword_paths=["path/to/your-agent_wake_word.ppn"]
    )
    pa = pyaudio.PyAudio()
    stream = pa.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length
    )

    while True:
        # Check toggle
        toggle = TOGGLE_FILE.read_text().strip() if TOGGLE_FILE.exists() else "off"
        if toggle != "on":
            time.sleep(0.5)
            continue

        # Echo suppression: pause during TTS playback
        if SPEAKING_FLAG.exists():
            time.sleep(0.1)
            continue

        pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
        pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)

        if porcupine.process(pcm) >= 0:
            # Wake word detected
            play_ack_tone()
            text = record_and_transcribe()
            if text:
                inject_into_tmux(text)
```

### Push-to-Talk

A simpler input mode that uses a hotkey (e.g., Caps Lock) to start recording.
No wake word detection needed -- just hold the key, speak, release.

PTT is purely an input mechanism. Whether the agent *speaks back* is still
controlled by the Voice Response toggle. You can use PTT with voice output off
for a "dictation" mode.

```python
# workers/ptt/listener.py (simplified concept)
from pynput import keyboard
from pathlib import Path

SPEAKING_FLAG = Path.home() / ".agent" / "voice-speaking"

def on_press(key):
    if key == keyboard.Key.caps_lock:
        start_recording()

def on_release(key):
    if key == keyboard.Key.caps_lock:
        audio = stop_recording()
        text = transcribe(audio)
        if text:
            inject_into_tmux(text)

with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()
```

### Injecting Text into tmux

Both input modes ultimately inject text into the agent's tmux session:

```python
import subprocess

def inject_into_tmux(text, session="agent"):
    """Send transcribed text to the agent's tmux session."""
    # Send the text as keystrokes
    subprocess.run([
        "tmux", "send-keys", "-t", session, text, "Enter"
    ])
```

This is the simplest reliable approach. The agent sees the text exactly as if
the operator typed it -- no special handling needed on the agent side.

## Echo Suppression

Echo suppression solves a critical problem: without it, the wake word listener
hears the agent's own TTS output and tries to transcribe it, creating a
feedback loop.

The solution is a flag file that the TTS daemon manages:

```
1. Agent responds with text
2. Stop hook POSTs text to TTS daemon
3. TTS daemon touches ~/.agent/voice-speaking
4. TTS daemon plays audio
5. TTS daemon removes ~/.agent/voice-speaking
6. Wake word listener resumes
```

The wake word listener checks for this flag on every audio frame:

```python
# In the wake word listener loop
if SPEAKING_FLAG.exists():
    time.sleep(0.1)
    continue  # Skip processing while agent is speaking
```

This is a coarse but reliable mechanism. The flag file approach has ~100ms
latency on check, which is fine -- you don't need millisecond precision for
echo suppression in a voice assistant context.

### Edge Cases

**TTS daemon crashes mid-playback.** The speaking flag stays set, blocking the
wake word listener indefinitely. Solution: add a staleness check. If the flag
file is older than 60 seconds, delete it and resume:

```python
import time

def is_speaking():
    if not SPEAKING_FLAG.exists():
        return False
    age = time.time() - SPEAKING_FLAG.stat().st_mtime
    if age > 60:
        SPEAKING_FLAG.unlink(missing_ok=True)
        return False
    return True
```

**Multiple audio outputs.** If the system plays other audio (notifications,
music), the wake word listener might pick those up. Porcupine is reasonably
good at rejecting non-wake-word audio, but in noisy environments you may want
to increase the sensitivity threshold.

## Follow-Up Listening

A natural conversation pattern: the agent asks a question, and the operator
answers without re-triggering the wake word. The TTS daemon handles this:

```python
# In the TTS daemon, after playing audio
voice_on = TOGGLE_FILE.read_text().strip() == "on" if TOGGLE_FILE.exists() else False
if text.strip().endswith("?") and voice_on:
    # Signal the wake word listener to open the mic for N seconds
    follow_up_file = Path.home() / ".agent" / "follow-up-listen"
    follow_up_file.write_text("5")  # seconds
```

The wake word listener checks for this signal:

```python
follow_up = Path.home() / ".agent" / "follow-up-listen"
if follow_up.exists():
    seconds = int(follow_up.read_text().strip())
    follow_up.unlink()
    # Record for N seconds without requiring wake word
    audio = record_for_duration(seconds)
    text = transcribe(audio)
    if text:
        inject_into_tmux(text)
```

This creates a natural conversational flow without requiring the operator to
say the wake word after every agent question.

## Selective Speech

Not every agent response should be spoken. Long code blocks, file listings,
and technical output are better left as text. The TTS daemon or the Stop hook
can filter based on content:

```bash
# In stop-tts.sh — skip responses that are mostly code
CODE_LINES=$(echo "$MESSAGE" | grep -c '^\s*[{}\[\]|#/]')
TOTAL_LINES=$(echo "$MESSAGE" | wc -l)

if (( TOTAL_LINES > 0 )); then
    CODE_RATIO=$(( CODE_LINES * 100 / TOTAL_LINES ))
    if (( CODE_RATIO > 60 )); then
        exit 0  # Skip — mostly code
    fi
fi
```

Or implement it on the daemon side with smarter heuristics:

- Skip responses longer than N characters (they're probably not conversational)
- Strip markdown formatting before speaking
- Only speak the first paragraph of long responses
- Never speak raw JSON, code blocks, or file paths

## TTS Suppression for Non-Voice Channels

When the agent processes messages from other channels (inbound messaging,
automated monitoring, heartbeat alerts), you don't want it speaking those
responses aloud. Use a suppression flag:

```bash
# Before processing queued messages -- set FIRST, before any agent turn
touch "$HOME/.agent/tts-suppress"

# Process all messages...

# Remove after all messages are processed
rm -f "$HOME/.agent/tts-suppress"
```

The Stop hook checks this flag before sending to the TTS daemon:

```bash
[ -f "$HOME/.agent/tts-suppress" ] && exit 0
```

This is simpler and more reliable than trying to toggle the voice response
setting itself -- the suppression flag is temporary and scoped to the current
operation. It also applies to heartbeat check results, which should only produce
voice output if they are escalating a genuine alert to the operator.

## Floating UI for Toggle Control

A small floating UI element gives the operator visual feedback and quick toggle
access without switching to the terminal:

```
┌─────────────────┐
│  Agent Status    │   ← Status display (working / idle / stale)
│  [W] [V]         │   ← Wake word and Voice toggles
└─────────────────┘
```

This is well-suited to a small always-on-top SwiftUI app on macOS (a "pill").
The two toggles -- [W] for wake word, [V] for voice response -- write directly
to the same flag files the daemon reads, so the UI and daemon are always in sync
without any IPC or state synchronization.

The pill itself is display-only for most state; only the two voice toggles are
interactive. This keeps the implementation simple: the UI just reads and writes
the same two files covered in the toggle section above.

What matters operationally:

1. See whether voice is active at a glance without opening the terminal
2. Toggle it without context-switching
3. See when the agent is actively working vs idle

The flag-file approach makes this trivially implementable in any language -- no
shared memory, no sockets, just file reads and writes.

## TTS Backend Options

The architecture is provider-agnostic. The TTS daemon wraps the synthesis call,
so swapping providers means changing one function. The recommended path is a
local model first, with a cloud provider as fallback:

| Provider | Latency | Quality | Cost | Offline |
|----------|---------|---------|------|---------|
| **Kokoro (mlx)** | ~40ms | Good | Free | Yes |
| Piper | ~200ms | Good | Free | Yes |
| Coqui/XTTS | ~500ms | Good | Free | Yes |
| macOS `say` | Instant | Basic | Free | Yes |
| OpenAI TTS | ~400ms | Good | ~$0.015/1K chars | No |
| ElevenLabs | ~300ms | Excellent | ~$0.30/1K chars | No |

**Kokoro** ([mlx-community/Kokoro-82M-bf16](https://huggingface.co/mlx-community/Kokoro-82M-bf16))
is the standout local option on Apple Silicon. An 82M-parameter model running
via MLX achieves roughly 24x realtime on an M-series chip -- latency is barely
perceptible, it runs fully offline, and there are no per-character API costs.
The `bf_emma` voice is natural-sounding British English. Install via `pip install
kokoro-mlx` in a dedicated venv.

A configurable backend file (e.g., `~/.agent/tts-backend` containing `"kokoro"`
or `"elevenlabs"`) lets the daemon switch at runtime without a restart.

For STT (speech-to-text):

| Provider | Latency | Quality | Cost | Offline |
|----------|---------|---------|------|---------|
| Whisper API | ~1-2s | Excellent | $0.006/min | No |
| Local Whisper | ~2-5s | Excellent | Free | Yes |
| Deepgram | ~300ms | Excellent | ~$0.01/min | No |
| macOS Dictation | ~500ms | Good | Free | Yes |

A fallback chain is useful: try the local provider first, fall back to cloud
if the local model is unavailable or too slow for the hardware.

## Putting It All Together

The full voice pipeline involves several independent components:

```
LaunchAgent
├── TTS daemon (localhost:7700)        ← Always running
├── Wake word listener (Porcupine)     ← Checks wake-word toggle
└── PTT listener (hotkey)              ← Always running

Claude Code Hooks
└── Stop hook → POST to TTS daemon     ← After every response

Flag Files (~/.agent/)
├── voice-response                     ← TTS output toggle (contains "on"/"off")
├── wake-word                          ← Wake word input toggle (contains "on"/"off")
├── voice-speaking                     ← Echo suppression (presence flag, managed by daemon)
├── tts-suppress                       ← Temporary suppression (presence flag, managed by agent)
├── voice-quiet-override               ← Permit overnight speech for ~10h (mtime-gated)
└── voice-followup                     ← Follow-up mic open, contains duration in seconds
```

Each component is simple on its own. The complexity comes from their
interactions, which is why the flag-file coordination pattern works well -- every
component can check state independently without IPC or shared memory.

## Common Pitfalls

**Feedback loops.** Without echo suppression, the agent hears itself and
responds to its own speech. This is the single most important problem to solve
before enabling voice.

**Blocking the agent.** If the Stop hook waits for TTS to complete before
returning, the agent can't accept input during speech. Always run TTS
asynchronously.

**Stale flags.** A crash can leave flag files in inconsistent states. Add
staleness checks and clear all flags on boot.

**Wake word false positives.** In noisy environments, the wake word detector
may trigger on ambient speech. Tune sensitivity and consider adding a
confirmation tone + short silence check before recording.

**Long responses.** Speaking a 500-word response takes 2+ minutes. Consider
truncating or summarizing for voice output, or let the operator interrupt with
a stop command.

**Network dependency.** Cloud TTS/STT means voice breaks when the network is
down. A local fallback (even if lower quality) keeps the system functional.
