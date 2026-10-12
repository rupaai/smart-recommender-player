package app.northernlights.tv

import android.webkit.JavascriptInterface

/**
 * window.NorthernLightsNative. Methods run on a WebView background thread; each one re-checks
 * that the page is on the saved server origin ([isTrusted]) and then hands over to the
 * main thread via [host].
 */
class NativeBridge(private val host: Host) {

    interface Host {
        /** True while the current page's origin equals the saved server origin. Thread-safe. */
        fun isTrusted(): Boolean
        fun onPlay(json: String)
        fun onStopRequested()
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
    fun version(): String = if (host.isTrusted()) "1" else ""
}
