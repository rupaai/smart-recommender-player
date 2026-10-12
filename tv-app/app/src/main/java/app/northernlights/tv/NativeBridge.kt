package app.northernlights.tv

import android.webkit.JavascriptInterface

/**
 * window.NorthernLightsNative (bridge v2). Methods run on a WebView background thread; each one re-checks
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

    @JavascriptInterface
    fun version(): String = if (host.isTrusted()) "2" else ""
}
