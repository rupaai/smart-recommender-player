---
feature: nl-player-polish
status: ready
---
## Line
- **M7a Player polish** (merged, `nl_player_extras.py`): trickplay thumbnails, chapters, skip intro / credits, phone gestures, speed. 2026-10-28 (Labiba): "Ends at" clock above the seek bar (`NLPlayerExtras.endsAt`, counts the speed); "Subtitle settings…" is the first item of the subtitle menu; TV Back first hides the controls, then shows "Press Back again to stop watching" and leaves only on a second press within 3 s (`NLPlayerExtras.backAction`). Quick skip presses (D-pad, buttons, keys) are grouped into one jump 0.35 s after the last press, counted from the pending target (`NLPlayerExtras.skipper`); the buffering spinner shows only if playback still waits 0.7 s (`NLPlayerExtras.spinner`).

## Open work
- **TV skip, next:** a 92 Mbps remux keeps only about 15 s ahead in memory on the TV (`DEFAULT_BUFFER_BYTES` in tv-app `PlaybackLogic.kt`), so jumps beyond that must still load.

## What changed
Ten quick 5 s skips now make one seek instead of ten, so the TV flushes its decoders once. The spinner no longer flashes on a seek inside the buffer.
