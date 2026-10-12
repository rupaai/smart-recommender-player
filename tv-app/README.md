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

(Newer Google TV builds show a pairing code under Wireless debugging; use `adb pair <tv-ip>:<port>` first, then `adb connect`.)

Alternative without a computer: install the "Downloader" app from the Play Store, allow it under Settings, Apps, Special app access, Install unknown apps, open it and enter the URL of the APK (for example a file served from the Mac), then install.

"Northern Lights" then appears in the Google TV apps row (and can be pinned to the home screen).

## Server address

On first launch the app asks for the server address, prefilled `http://192.168.1.`. Type the rest with the remote (for example `http://192.168.1.20:8000`). A missing port means 8000. Pressing Save checks `GET <address>/healthz`; it saves only when that answers.

To change it later: press Menu (the remote's three-lines or asterisk button), or hold Back on the home page. If the page can't load, the error screen has "Try again" and "Change server".

Cleartext HTTP is allowed (see `res/xml/network_security_config.xml`): the app is LAN-only and the host is whatever you type, which a per-domain rule cannot cover. WebView navigation is limited to the server's origin.

## Remote control

- Browsing: D-pad and Enter go to the web page. Back goes back in the page; on the first screen press Back twice to exit.
- Playing: Left/Right skip by the page's chosen length (hold to speed up, up to 6x), Enter or Play/Pause toggles and shows the controls, Up opens the Audio / Subtitles menu, Back stops and returns to the page exactly where you left it. System media keys work (also via a Media3 MediaSession).

## How progress works

The page calls `window.NorthernLightsNative.play(json)`; the app plays it natively and reports back with `window.NLNativeEvents.dispatch(json)`: `started`, `progress` (every 10 s while playing), `paused`, `resumed`, `seeked`, `stopped` (Back, with the position), `ended`, `error`, `audio` and `subtitle` (a track picked in the menu; the page then fetches a new stream and calls `play` again). The page turns these into the same `/api/nl/progress` calls the browser player makes, so "Continue Watching" works the same on the TV. Leaving the app with Home also sends `stopped`.

### Bridge contract (version 1)

`window.NorthernLightsNative` exists only on pages from the saved server origin:

- `play(json)`: `{url, kind ("file"|"hls"), startSeconds, title, subtitle, skipSeconds, audioTracks:[{index,label,language,isDefault}], subtitleTracks:[{index,label,language,url,kind}], audioIndex, subtitleIndex, nextUp: null|{label}}`. All URLs must be absolute and on the server origin; anything else is refused with an `error` event.
- `stop()`, `version()` (returns `"1"`).
- Events: `{type, positionSeconds, durationSeconds, message?, index?}`.
