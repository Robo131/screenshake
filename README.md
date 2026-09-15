# BassShake

A bass/kick-reactive screen overlay for Windows. It listens to whatever is
playing through your PC's speakers, and glows/swells a border on bass and
jolts the whole overlay on kick hits — great for bassline, organ house,
techno, etc. It automatically dials itself down while Roblox, Rust, or
Minecraft are the loudest thing playing, so in-game gunfire/footsteps won't
set it off.

**Windows only.** Uses WASAPI loopback audio capture and the Windows volume
mixer API, neither of which exist on Mac/Linux.

## How the game-exclusion actually works (read this)

Windows doesn't give background apps a clean way to say "only listen to
Spotify, not Roblox" — loopback capture always picks up the full system
audio mix. What this tool does instead: every ~300ms it checks the Windows
volume mixer for two things:

1. **Priority apps** — browsers (Chrome, Edge, Firefox, Brave, Opera,
   Vivaldi), Voicemod, and Spotify. If any of these is making meaningful
   noise, the effect stays fully **on**, no matter what else is playing —
   these are treated as trusted sources.
2. Only if no priority app is active does it check whether an **excluded
   game** (Roblox/Rust/Minecraft) is the loudest session, and if so, it
   suppresses the effect.

This works well in the common case (game audio dominates while you're
in-game, browser/Spotify/Voicemod dominates otherwise), but it's a
heuristic, not true isolation — very quiet background music under loud
game audio may still get suppressed.

You can edit `EXCLUDED_PROCESSES` and `PRIORITY_PROCESSES` at the top of
`main.py` to add or remove any executable name.

## Volume sensitivity

The effect now scales with how loud the audio actually is, not just its
bass content — quiet background music barely moves the screen, loud music
hits hard. Tune this with:

- `VOLUME_SENSITIVITY` — overall multiplier (higher = more dramatic scaling with loudness)
- `VOLUME_CURVE` — exponent on the volume curve (>1 makes quiet audio contribute even less; <1 flattens it out)
- `VOLUME_NOISE_FLOOR` — below this loudness, the effect is treated as silent (stops idle-noise jitter)

## Setup (one time)

1. Install [Python 3.9+](https://www.python.org/downloads/) if you don't
   have it. During install, check "Add Python to PATH".
2. Put `main.py`, `requirements.txt`, and `build.bat` in the same folder.
3. Double-click `build.bat`. It will:
   - install the required packages
   - package everything into a single `dist\BassShake.exe`
4. Run `dist\BassShake.exe`. No console window will appear — it just runs
   in the background as a transparent overlay.
5. Press **Ctrl+Alt+Q** to quit it (or close it from Task Manager if the
   hotkey doesn't register — it needs to run as admin on some setups).

You only need to re-run `build.bat` if you change `main.py`.

## Tuning it to taste

Open `main.py` and edit the config block near the top:

| Setting | What it does |
|---|---|
| `EXCLUDED_PROCESSES` | executable names to ignore |
| `PRIORITY_PROCESSES` | trusted executable names (browsers, Voicemod, Spotify) that always keep the effect on |
| `PRIORITY_ACTIVE_THRESHOLD` | how loud a priority app needs to be to count as "active" |
| `SUPPRESSION_THRESHOLD` | how loud an excluded app needs to be before it suppresses the effect |
| `BASS_LOW_HZ` / `BASS_HIGH_HZ` | frequency band used for the swell (default 25–150Hz covers sub-bass/kick/bassline) |
| `BASS_SENSITIVITY` | overall intensity multiplier for the glow swell |
| `VOLUME_SENSITIVITY` / `VOLUME_CURVE` / `VOLUME_NOISE_FLOOR` | how strongly overall loudness scales the effect |
| `KICK_SPIKE_RATIO` | how sharp a bass spike needs to be to count as a "kick" and trigger a jolt |
| `KICK_SHAKE_PIXELS` | max pixel offset for the shake jolt |
| `GLOW_COLOR` | hex color of the border glow |
| `GLOW_MAX_THICKNESS` | how thick the border gets at max bass |

After editing, just run `build.bat` again to rebuild the exe.

## Notes / known limitations

- It reacts to whatever your **default playback device** is outputting. If
  you switch output devices (e.g. speakers → headphones) while it's
  running, restart the app.
- The "shake" moves the *overlay window itself* — it can't physically move
  your desktop icons or other app windows, since Windows doesn't allow
  that. What you'll see is the glow border jolting position, which reads
  as a screen shake without disturbing anything underneath.
- If Ctrl+Alt+Q doesn't work, run the exe as Administrator, or just end
  the `BassShake.exe` process in Task Manager.
