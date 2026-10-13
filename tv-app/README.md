# Northern Lights TV app

A small Android TV / Google TV app (Kotlin, no Capacitor) for the Northern Lights player. It is a WebView that loads `<server>/nl/tv/` plus a native Media3 (ExoPlayer) player, because the TV decodes far more formats natively (HEVC, AC3/EAC3, MKV) than a WebView does.

It only talks to our FastAPI server. It never holds or sends a Jellyfin key.

- Package `app.northernlights.tv`, minSdk 24, target/compile SDK 35.
- AGP 8.7.3, Kotlin 2.0.21, Gradle 8.10.2 (wrapper), AndroidX Media3 1.5.1 (`exoplayer`, `exoplayer-hls`, `ui`, `session`).

## Get the APK

1. GitHub: Actions, workflow "TV app", pick the latest run for your branch.
2. Download the artifact `northern-lights-tv-debug` (a zip containing `app-debug.apk`).

The workflow runs on changes under `tv-app/` and on "Run workflow". To build locally you need JDK 17 and the Android SDK (`ANDROID_HOME`): `cd tv-app && ./gradlew assembleDebug`; the APK lands in `app/build/outputs/apk/debug/`.

## Install on a TCL Google TV (sideload)

Enable developer mode on the TV:

1. Settings, System, About, click "Android TV OS build" 7 times.
2. Settings, System, Developer options: turn on USB debugging and Network (wireless) debugging.

Then, from a computer on the same network, using adb:

```
adb connect <tv-ip>:5555
adb install -r app-debug.apk
```

`-r` installs over the copy already on the TV and keeps its saved server address. Every build is signed with the committed `app/debug.keystore`, so updates always match. If adb says `INSTALL_FAILED_UPDATE_INCOMPATIBLE` (a copy from before that key), run `adb uninstall app.northernlights.tv` once and install again.

(Newer Google TV builds show a pairing code under Wireless debugging; use `adb pair <tv-ip>:<port>` first, then `adb connect`.)

Alternative without a computer: install the "Downloader" app from the Play Store, allow it under Settings, Apps, Special app access, Install unknown apps, open it and enter the URL of the APK (for example a file served from the Mac), then install.

"Northern Lights" then appears in the Google TV apps row (and can be pinned to the home screen).

## Server address

On first launch the app asks for the server address, prefilled `http://192.168.1.`. Type the rest with the remote (for example `http://192.168.1.20:8000`). A missing port means 8000. Pressing Save checks `GET <address>/healthz`; it saves only when that answers.

To change it later: press Menu (the remote's three-lines or asterisk button), or hold Back on the home page. If the page can't load, the error screen has "Try again" and "Change server".

Cleartext HTTP is allowed (see `res/xml/network_security_config.xml`): the app is LAN-only and the host is whatever you type, which a per-domain rule cannot cover. WebView navigation is limited to the server's origin.

## Remote control

- Browsing: D-pad and Enter go to the web page. Back goes back in the page; on the first screen press Back twice to exit.
- Playing: the on-screen player is the web page's own (same controls as `/nl/play/{id}?tv=1` in a browser): Left/Right skip by the chosen length, OK pauses, Up/Down open the controls (seek bar with times, skip buttons, skip-length chip, Audio and Subtitles menus), Back closes a menu or leaves and returns to the page where you left it. The app has no player UI of its own: Media3 only decodes and draws the picture (and text subtitles) behind the transparent WebView. Media keys (play/pause, rewind, fast forward) reach the page too, and Media3's MediaSession stays for the system.

## How progress works

The page calls `window.NorthernLightsNative.play(json)`; the app plays it natively and reports back with `window.NLNativeEvents.dispatch(json)`: `started`, `time` (every 500 ms), `progress` (every 10 s while playing), `paused`, `resumed`, `seeked`, `stopped` (the app went to the background), `ended` and `error`. Pause, resume, seeking and the text-subtitle choice are calls from the page; an audio or picture-subtitle change makes the page fetch a new stream and call `play` again. The page turns these into the same `/api/nl/progress` calls the browser player makes, so "Continue Watching" works the same on the TV. Leaving the app with Home also sends `stopped`.

### Bridge contract (version 4: version 3 plus `setSpeed`; version 3 is version 2 plus `setSubtitleStyle`)

`window.NorthernLightsNative` exists only on pages from the saved server origin:

- `play(json)`: `{url, kind ("file"|"hls"), startSeconds, title, subtitle, skipSeconds, audioTracks:[{index,label,language,isDefault}], subtitleTracks:[{index,label,language,url,kind}], audioIndex, subtitleIndex, nextUp: null|{label}}`. All URLs must be absolute and on the server origin; anything else is refused with an `error` event.
- `pause()`, `resume()`, `seekTo(seconds)` (inside the current stream), `setSubtitle(index)` (side-loaded text track by the page's track index, -1 = off), `stop()`, `setSubtitleStyle(json)` (version 3: `{size: S|M|L|XL, background: none|shadow|box, raise: 0|1|2}` applied to the PlayerView's `SubtitleView` with `CaptionStyleCompat`, `setFractionalTextSize` and `setBottomPaddingFraction`; the page sends it at start and on every change; there is no subtitle delay on the TV because Media3 1.5.1 has no subtitle offset), `setSpeed(rate)` (version 4: `"0.75"` .. `"2"` as a string, applied with `player.setPlaybackSpeed(float)` and kept for the next stream; an app without it never sees the call because the page checks the version and hides its speed menu), `version()` (returns `"4"`; `"3"` and `"2"` apps still work, the page just hides the appearance settings (v2) or the speed menu (v2, v3); version 1 was the app with its own native controls, and the page still supports it by keeping its web controls hidden). A freshly downloaded subtitle reaches the TV player by the page calling `play(json)` again at the current position with the new track in `subtitleTracks`.
- Events: `{type, positionSeconds, durationSeconds, bufferedSeconds (ahead of the playhead), playing, message?}`.
- Layering: the `PlayerView` (controller off) is behind the WebView; while playback is active the WebView background is transparent, the page is transparent too (`html.native`), and every key goes to the WebView (Back is sent to the page as Escape).
