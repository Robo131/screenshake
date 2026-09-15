"""
BassShake - a bass/kick-reactive screen overlay for Windows.

What it does:
  - Listens to your PC's system audio output (WASAPI loopback - everything
    playing through your speakers/headphones).
  - Runs an FFT to track bass energy and detect kick/transient hits.
  - Drives a genuinely transparent, click-through, always-on-top fullscreen
    overlay that glows/swells across the whole screen on bass and jolts on
    kicks.
  - Every ~300ms it checks which application is currently the loudest audio
    source on your system (via the Windows volume mixer API). Priority apps
    (browsers, Voicemod, Spotify) always keep the effect on; only when none
    of those are active does it check whether an excluded game
    (Roblox/Rust/Minecraft) is dominant and suppress the effect if so.
  - The effect strength also scales with actual playback volume, so quiet
    audio barely moves the screen and loud audio hits hard.

Why Qt (PySide6) instead of Tkinter:
  Tkinter's "-transparentcolor" trick only makes pixels that are EXACTLY
  the key color invisible - it can't do a soft alpha fade. A gradient glow
  built that way renders as a solid near-black block (opaque, not
  click-through) everywhere it isn't perfectly pure black. Qt's
  WA_TranslucentBackground gives real per-pixel alpha compositing (backed
  by the same Windows layered-window API real overlay apps use), so a soft
  gradient actually fades to nothing and lets clicks/games through.

Important honest limitation:
  Windows does not let a background app cleanly separate "this stream is
  music" from "this stream is game audio" at the waveform level. The
  loopback capture always contains the full system mix. The exclusion
  logic works by checking which app is *dominant* in the Windows volume
  mixer at that moment, not by filtering the audio itself.

Run this on Windows only. See README.md for setup + build-to-exe steps.
"""

import sys
import time
import threading
import random
import math

