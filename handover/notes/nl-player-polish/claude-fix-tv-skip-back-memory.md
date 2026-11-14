---
feature: nl-player-polish
status: ready
---
## Line
- **M7a Player polish** (merged, `nl_player_extras.py`): trickplay thumbnails, chapters, skip intro / credits, phone gestures, speed. 2026-10-28 (Labiba): "Ends at" clock above the seek bar (`NLPlayerExtras.endsAt`, counts the speed); "Subtitle settings…" is the first item of the subtitle menu; TV Back first hides the controls, then shows "Press Back again to stop watching" and leaves only on a second press within 3 s (`NLPlayerExtras.backAction`). Quick skip presses (D-pad, buttons, keys) are grouped into one jump 0.35 s after the last press, counted from the pending target (`NLPlayerExtras.skipper`); the buffering spinner shows only if playback still waits 0.7 s (`NLPlayerExtras.spinner`). TV skip back (2026-11-14, needs the TV app update): `bufferPlan()` keeps what was just played in memory as far as the TV has room (up to 128 MB, at least 24 MB, after a 96 MB margin of free memory), and its length follows the file's bitrate (`bitrateKbps` in play(), from `quality.source_kbps`): about 10 s of a 92 Mbps remux, up to 60 s of a light film. Before it was 10 s or nothing, and the TV is often just under the 224 MB free it needed, so skips back were slow on some nights. Playback info shows "Kept for skip back".

## Open work
- **TV skip, next:** a 92 Mbps remux keeps only about 15 s ahead and 10 s behind in memory on the TV, so jumps beyond that must still load. Check "Kept for skip back" in Playback info on the real TV.

## What changed
Skip back on the TV was sometimes slow (Labiba, 2026-11-14): the back buffer was all or nothing.
