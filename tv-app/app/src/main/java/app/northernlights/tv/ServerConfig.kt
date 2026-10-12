package app.northernlights.tv

import android.content.Context
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL

/** Saved server address (S5). Only ever the user's own FastAPI server; no Jellyfin key lives here. */
object ServerConfig {
    private const val PREFS = "nl_tv"
    private const val KEY_SERVER = "server"
    const val DEFAULT_PREFILL = "http://192.168.1."
    const val DEFAULT_PORT = 8000

    fun load(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_SERVER, null)

    fun save(context: Context, address: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_SERVER, address).apply()
    }

    /**
     * Turns user input into "scheme://host:port" (no path), or null when invalid.
     * A missing port becomes 8000 (our default), a missing scheme becomes http.
     */
    fun normalize(input: String): String? {
        var text = input.trim().trimEnd('/')
        if (text.isEmpty()) return null
        if (!text.contains("://")) text = "http://$text"
        val uri = try {
            URI(text)
        } catch (e: Exception) {
            return null
        }
        val scheme = uri.scheme?.lowercase() ?: return null
        if (scheme != "http" && scheme != "https") return null
        val host = uri.host ?: return null
        if (host.isBlank()) return null
        val path = uri.rawPath ?: ""
        if (path.isNotEmpty() && path != "/") return null
        val port = if (uri.port == -1) DEFAULT_PORT else uri.port
        if (port !in 1..65535) return null
        val hostPart = if (host.contains(":")) "[$host]" else host
        return "$scheme://$hostPart:$port"
    }

    /** "scheme://host:port" with the default port made explicit, for origin comparison. */
    fun originOf(url: String?): String? {
        if (url.isNullOrBlank()) return null
        val uri = try {
            URI(url)
        } catch (e: Exception) {
            return null
        }
        val scheme = uri.scheme?.lowercase() ?: return null
        val host = uri.host?.lowercase() ?: return null
        val port = if (uri.port != -1) uri.port else if (scheme == "https") 443 else 80
        return "$scheme://$host:$port"
    }

    /** Blocking GET <address>/healthz. Returns null when OK, else a human-readable error. */
    fun testConnection(address: String): String? {
        return try {
            val conn = URL("$address/healthz").openConnection() as HttpURLConnection
            conn.connectTimeout = 5000
            conn.readTimeout = 5000
            conn.requestMethod = "GET"
            try {
                val code = conn.responseCode
                if (code in 200..299) null else "Server answered HTTP $code. Is this a Northern Lights server?"
            } finally {
                conn.disconnect()
            }
        } catch (e: java.net.UnknownHostException) {
            "Can't find that host. Check the address."
        } catch (e: java.net.SocketTimeoutException) {
            "Timed out. Is the server on and on the same network?"
        } catch (e: java.net.ConnectException) {
            "Connection refused. Check the address and port."
        } catch (e: Exception) {
            "Couldn't connect: ${e.message ?: e.javaClass.simpleName}"
        }
    }
}
