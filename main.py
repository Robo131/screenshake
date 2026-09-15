"""
BassShake - a bass/kick-reactive screen overlay for Windows.

What it does:
  - Listens to your PC's system audio output (WASAPI loopback - everything
    playing through your speakers/headphones).
  - Runs an FFT to track bass energy and detect kick/transient hits.
  - Drives a transparent, click-through, always-on-top fullscreen overlay
    that glows/swells on bass and jolts on kicks.
  - Every ~300ms it checks which application is currently the loudest audio
    source on your system (via the Windows volume mixer API). If that app
    is in EXCLUDED_PROCESSES (games like Roblox/Rust/Minecraft), the visual
    effect is smoothly suppressed - so gunfire/footsteps in-game won't
    trigger it, but Spotify/Chrome/Discord music will.

Important honest limitation:
  Windows does not let a background app cleanly separate "this stream is
  music" from "this stream is game audio" at the waveform level. The
  loopback capture always contains the full system mix. The exclusion logic
  works by checking which app is *dominant* in the Windows volume mixer at
  that moment, not by filtering the audio itself. This works well in
  practice (if you're in Roblox, Roblox is almost always the loudest
  session) but isn't a perfect guarantee - e.g. Discord/Spotify audio
  playing *underneath* a loud game may still be temporarily suppressed.

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

import tkinter as tk

try:
    from PIL import Image, ImageTk
except ImportError:
    print("Missing dependency: Pillow. Run: pip install -r requirements.txt")
    sys.exit(1)


try:
    import win32gui
    import win32con
    import win32api
except ImportError:
    print("Missing dependency: pywin32. Run: pip install -r requirements.txt")
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
# Add your browser(s) of choice, voice changer, and music apps here.
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
# volume changes, lower = flatter response (effect strength depends mostly
# on bass content, not overall loudness).
VOLUME_SENSITIVITY = 2.5
# Exponent applied to the normalized volume before scaling (>1 makes quiet
# audio contribute even less, <1 flattens the curve).
VOLUME_CURVE = 1.4
# Below this normalized volume (0.0-1.0), the effect is treated as silent.
VOLUME_NOISE_FLOOR = 0.015

# Kick/transient detection: a bass spike this many times above the recent
# rolling average triggers a screen "jolt".
KICK_SPIKE_RATIO = 1.8
KICK_MIN_INTERVAL_SEC = 0.12   # don't re-trigger faster than this
KICK_DECAY_SEC = 0.16          # how fast the jolt settles back to center

# --- Shake (screen jolt) ---
# BASS_SHAKE_PIXELS: a small constant jitter while bass is present, so the
# screen feels "alive" during a rolling bassline, not just on hits.
# KICK_SHAKE_PIXELS: a bigger jolt layered on top for individual kick hits.
BASS_SHAKE_PIXELS = 6
KICK_SHAKE_PIXELS = 32

# --- Full-screen glow/swell look ---
GLOW_COLOR = "#7fdfff"         # tint color; change to taste, e.g. "#ff3366"

# Radial vignette: color washes in from the screen edges toward the
# center as bass builds. Higher VIGNETTE_POWER keeps the center clearer
# for longer (color stays concentrated near the edges); lower values let
# it fill the whole screen sooner.
VIGNETTE_POWER = 1.6
VIGNETTE_MAX_ALPHA = 175       # 0-255, how opaque the glow gets at max bass
VIGNETTE_LEVELS = 24           # precomputed intensity steps (smoothness vs. startup time/RAM)

# Full-screen flash: a brief, near-uniform color wash across the ENTIRE
# screen on kick hits, layered on top of the vignette - this is what
# sells the "whole monitor swelling" feeling rather than just edges.
FLASH_MAX_ALPHA = 130
FLASH_LEVELS = 16

# Audio capture
CHUNK = 1024
AUDIO_FORMAT_BITS = 16

# Frames per second for the overlay redraw loop
FPS = 60

# ================================================================


class SharedState:
    """Thread-safe-enough shared values between audio thread and UI thread.
    Reads/writes of floats are effectively atomic in CPython, so no lock
    needed for this simple case."""
    def __init__(self):
        self.bass_level = 0.0       # smoothed 0..1 swell amount (volume-scaled)
        self.kick_pulse = 0.0       # 0..1, decays after a kick, drives jolt
        self.volume_level = 0.0     # smoothed 0..1 overall loudness, for debugging/tuning
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

        # Normalize roughly against int16 range * chunk size
        norm = bass_energy / (32768.0 * CHUNK / 4.0)
        norm = min(1.0, norm * BASS_SENSITIVITY)

        # Smooth the swell value so it doesn't flicker, then scale by
        # actual playback volume so quiet audio barely moves the screen.
        smoothed_bass = smoothed_bass * 0.75 + norm * 0.25
        state.bass_level = smoothed_bass * volume_factor

        # Kick / transient detection off the rolling average
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
                    # A trusted app (browser/Voicemod/Spotify) is active -
                    # keep the effect on regardless of anything else playing.
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

        # Smooth transition so suppression doesn't feel like a hard cut
        for _ in range(6):
            current += (target - current) * 0.3
            state.suppress = max(0.0, min(1.0, current))
            time.sleep(0.05)


# ----------------------------- Overlay UI -----------------------------

class Overlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")

        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        self.base_x = 0
        self.base_y = 0
        self.root.geometry(f"{self.screen_w}x{self.screen_h}+0+0")

        # Black becomes fully transparent (Windows-only trick)
        self.root.attributes("-transparentcolor", "black")

        self.canvas = tk.Canvas(
            self.root, width=self.screen_w, height=self.screen_h,
            bg="black", highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)

        self.root.update_idletasks()
        self._make_click_through()

        print("Warming up visuals (precomputing glow frames)...")
        self.vignette_frames = self._build_gradient_frames(
            levels=VIGNETTE_LEVELS, max_alpha=VIGNETTE_MAX_ALPHA,
            power=VIGNETTE_POWER, radial=True,
        )
        self.flash_frames = self._build_gradient_frames(
            levels=FLASH_LEVELS, max_alpha=FLASH_MAX_ALPHA,
            power=1.0, radial=False,
        )
        print("Ready.")

        self.vignette_image_id = self.canvas.create_image(
            0, 0, anchor="nw", image=self.vignette_frames[0]
        )
        self.flash_image_id = self.canvas.create_image(
            0, 0, anchor="nw", image=self.flash_frames[0]
        )

    def _make_click_through(self):
        hwnd = win32gui.FindWindow(None, self.root.title() or None)
        if not hwnd:
            # fallback: get hwnd via winfo_id
            hwnd = self.root.winfo_id()
        try:
            styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            styles |= win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, styles)
        except Exception:
            pass  # if this fails, overlay still works, just won't be click-through

    @staticmethod
    def _hex_to_rgb(hex_color):
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

    def _build_gradient_frames(self, levels, max_alpha, power, radial):
        """Precompute a list of full-screen RGBA PhotoImages, one per
        intensity level (0 = invisible, `levels` = full max_alpha), so the
        animation loop just swaps images instead of doing per-frame image
        math. `radial=True` gives edge-to-center vignette; `radial=False`
        gives a near-uniform full-screen wash (used for the kick flash)."""
        w, h = self.screen_w, self.screen_h
        color = self._hex_to_rgb(GLOW_COLOR)

        if radial:
            ys, xs = np.mgrid[0:h, 0:w]
            cx, cy = w / 2.0, h / 2.0
            dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
            max_dist = math.sqrt(cx ** 2 + cy ** 2)
            norm = np.clip(dist / max_dist, 0.0, 1.0)
            weight = norm ** power
        else:
            weight = np.ones((h, w), dtype=np.float64)

        frames = []
        for level in range(levels + 1):
            intensity = level / levels
            alpha = (weight * intensity * max_alpha).astype(np.uint8)
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            rgba[..., 0] = color[0]
            rgba[..., 1] = color[1]
            rgba[..., 2] = color[2]
            rgba[..., 3] = alpha
            img = Image.fromarray(rgba, mode="RGBA")
            frames.append(ImageTk.PhotoImage(img))
        return frames

    def frame(self):
        bass = max(0.0, min(1.0, state.bass_level * state.suppress))
        state.kick_pulse *= 0.80  # decay each frame
        kick = max(0.0, min(1.0, state.kick_pulse * state.suppress))

        # --- swap in the right precomputed glow/flash frames ---
        v_idx = int(round(bass * VIGNETTE_LEVELS))
        v_idx = max(0, min(VIGNETTE_LEVELS, v_idx))
        self.canvas.itemconfig(self.vignette_image_id, image=self.vignette_frames[v_idx])

        f_idx = int(round(kick * FLASH_LEVELS))
        f_idx = max(0, min(FLASH_LEVELS, f_idx))
        self.canvas.itemconfig(self.flash_image_id, image=self.flash_frames[f_idx])

        # --- shake: a subtle constant jitter while bass is present, plus
        # a bigger jolt layered on top for individual kick hits ---
        bass_offset = bass * BASS_SHAKE_PIXELS
        kick_offset = kick * KICK_SHAKE_PIXELS
        total_offset = bass_offset + kick_offset

        if total_offset > 0.5:
            dx = int(random.uniform(-total_offset, total_offset))
            dy = int(random.uniform(-total_offset, total_offset))
        else:
            dx = dy = 0

        self.root.geometry(f"{self.screen_w}x{self.screen_h}+{dx}+{dy}")

        self.root.after(int(1000 / FPS), self.frame)

    def run(self):
        self.frame()
        self.root.mainloop()


def main():
    print("BassShake starting...")
    print("Excluded processes:", ", ".join(sorted(EXCLUDED_PROCESSES)))

    audio_t = threading.Thread(target=audio_thread_func, daemon=True)
    monitor_t = threading.Thread(target=excluded_monitor_thread_func, daemon=True)
    audio_t.start()
    monitor_t.start()

    if HAVE_KEYBOARD:
        def quit_all():
            state.running = False
            time.sleep(0.1)
            sys.exit(0)
        try:
            keyboard.add_hotkey("ctrl+alt+q", quit_all)
            print("Press Ctrl+Alt+Q to quit.")
        except Exception:
            print("Global hotkey unavailable (may need admin rights). "
                  "Use Task Manager to close BassShake.exe to quit.")

    overlay = Overlay()
    try:
        overlay.run()
    finally:
        state.running = False


if __name__ == "__main__":
    main()