import numpy as np

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    print("Missing dependency: pyaudiowpatch. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
except ImportError:
    print("Missing dependency: pycaw. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
    from PySide6.QtGui import QPainter, QRadialGradient, QColor, QGuiApplication
    from PySide6.QtWidgets import QApplication, QWidget
except ImportError:
    print("Missing dependency: PySide6. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    import keyboard  # optional, for the global quit hotkey
    HAVE_KEYBOARD = True
except ImportError:
    HAVE_KEYBOARD = False


# =========================== CONFIG ===========================

# Executables to ignore. Add/remove as needed (case-insensitive, no path).
EXCLUDED_PROCESSES = {
    "robloxplayerbeta.exe",
    "roblox.exe",
    "rustclient.exe",
    "rust.exe",
    "javaw.exe",            # Minecraft Java Edition launcher process
    "minecraft.exe",
    "minecraft.windows.exe",  # Minecraft Bedrock
}

# Trusted/priority sources. If any of these are making meaningful noise,
# the effect stays ON regardless of what else is playing (even if an
# excluded game happens to be technically louder at that instant).
PRIORITY_PROCESSES = {
    # Browsers
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "opera_gx.exe",
    "vivaldi.exe",
    # Voicemod
    "voicemod.exe",
    "vmwinsvc.exe",
    # Spotify
    "spotify.exe",
}

# Peak level (0.0-1.0) a priority app needs to hit before it's considered
# "active" and overrides suppression from an excluded app.
PRIORITY_ACTIVE_THRESHOLD = 0.02

# How strongly an excluded app's audio needs to dominate before we
# suppress the effect (0.0 - 1.0). Lower = more aggressive suppression.
# Only applies when no priority app is currently active (see above).
SUPPRESSION_THRESHOLD = 0.35

# Bass frequency band used for the "swell"/glow effect, in Hz.
BASS_LOW_HZ = 25
BASS_HIGH_HZ = 150

# Overall sensitivity multiplier for the swell effect.
BASS_SENSITIVITY = 6.0

# --- Volume sensitivity ---
# Scales the whole effect (swell + shake) by how loud the actual audio
# currently is, so quiet background music barely moves the screen while
# loud music hits hard. 1.0 = neutral, higher = more dramatic response to
# volume changes, lower = flatter response.
VOLUME_SENSITIVITY = 2.5
VOLUME_CURVE = 1.4          # >1 makes quiet audio contribute even less
VOLUME_NOISE_FLOOR = 0.015  # below this normalized volume, treat as silent

# Kick/transient detection: a bass spike this many times above the recent
# rolling average triggers a screen "jolt".
KICK_SPIKE_RATIO = 1.8
KICK_MIN_INTERVAL_SEC = 0.12   # don't re-trigger faster than this
KICK_DECAY_SEC = 0.16          # how fast a kick jolt settles back to zero

# --- Shake ---
# BASS_SHAKE_PIXELS: small constant jitter while bass is present, so a
# rolling bassline feels alive, not just individual hits.
# KICK_SHAKE_PIXELS: a bigger jolt layered on top for individual kicks.
BASS_SHAKE_PIXELS = 6
KICK_SHAKE_PIXELS = 32

# --- Full-screen glow/swell look ---
GLOW_COLOR = "#7fdfff"     # tint color; change to taste, e.g. "#ff3366"

# Radial vignette: color washes in from the screen edges toward the
# center as bass builds. Higher VIGNETTE_POWER keeps the center clearer
# for longer; lower values let color fill the whole screen sooner.
VIGNETTE_POWER = 1.6
VIGNETTE_MAX_ALPHA = 175   # 0-255, how opaque the glow gets at max bass

# Full-screen flash: a brief, uniform color wash across the ENTIRE screen
# on kick hits, layered on top of the vignette - this is what sells the
# "whole monitor swelling" feeling rather than just edges.
FLASH_MAX_ALPHA = 130

# Audio capture
CHUNK = 1024

# Frames per second for the overlay redraw loop
FPS = 60

# ================================================================

KICK_DECAY_FACTOR = math.exp(-(1.0 / FPS) / KICK_DECAY_SEC)


class SharedState:
    """Thread-safe-enough shared values between audio thread and UI thread.
    Reads/writes of floats are effectively atomic in CPython, so no lock
    needed for this simple case."""
    def __init__(self):
        self.bass_level = 0.0       # smoothed 0..1 swell amount (volume-scaled)
        self.kick_pulse = 0.0       # 0..1, decays after a kick, drives jolt
        self.volume_level = 0.0     # smoothed 0..1 overall loudness
        self.suppress = 1.0         # 1.0 = full effect, 0.0 = fully suppressed
        self.running = True


state = SharedState()


# ----------------------- Audio analysis thread -----------------------

def get_loopback_device(p):
    """Find the WASAPI loopback device matching the current default output."""
    wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    default_speakers = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])

    if not default_speakers.get("isLoopbackDevice", False):
        for loopback in p.get_loopback_device_info_generator():
            if default_speakers["name"] in loopback["name"]:
                default_speakers = loopback
                break
        else:
            raise RuntimeError(
                "Could not find a loopback device matching your default output. "
                "Make sure you're on Windows 10/11 with WASAPI available."
            )
    return default_speakers


def audio_thread_func():
    p = pyaudio.PyAudio()
    device = get_loopback_device(p)
    rate = int(device["defaultSampleRate"])
    channels = max(1, int(device["maxInputChannels"]))

    stream = p.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=rate,
        frames_per_buffer=CHUNK,
        input=True,
        input_device_index=device["index"],
    )

    freqs = np.fft.rfftfreq(CHUNK, d=1.0 / rate)
    bass_mask = (freqs >= BASS_LOW_HZ) & (freqs <= BASS_HIGH_HZ)

    rolling_avg = 1e-6
    last_kick_time = 0.0
    smoothed_bass = 0.0
    smoothed_volume = 0.0

    while state.running:
        try:
            raw = stream.read(CHUNK, exception_on_overflow=False)
        except Exception:
            time.sleep(0.05)
            continue

        samples = np.frombuffer(raw, dtype=np.int16)
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)

        if len(samples) < CHUNK:
            continue

        # --- overall loudness (volume sensitivity) ---
        rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
        volume_norm = min(1.0, rms / 32768.0)
        smoothed_volume = smoothed_volume * 0.85 + volume_norm * 0.15
        state.volume_level = smoothed_volume

        if smoothed_volume < VOLUME_NOISE_FLOOR:
            volume_factor = 0.0
        else:
            volume_factor = min(1.0, (smoothed_volume * VOLUME_SENSITIVITY) ** VOLUME_CURVE)

        windowed = samples * np.hanning(len(samples))
        spectrum = np.abs(np.fft.rfft(windowed))

        bass_energy = float(np.mean(spectrum[bass_mask])) if bass_mask.any() else 0.0

        norm = bass_energy / (32768.0 * CHUNK / 4.0)
        norm = min(1.0, norm * BASS_SENSITIVITY)

        smoothed_bass = smoothed_bass * 0.75 + norm * 0.25
        state.bass_level = smoothed_bass * volume_factor

        rolling_avg = rolling_avg * 0.98 + bass_energy * 0.02
        now = time.time()
        if (
            rolling_avg > 1e-6
            and bass_energy > rolling_avg * KICK_SPIKE_RATIO
            and (now - last_kick_time) > KICK_MIN_INTERVAL_SEC
            and volume_factor > 0.05
        ):
            state.kick_pulse = min(1.0, volume_factor * 1.2)
            last_kick_time = now

    stream.stop_stream()
    stream.close()
    p.terminate()


# ------------------- App-exclusion monitor thread -------------------

