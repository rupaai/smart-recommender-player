package app.northernlights.tv

import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.graphics.Bitmap
import android.graphics.Color
import android.os.Bundle
import android.os.SystemClock
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.media3.ui.PlayerView
import org.json.JSONObject

/**
 * Hosts the Northern Lights TV web UI in a WebView. Native playback (Media3, bridge v2) is drawn
 * by a PlayerView BEHIND the WebView; while it plays the WebView is transparent and keeps focus,
 * so the page draws all controls and receives every key.
 */
class MainActivity : AppCompatActivity(), NativeBridge.Host {
    private lateinit var webView: WebView
    private lateinit var errorPanel: View
    private lateinit var errorDetail: TextView
    private lateinit var retry: Button
    private lateinit var change: Button
    private lateinit var player: PlayerController

    private var serverOrigin: String? = null
    private var serverAddress: String? = null

    @Volatile
    private var pageTrusted = false
    private var errorShowing = false
    private var lastBackAt = 0L
    private var backLongHandled = false

    private val settingsLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
            val saved = ServerConfig.load(this)
            if (saved == null) {
                finish() // first run and the user left settings without saving
            } else if (saved != serverAddress) {
                loadServer()
            } else if (errorShowing) {
                reloadHome()
            }
        }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webview)
        errorPanel = findViewById(R.id.error_panel)
        errorDetail = findViewById(R.id.error_detail)
        retry = findViewById(R.id.btn_retry)
        change = findViewById(R.id.btn_change)

        retry.setOnClickListener { reloadHome() }
        change.setOnClickListener { openSettings() }

        player = PlayerController(
            activity = this,
            playerView = findViewById<PlayerView>(R.id.player_view),
            sendEvent = { dispatchToPage(it) },
            onOpened = {
                // The picture is behind the WebView: let it show through the transparent page.
                webView.setBackgroundColor(Color.TRANSPARENT)
                webView.requestFocus()
            },
            onClosed = {
                webView.setBackgroundColor(PAGE_BACKGROUND)
                webView.requestFocus()
            },
        )

        if (0 != (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE)) {
            WebView.setWebContentsDebuggingEnabled(true)
        }
        webView.setBackgroundColor(PAGE_BACKGROUND)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            cacheMode = WebSettings.LOAD_DEFAULT
            textZoom = 100 // the page scales itself to the screen; ignore the system font size
            userAgentString = (userAgentString ?: "") + " NorthernLightsTV/1"
        }
        webView.addJavascriptInterface(NativeBridge(this), "NorthernLightsNative")
        webView.webViewClient = NlWebViewClient()
        webView.isFocusable = true
        webView.isFocusableInTouchMode = true

        if (ServerConfig.load(this) == null) {
            openSettings()
        } else {
            loadServer()
        }
    }

    // ---- loading ---------------------------------------------------------------------

    private fun loadServer() {
        val address = ServerConfig.load(this) ?: return openSettings()
        serverAddress = address
        serverOrigin = ServerConfig.originOf(address)
        hideError()
        webView.loadUrl("$address/nl/tv/")
    }

    private fun reloadHome() {
        hideError()
        player.close(sendStopped = false)
        loadServer()
    }

    private fun showError(detail: String) {
        errorShowing = true
        pageTrusted = false
        player.close(sendStopped = false)
        errorDetail.text = detail
        errorPanel.visibility = View.VISIBLE
        webView.visibility = View.INVISIBLE
        retry.requestFocus()
    }

    private fun hideError() {
        errorShowing = false
        errorPanel.visibility = View.GONE
        webView.visibility = View.VISIBLE
        webView.requestFocus()
    }

    private fun openSettings() {
        settingsLauncher.launch(Intent(this, SettingsActivity::class.java))
    }

    private inner class NlWebViewClient : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            // Stay inside the app and inside the server's origin. Anything else is dropped.
            return ServerConfig.originOf(request.url.toString()) != serverOrigin
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
            pageTrusted = ServerConfig.originOf(url) == serverOrigin && serverOrigin != null
            player.close(sendStopped = false) // a new page load means any old playback is orphaned
        }

        override fun onPageFinished(view: WebView, url: String?) {
            pageTrusted = ServerConfig.originOf(url) == serverOrigin && serverOrigin != null
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame) {
                showError("${error.description} (${request.url.host ?: "server"})")
            }
        }

        override fun onReceivedHttpError(
            view: WebView,
            request: WebResourceRequest,
            errorResponse: WebResourceResponse,
        ) {
            if (request.isForMainFrame && errorResponse.statusCode >= 400) {
                showError("The server answered HTTP ${errorResponse.statusCode}.")
            }
        }
    }

    // ---- JS bridge -------------------------------------------------------------------

    override fun isTrusted(): Boolean = pageTrusted

    override fun onPlay(json: String) {
        runOnUiThread {
            val origin = serverOrigin
            if (!pageTrusted || origin == null || isFinishing) return@runOnUiThread
            // Re-check against the live URL too, in case the page navigated since the flag was set.
            if (ServerConfig.originOf(webView.url) != origin) return@runOnUiThread
            try {
                player.start(PlayRequest.parse(json, origin))
            } catch (e: Exception) {
                dispatchToPage(
                    JSONObject()
                        .put("type", "error")
                        .put("positionSeconds", 0)
                        .put("durationSeconds", 0)
                        .put("message", "Refused: ${e.message ?: "bad play request"}")
                )
            }
        }
    }

    override fun onPauseRequested() {
        runOnUiThread { if (pageTrusted) player.pause() }
    }

    override fun onResumeRequested() {
        runOnUiThread { if (pageTrusted) player.resume() }
    }

    override fun onSeekRequested(seconds: Double) {
        runOnUiThread { if (pageTrusted) player.seekTo(seconds) }
    }

    override fun onSubtitleRequested(index: Int) {
        runOnUiThread { if (pageTrusted) player.setSubtitle(index) }
    }

    override fun onSubtitleStyleRequested(json: String) {
        runOnUiThread { if (pageTrusted) player.setSubtitleStyle(json) }
    }

    override fun onSpeedRequested(rate: Double) {
        runOnUiThread { if (pageTrusted) player.setSpeed(rate) }
    }

    override fun onStop() {
        super.onStop()
        player.close(sendStopped = true) // user left the app: save the position
    }

    // The bridge's stop() lands here; named separately so it does not clash with Activity.onStop.
    override fun onStopRequested() {
        runOnUiThread {
            if (pageTrusted) player.close(sendStopped = true)
        }
    }

    private fun dispatchToPage(event: JSONObject) {
        if (!pageTrusted) return
        val js = "window.NLNativeEvents && window.NLNativeEvents.dispatch(" +
            JSONObject.quote(event.toString()) + ")"
        webView.evaluateJavascript(js, null)
    }

    // ---- keys ------------------------------------------------------------------------

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        // While a video plays every key goes to the WebView: the page draws the controls.
        if (!player.isActive && !errorShowing && event.keyCode == KeyEvent.KEYCODE_MENU) {
            if (event.action == KeyEvent.ACTION_UP) openSettings()
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            event.startTracking()
            backLongHandled = false
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onKeyLongPress(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (player.isActive) return true // Back belongs to the page while a video plays
            // Long Back on the home page (nothing to go back to) opens settings.
            if (!webView.canGoBack() || errorShowing) {
                backLongHandled = true
                openSettings()
            }
            return true
        }
        return super.onKeyLongPress(keyCode, event)
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (!event.isCanceled && !backLongHandled) handleBack()
            return true
        }
        return super.onKeyUp(keyCode, event)
    }

    private fun handleBack() {
        if (player.isActive) {
            // The page's own Back logic: close a menu, cancel the next card, else leave (which stops playback).
            webView.evaluateJavascript(
                "document.dispatchEvent(new KeyboardEvent('keydown'," +
                    "{key:'Escape',keyCode:27,which:27,bubbles:true,cancelable:true}))",
                null,
            )
            return
        }
        if (!errorShowing && webView.canGoBack()) {
            webView.goBack()
            return
        }
        val now = SystemClock.elapsedRealtime()
        if (now - lastBackAt < 2000) {
            finish()
        } else {
            lastBackAt = now
            Toast.makeText(this, R.string.press_back_again, Toast.LENGTH_SHORT).show()
        }
    }

    companion object {
        private val PAGE_BACKGROUND = 0xFF0B1020.toInt()
    }

    override fun onDestroy() {
        player.close(sendStopped = false, notifyClosed = false)
        webView.destroy()
        super.onDestroy()
    }
}
