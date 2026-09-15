BassShake
A bass/kick-reactive screen overlay for Windows. It listens to whatever is
playing through your PC's speakers, and washes a color glow across the
whole screen on bass + jolts on kick hits — great for bassline, organ
house, techno, etc. It automatically dials itself down while Roblox, Rust,
or Minecraft are the loudest thing playing, so in-game gunfire/footsteps
won't set it off, while browsers/Voicemod/Spotify always keep it active.
Windows only. Uses WASAPI loopback audio capture and the Windows volume
mixer API, neither of which exist on Mac/Linux.
Why this version looks different under the hood
The first version used Tkinter's "-transparentcolor" trick for
transparency, which only makes pixels that are exactly pure black
invisible — it can't do a soft fade. A gradient built that way rendered as
a solid, opaque near-black block everywhere it wasn't perfectly pure
black, which is why it looked like a black screen and blocked clicks.
This version uses Qt (PySide6) instead, which has real per-pixel alpha
transparency (`WA_TranslucentBackground`) and genuine click-through
(`WA_TransparentForMouseEvents`) — the same underlying Windows layered-
window tech that real overlay/OSD apps use. The glow is drawn live every
frame with a radial gradient instead of precomputed images, so there's no
more "warming up" delay either.
How the game-exclusion actually works (read this)
Windows doesn't give background apps a clean way to say "only listen to
Spotify, not Roblox" — loopback capture always picks up the full system
audio mix. What this tool does instead: every ~300ms it checks the Windows
volume mixer for two things:
Priority apps — browsers (Chrome, Edge, Firefox, Brave, Opera,
Vivaldi), Voicemod, and Spotify. If any of these is making meaningful
noise, the effect stays fully on, no matter what else is playing.
Only if no priority app is active does it check whether an excluded
game (Roblox/Rust/Minecraft) is the loudest session, and if so, it
suppresses the effect.
This is a heuristic, not true audio isolation — very quiet background
music under loud game audio may still get suppressed. Edit
`EXCLUDED_PROCESSES` and `PRIORITY_PROCESSES` at the top of `main.py` to
add or remove any executable name.
Volume sensitivity
The effect scales with how loud the audio actually is, not just its bass
content — quiet background music barely moves the screen, loud music hits
hard. Tune with `VOLUME_SENSITIVITY`, `VOLUME_CURVE`, `VOLUME_NOISE_FLOOR`.
The full-screen swell/shake effect
Color washes in from every edge of the screen toward the center as bass
builds (a radial gradient), plus a near-uniform full-screen flash on kick
hits layered on top. Shake is two-part: a small constant jitter while bass
is present, plus a bigger jolt for individual kicks — both are applied as
an internal paint offset, so the window itself never has to move (smoother
than repositioning the OS window every frame).
Tune the feel with:
`VIGNETTE_POWER` — how far the glow reaches toward center (lower = fills more of the screen sooner)
`VIGNETTE_MAX_ALPHA` / `FLASH_MAX_ALPHA` — max opacity (0-255) of the edge glow / full-screen flash
`BASS_SHAKE_PIXELS` / `KICK_SHAKE_PIXELS` — constant jitter amount vs. kick jolt amount
`GLOW_COLOR` — hex color of the glow/flash
Honest limitation: this overlay can only draw on top of your screen —
it can't physically move your desktop icons or the windows underneath,
since Windows doesn't expose a way for a regular app to do that. What
you're seeing is a full-screen glow + jolt drawn on a transparent
click-through layer, which reads as a real screen shake without
disrupting anything running underneath.
Setup (one time)
Install Python 3.9+ if you don't
have it. During install, check "Add Python to PATH".
Put `main.py`, `requirements.txt`, and `build.bat` in the same folder.
Double-click `build.bat`. It will:
install the required packages (this now includes Qt via PySide6, so
the install + build take noticeably longer and the exe is bigger,
roughly 100-200MB — that's normal)
package everything into a single `dist\BassShake.exe`
Run `dist\BassShake.exe`. No console window will appear — it just runs
in the background as a transparent overlay.
Press Ctrl+Alt+Q to quit it (or close it from Task Manager if the
hotkey doesn't register — it needs to run as admin on some setups).
You only need to re-run `build.bat` if you change `main.py`.
Building via GitHub Actions instead
If you're using the `.github/workflows/build.yml` route, no changes are
needed there — it already runs `pip install -r requirements.txt` and
`pyinstaller --onefile --noconsole --name BassShake main.py`, which works
the same way for the Qt version. The build will just take a bit longer and
the artifact will be bigger than before.
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
After editing, just run `build.bat` again (or re-trigger the GitHub
Actions workflow) to rebuild the exe.
Notes / known limitations
It reacts to whatever your default playback device is outputting. If
you switch output devices (e.g. speakers → headphones) while it's
running, restart the app.
The overlay spans all your monitors if you have more than one.
If Ctrl+Alt+Q doesn't work, run the exe as Administrator, or just end
the `BassShake.exe` process in Task Manager.
This was built and reasoned through carefully but not run on a live
Windows machine before you tested it — if anything about the Qt window
flags behaves slightly differently on your Windows build/GPU driver,
let me know what you see and we'll adjust.