def excluded_monitor_thread_func():
    """Every ~300ms, check active audio sessions and decide whether to
    suppress the effect.

    Priority apps (browsers, Voicemod, Spotify) always win: if any of them
    is making meaningful noise, the effect stays fully on. Only when no
    priority app is active do we check whether an excluded game is the
    dominant/loudest session, and suppress if so."""
    target = 1.0
    current = 1.0

    while state.running:
        try:
            sessions = AudioUtilities.GetAllSessions()
            peaks = []  # (name, peak)
            for session in sessions:
                proc = session.Process
                if proc is None:
                    continue
                try:
                    meter = session._ctl.QueryInterface(IAudioMeterInformation)
                    peak = meter.GetPeakValue()
                except Exception:
                    peak = 0.0
                try:
                    name = proc.name().lower()
                except Exception:
                    continue
                peaks.append((name, peak))

            if peaks:
                priority_peak = max(
                    (p for n, p in peaks if n in PRIORITY_PROCESSES), default=0.0
                )

                if priority_peak > PRIORITY_ACTIVE_THRESHOLD:
                    target = 1.0
                else:
                    peaks.sort(key=lambda x: x[1], reverse=True)
                    loudest_name, loudest_peak = peaks[0]
                    if loudest_name in EXCLUDED_PROCESSES and loudest_peak > SUPPRESSION_THRESHOLD:
                        target = 0.0
                    else:
                        target = 1.0
            else:
                target = 1.0
        except Exception:
            target = 1.0

        for _ in range(6):
            current += (target - current) * 0.3
            state.suppress = max(0.0, min(1.0, current))
            time.sleep(0.05)


# ----------------------------- Overlay UI -----------------------------

class Overlay(QWidget):
    def __init__(self):
        super().__init__()

        screen = QGuiApplication.primaryScreen()
        geo = screen.virtualGeometry()  # spans all monitors on Windows
        self.screen_w = geo.width()
        self.screen_h = geo.height()

        flags = (
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                       # keeps it off the taskbar / alt-tab
            | Qt.NoDropShadowWindowHint
        )
        if hasattr(Qt, "WindowDoesNotAcceptFocus"):
            flags |= Qt.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)

        self.setAttribute(Qt.WA_TranslucentBackground, True)   # real per-pixel alpha
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # click-through
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self.setGeometry(geo)

        self.glow_color = QColor(GLOW_COLOR)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)  # triggers paintEvent
        self.timer.start(int(1000 / FPS))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        bass = max(0.0, min(1.0, state.bass_level * state.suppress))
        state.kick_pulse *= KICK_DECAY_FACTOR
        kick = max(0.0, min(1.0, state.kick_pulse * state.suppress))

        # --- shake: constant jitter from bass + a bigger jolt from kicks ---
        total_offset = bass * BASS_SHAKE_PIXELS + kick * KICK_SHAKE_PIXELS
        if total_offset > 0.5:
            dx = random.uniform(-total_offset, total_offset)
            dy = random.uniform(-total_offset, total_offset)
        else:
            dx = dy = 0.0
        painter.translate(dx, dy)

        w, h = self.screen_w, self.screen_h
        cx, cy = w / 2.0, h / 2.0
        max_radius = math.hypot(cx, cy)
        pad = int(max(abs(dx), abs(dy))) + 4
        fill_rect = QRectF(-pad, -pad, w + 2 * pad, h + 2 * pad)

        # --- radial vignette: color washes in from edges toward center ---
        if bass > 0.01:
            # As bass increases, the transparent inner stop moves toward
            # the center, so the colored region grows to fill more of
            # the screen.
            inner_stop = max(0.0, min(0.999, 1.0 - (bass ** (1.0 / VIGNETTE_POWER))))
            gradient = QRadialGradient(QPointF(cx, cy), max_radius)

            transparent = QColor(self.glow_color)
            transparent.setAlpha(0)
            gradient.setColorAt(inner_stop, transparent)

            edge_color = QColor(self.glow_color)
            edge_color.setAlpha(int(VIGNETTE_MAX_ALPHA * bass))
            gradient.setColorAt(1.0, edge_color)

            painter.fillRect(fill_rect, gradient)

        # --- full-screen flash punch on kicks ---
        if kick > 0.01:
            flash_color = QColor(self.glow_color)
            flash_color.setAlpha(int(FLASH_MAX_ALPHA * kick))
            painter.fillRect(fill_rect, flash_color)

        painter.end()


def main():
    print("BassShake starting...")
    print("Excluded processes:", ", ".join(sorted(EXCLUDED_PROCESSES)))
    print("Priority processes:", ", ".join(sorted(PRIORITY_PROCESSES)))

    audio_t = threading.Thread(target=audio_thread_func, daemon=True)
    monitor_t = threading.Thread(target=excluded_monitor_thread_func, daemon=True)
    audio_t.start()
    monitor_t.start()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    overlay = Overlay()
    overlay.show()

    if HAVE_KEYBOARD:
        def quit_all():
            state.running = False
            app.quit()
        try:
            keyboard.add_hotkey("ctrl+alt+q", quit_all)
            print("Press Ctrl+Alt+Q to quit.")
        except Exception:
            print("Global hotkey unavailable (may need admin rights). "
                  "Use Task Manager to close BassShake.exe to quit.")

    exit_code = app.exec()
    state.running = False
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
