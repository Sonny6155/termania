# termania
A highly portable version of DDR/Stepmania for Mac/Unix terminals to play on the go.

### Supported features
- Tap, hold, roll, and mine notes
- Semi-compliant SM file format parser
- Dynamic BPM changes (XMod)
- Constant speed rendering (CMod)
- Negative BPMs/stops (as defined by SM)
- Minimal HUD
- Various configs
  - Scroll factor (aka note speed)
  - Key mappings for n-K charts
  - Offset

## Installation & Usage
In the project directory:
```
pip install -r requirements.txt
python main.py --help
```

Examples:
```
python main.py "songs/V^3 (Hello World)" --scroll 0.75
python main.py songs/Cloudless 1 --scroll 2 --keys "zxcv" --offset -0.002
python main.py songs/Cloudless 1 --scroll 4.6667 --cmod
```

You may need to enable input permissions during usage.
If the chart doesn't load, ensure that the "#MUSIC" tag in the SM file uses the correct music filename/path.

### Controls
- Esc: Toggle pause
- Backspace: Exit program
- D, F, J, K: Default 4K key mappings

## Technical Notes
### Design considerations
Graphics is purely text-based, since sixel is unsupported on the default terminal for Mac and GNOME.
For the same reason, colour space should be 8-bit and should only use ASCII for consistent width/support.

Python is fine because the Mac terminal refresh rate is the far greater bottleneck.
But the libraries should be kept minimal so that it is lightweight, easily rewritable for another language/application if needed, and as a learning experience.
As such:
- just\_playback was used as a tiny audio engine for syncing all threads. Surprisingly well-supported at time of writing.
- pynput was used to overcome the limitations of a terminal's stdin.
- msdparser greatly simplifies MSD format parsing for SM, SSC and DWI.

Finally, it needs to be able to parse existing Stepmania charts into the internal format (at bare minimum, SM format, but eventually SSC and SSQ) to be realistically practical.

### Notable limitations
The vertical axis is the traditional, most intuitive play direction and also the easiest to draw in curses.
However, it is also the worst axis for gameplay because rendered chars are usually taller than wide, causing the illusion of flickering.
So while the original V^3 gimmick map works, it's not particularly recommended.
One option is to render thicker notes and increase the note spacing/speed to improve the illusion of steady note movement,
but the limited vertical resolution means this can only go so far (without a tall monitor or smaller char cell).

The limited resolution also means highly dense charts may not render quite as well on low scroll factors, as scroll=1 will round 16th+ notes to the same row while 12th-ish notes rely more on rhythm than visuals.

Due to y rounding, CMod may look jittery if the exact correct BPS is not used for scroll speed, where XMod would render at constant spacing for integer scrolls. For a constant 140 BPM song, scroll 2 on XMod equals 4.6667 on CMod (aka 140 / 60). Short of checking the file itself, the next best fix is simply to play at a sufficiently fast BPM.

## Resources Used
A Hackaday blog post proposing a graph solution. No code given, but re-implemented the concept into an avg O(1) lookup:
- https://hackaday.io/project/10136-autogenerated-light-shows-from-music/log/36998-how-to-determine-when-each-note-happens-in-a-stepfile

FFR forum post of old and current Stepmania charting exploits. Explanations are inaccurate/lacking, but the descriptions of expected behaviour were very helpful in reverse engineering the inner workings:
- https://www.flashflashrevolution.com/vbz/showthread.php?t=113152

The official SM wiki for spec, plus the wiki of other parser projects:
- https://github.com/stepmania/stepmania/wiki/sm
- https://github.com/stepmania/stepmania/wiki/ssc
- https://github.com/barrysir/stepmania-parsing-code/wiki/.sm-file-format

Docs or interface source for utilised libraries:
- https://msdparser.readthedocs.io/en/latest/
- https://github.com/cheofusi/just\_playback

### Tools
Vim (purely for learning), miniconda+pip, Desmos, Figma, Toggl

## Backlog (might move to Issues)
Todo:
- Colours
  - Notes should be coloured by their measure fraction and judgement state
  - Measure lines can be made a less intrusive colour
  - Judgement colour scheme (maybe emulate DDR colours or by early/lateness)
- SSC format parser
  - Must support WARP
  - Both SM/SSC should parse the less common aliases
- Custom format and its parser
  - Should allow true negative BPM as backrolling charts
- Hitsounds/claps/explosions
- Loading/results screens (or results file dump)
- Fade out audio exit for graceful end
- Live rewind/seek/loop section
- Live slow/pitch
  - Requires swapping out just\_playback with unthreaded lib or raw
  - May need to custom handle time-stretch algorithm?
- Document theory more thoroughly, but maybe out of repo
- Move out name-main test cases into pytest

Won't do (any time soon):
- Dynamic scroll factors
- Explicit lift notes
  - Easy to implement in pynput, unfun to play
- DP scoring
- ANSI-based input
  - Would require no permissions
  - Holds work already with autorepeat rate tweaks, but rolls become redundant
  - Best left to a separate repo
- Chart search/navigator
  - Best left to a separate repo
- Arrow heads and note skins
  - Without a better framework for it, this is too difficult to implement well
  - Not to mention, the limited vertical resolution means it usually doesn't fit nicely
