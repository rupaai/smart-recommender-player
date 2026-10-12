package app.northernlights.tv

import android.webkit.JavascriptInterface

/**
 * window.NorthernLightsNative (bridge v3 = v2 + setSubtitleStyle). Methods run on a WebView background thread; each one re-checks
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
     * raise: 0|1|2}. Safe to call before or during playback. There is deliberately no delay call:
     * Media3 1.5.1 has no subtitle offset, so the page hides its delay control on the TV.
     */
    @JavascriptInterface
    fun setSubtitleStyle(json: String?) {
        if (json == null || !host.isTrusted()) return
        host.onSubtitleStyleRequested(json)
    }

    @JavascriptInterface
    fun version(): String = if (host.isTrusted()) "3" else ""
}
