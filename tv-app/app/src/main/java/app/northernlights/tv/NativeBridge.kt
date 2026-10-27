package app.northernlights.tv

import android.webkit.JavascriptInterface

/**
 * window.NorthernLightsNative (bridge v7 = v6 + setSubtitleDelay; v6 = v5 + stats() and embedded subtitle tracks (kind "embedded" + order in play()'s subtitleTracks, chosen with setSubtitle); v5 = v4 + setAudioTrack; v4 = v3 + setSpeed; v3 = v2 + setSubtitleStyle). Methods run on a WebView background thread; each one re-checks
 * that the page is on the saved server origin ([isTrusted]) and then hands over to the
 * main thread via [host].
 */
class NativeBridge(private val host: Host) {

    interface Host {
        /** True while the current page's origin equals the saved server origin. Thread-safe. */
        fun isTrusted(): Boolean
        fun onPlay(json: String)
        fun onStopRequested()
        fun onPauseRequested()
        fun onResumeRequested()
        fun onSeekRequested(seconds: Double)
        fun onSubtitleRequested(index: Int)
        fun onSubtitleStyleRequested(json: String)
        fun onSubtitleDelayRequested(seconds: Double)
        fun onSpeedRequested(rate: Double)
        fun onAudioTrackRequested(order: Int)
        /** Bridge v6: the info panel's numbers as JSON. May be called from any thread. */
        fun statsJson(): String
        /** Opens the app's own settings screen (server address, Check for updates). Ignored during playback. */
        fun onOpenSettingsRequested()
    }

    /** The TV me page shows "TV app: server address and updates" when this exists. */
    @JavascriptInterface
    fun openSettings() {
        if (!host.isTrusted()) return
        host.onOpenSettingsRequested()
    }

    @JavascriptInterface
    fun play(json: String?) {
        if (json == null || !host.isTrusted()) return
        host.onPlay(json)
    }

    @JavascriptInterface
    fun stop() {
        if (!host.isTrusted()) return
        host.onStopRequested()
    }

    @JavascriptInterface
    fun pause() {
        if (!host.isTrusted()) return
        host.onPauseRequested()
    }

    @JavascriptInterface
    fun resume() {
        if (!host.isTrusted()) return
        host.onResumeRequested()
    }

    /** [seconds] arrives as a string ("123.4") so a JS number or string both work. */
    @JavascriptInterface
    fun seekTo(seconds: String?) {
        if (!host.isTrusted()) return
        val value = seconds?.toDoubleOrNull() ?: return
        host.onSeekRequested(value)
    }

    /** Local side-loaded text track by the page's track index; -1 = off. */
    @JavascriptInterface
    fun setSubtitle(index: Int) {
        if (!host.isTrusted()) return
        host.onSubtitleRequested(index)
    }

    /**
     * Bridge v3: how the subtitles Media3 draws look. [json] is {size: S|M|L|XL, background: none|shadow|box,
     * raise: 0|1|2}. Safe to call before or during playback. Picture subtitles follow size, box and position too (v7).
     */
    @JavascriptInterface
    fun setSubtitleStyle(json: String?) {
        if (json == null || !host.isTrusted()) return
        host.onSubtitleStyleRequested(json)
    }

    /**
     * Bridge v7: subtitle delay in seconds ("-1.25"; positive = later), for the playing stream only: play() sets it
     * back to 0, so the page sends it again after play(). Unparsable values are ignored; the player clamps to +-60 s.
     */
    @JavascriptInterface
    fun setSubtitleDelay(seconds: String?) {
        if (!host.isTrusted()) return
        val value = seconds?.toDoubleOrNull() ?: return
        if (value.isNaN() || value.isInfinite()) return
        host.onSubtitleDelayRequested(value)
    }

    /**
     * Bridge v4: playback speed ("0.75" .. "2"), as a string so a JS number or string both work. Out-of-range or
     * unparsable values are ignored; the player keeps the speed for the next stream too.
     */
    @JavascriptInterface
    fun setSpeed(rate: String?) {
        if (!host.isTrusted()) return
        val value = rate?.toDoubleOrNull() ?: return
        if (value.isNaN() || value.isInfinite() || value < MIN_SPEED || value > MAX_SPEED) return
        host.onSpeedRequested(value)
    }

    /**
     * Bridge v5: select the [order]-th audio track of the playing file (0-based, file order) without reloading.
     * The first selection comes with play() as audioOrder.
     */
    @JavascriptInterface
    fun setAudioTrack(order: String?) {
        if (!host.isTrusted()) return
        val value = order?.toIntOrNull() ?: return
        if (value < 0 || value > MAX_AUDIO_ORDER) return
        host.onAudioTrackRequested(value)
    }

    /**
     * Bridge v6: numbers for the playback info panel, as JSON: {source, videoDecoder, width, height, hdr, droppedFrames,
     * audioLabel, audioPath ("passthrough"|"decoded"|""), audioChannels, bandwidthMbps, bufferedAheadSeconds,
     * lastSeekMs (-1 = none yet), memoryMb}. "{}" when the page is not trusted.
     */
    @JavascriptInterface
    fun stats(): String = if (host.isTrusted()) host.statsJson() else "{}"

    @JavascriptInterface
    fun version(): String = if (host.isTrusted()) "7" else ""

    companion object {
        private const val MIN_SPEED = 0.25
        private const val MAX_SPEED = 4.0
        private const val MAX_AUDIO_ORDER = 63
    }
}
