BassShake
A bass/kick-reactive screen overlay for Windows. It listens to whatever is
playing through your PC's speakers, and glows/swells a border on bass and
jolts the whole overlay on kick hits — great for bassline, organ house,
techno, etc. It automatically dials itself down while Roblox, Rust, or
Minecraft are the loudest thing playing, so in-game gunfire/footsteps won't
set it off.
Windows only. Uses WASAPI loopback audio capture and the Windows volume
mixer API, neither of which exist on Mac/Linux.
How the game-exclusion actually works (read this)
Windows doesn't give background apps a clean way to say "only listen to
Spotify, not Roblox" — loopback capture always picks up the full system
audio mix. What this tool does instead: every ~300ms it checks the Windows
volume mixer for two things:
Priority apps — browsers (Chrome, Edge, Firefox, Brave, Opera,
Vivaldi), Voicemod, and Spotify. If any of these is making meaningful
noise, the effect stays fully on, no matter what else is playing —
these are treated as trusted sources.
Only if no priority app is active does it check whether an excluded
game (Roblox/Rust/Minecraft) is the loudest session, and if so, it
suppresses the effect.
This works well in the common case (game audio dominates while you're
in-game, browser/Spotify/Voicemod dominates otherwise), but it's a
heuristic, not true isolation — very quiet background music under loud
game audio may still get suppressed.
You can edit `EXCLUDED_PROCESSES` and `PRIORITY_PROCESSES` at the top of
`main.py` to add or remove any executable name.
Volume sensitivity
The effect now scales with how loud the audio actually is, not just its
bass content — quiet background music barely moves the screen, loud music
hits hard. Tune this with:
`VOLUME_SENSITIVITY` — overall multiplier (higher = more dramatic scaling with loudness)
`VOLUME_CURVE` — exponent on the volume curve (>1 makes quiet audio contribute even less; <1 flattens it out)
`VOLUME_NOISE_FLOOR` — below this loudness, the effect is treated as silent (stops idle-noise jitter)
The full-screen swell/shake effect
Instead of a thin border, the overlay now washes color in from every edge
of the screen toward the center as bass builds (a radial vignette), plus a
near-uniform full-screen color flash on kick hits layered on top. Shake is
now two-part: a small constant jitter while bass is present (so a rolling
bassline feels alive), plus a bigger jolt for individual kicks.
Tune the feel with:
`VIGNETTE_POWER` — how far the glow reaches toward center (lower = fills more of the screen sooner)
`VIGNETTE_MAX_ALPHA` / `FLASH_MAX_ALPHA` — max opacity (0-255) of the edge glow / full-screen flash
`BASS_SHAKE_PIXELS` / `KICK_SHAKE_PIXELS` — constant jitter amount vs. kick jolt amount
Honest limitation: this overlay can only draw on top of your screen —
it can't physically move your desktop icons or the windows underneath,
since Windows doesn't expose a way for a regular app to do that. What
you're seeing is a full-screen glow + the whole overlay window jolting
position, which reads as a real screen shake without disrupting anything
running underneath (games, browsers, etc. stay fully interactive since the
overlay is click-through).
Performance note: at startup it precomputes ~40 full-screen glow
images so the animation loop doesn't have to do image math every frame —
you'll see "Warming up visuals..." briefly (a second or two) before the
overlay appears. If you have a very high-res or multi-monitor setup and
it uses more startup memory/time than you'd like, lower `VIGNETTE_LEVELS`
and `FLASH_LEVELS` in `main.py` (fewer precomputed steps = less RAM/startup
time, at the cost of slightly less smooth transitions).
Setup (one time)
Install Python 3.9+ if you don't
have it. During install, check "Add Python to PATH".
Put `main.py`, `requirements.txt`, and `build.bat` in the same folder.
Double-click `build.bat`. It will:
install the required packages
package everything into a single `dist\BassShake.exe`
Run `dist\BassShake.exe`. No console window will appear — it just runs
in the background as a transparent overlay.
Press Ctrl+Alt+Q to quit it (or close it from Task Manager if the
hotkey doesn't register — it needs to run as admin on some setups).
You only need to re-run `build.bat` if you change `main.py`.
Tuning it to taste
Open `main.py` and edit the config block near the top:
Setting	What it does
`EXCLUDED_PROCESSES`	executable names to ignore
`PRIORITY_PROCESSES`	trusted executable names (browsers, Voicemod, Spotify) that always keep the effect on
`PRIORITY_ACTIVE_THRESHOLD`	how loud a priority app needs to be to count as "active"
`SUPPRESSION_THRESHOLD`	how loud an excluded app needs to be before it suppresses the effect
`BASS_LOW_HZ` / `BASS_HIGH_HZ`	frequency band used for the swell (default 25–150Hz covers sub-bass/kick/bassline)
`BASS_SENSITIVITY`	overall intensity multiplier for the glow swell
`VOLUME_SENSITIVITY` / `VOLUME_CURVE` / `VOLUME_NOISE_FLOOR`	how strongly overall loudness scales the effect
`KICK_SPIKE_RATIO`	how sharp a bass spike needs to be to count as a "kick" and trigger a jolt
`BASS_SHAKE_PIXELS` / `KICK_SHAKE_PIXELS`	constant jitter vs. kick jolt shake amount
`GLOW_COLOR`	hex color of the full-screen glow/flash
`VIGNETTE_POWER` / `VIGNETTE_MAX_ALPHA` / `FLASH_MAX_ALPHA`	how the full-screen swell/flash looks and how far it reaches
After editing, just run `build.bat` again to rebuild the exe.
Notes / known limitations
It reacts to whatever your default playback device is outputting. If
you switch output devices (e.g. speakers → headphones) while it's
running, restart the app.
The "shake" moves the overlay window itself — it can't physically move
your desktop icons or other app windows, since Windows doesn't allow
that. What you'll see is the glow border jolting position, which reads
as a screen shake without disturbing anything underneath.
If Ctrl+Alt+Q doesn't work, run the exe as Administrator, or just end
the `BassShake.exe` process in Task Manager.
