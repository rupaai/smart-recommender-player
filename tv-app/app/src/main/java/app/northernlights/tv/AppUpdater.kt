package app.northernlights.tv

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import kotlin.concurrent.thread
import java.net.HttpURLConnection
import java.net.URL

/**
 * Self-update: reads version.json from the `tv-app` GitHub release, downloads the APK straight into
 * a PackageInstaller session and lets Android show its own "Update this app?" screen. Android never
 * lets a normal app update itself silently, so the viewer always presses OK once on the remote.
 */
object AppUpdater {
    private const val PREFS = "nl_tv"
    private const val KEY_LATER_BUILD = "update_later_build"

    // Set while the viewer is in Android's "install unknown apps" screen, so Install carries on when they come back.
    @Volatile
    private var installWaitingForPermission = false

    /** True from Install until Android reports a result, so the automatic prompt does not open on top. */
    @Volatile
    var installing = false

    fun installedBuild(context: Context): Long {
        val info = context.packageManager.getPackageInfo(context.packageName, 0)
        @Suppress("DEPRECATION")
        return if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else info.versionCode.toLong()
    }

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** False when the viewer already pressed Later for this build (Settings > Check for updates still offers it). */
    fun shouldOfferAutomatically(context: Context, build: Long): Boolean =
        UpdateCheck.shouldOffer(build, prefs(context).getLong(KEY_LATER_BUILD, 0L))

    /** Blocking. The newer build number on GitHub, or null when there is none or GitHub can't be read. */
    fun findNewerBuild(context: Context): Long? {
        val body = try {
            val conn = open(UpdateCheck.VERSION_URL)
            try {
                if (conn.responseCode !in 200..299) return null
                conn.inputStream.bufferedReader().use { it.readText() }
            } finally {
                conn.disconnect()
            }
        } catch (e: Exception) {
            return null
        }
        return UpdateCheck.newerBuild(UpdateCheck.versionCodeFrom(body), installedBuild(context))
    }

    /**
     * Android 8+ asks once that Northern Lights may install apps. Returns false (and opens that
     * setting) when it is still off; the install carries on in [resumeInstallIfAllowed] when they come back.
     */
    private fun ensureInstallAllowed(activity: Activity): Boolean {
        if (installAllowed(activity)) return true
        installWaitingForPermission = true
        Toast.makeText(activity, R.string.update_allow_hint, Toast.LENGTH_LONG).show()
        try {
            activity.startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${activity.packageName}"))
            )
        } catch (e: Exception) {
            // Some TVs have no per-app screen; the hint toast names the setting to look for.
        }
        return false
    }

    private fun installAllowed(context: Context): Boolean =
        Build.VERSION.SDK_INT < 26 || context.packageManager.canRequestPackageInstalls()

    /** Call from onResume: finishes an Install that had to wait for the permission. */
    fun resumeInstallIfAllowed(activity: Activity) {
        if (!installWaitingForPermission || !installAllowed(activity)) return
        installWaitingForPermission = false
        startInstall(activity)
    }

    /** Blocking (call off the main thread). Downloads the APK into an install session and commits it. */
    fun downloadAndInstall(context: Context) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        params.setAppPackageName(context.packageName)
        val sessionId = installer.createSession(params)
        val session = installer.openSession(sessionId)
        try {
            val conn = open(UpdateCheck.APK_URL)
            try {
                if (conn.responseCode !in 200..299) throw IllegalStateException("GitHub answered HTTP ${conn.responseCode}")
                val length = conn.contentLengthLong
                conn.inputStream.use { input ->
                    session.openWrite("northern-lights-tv.apk", 0, if (length > 0) length else -1).use { out ->
                        input.copyTo(out, 64 * 1024)
                        session.fsync(out)
                    }
                }
            } finally {
                conn.disconnect()
            }
            var flags = PendingIntent.FLAG_UPDATE_CURRENT
            // Android fills in the result extras, so the intent must be mutable on 12+.
            if (Build.VERSION.SDK_INT >= 31) flags = flags or PendingIntent.FLAG_MUTABLE
            val result = PendingIntent.getBroadcast(
                context,
                sessionId,
                Intent(context, UpdateResultReceiver::class.java),
                flags,
            )
            session.commit(result.intentSender)
            session.close()
        } catch (e: Exception) {
            session.abandon()
            throw e
        }
    }

    /** "Update available" dialog. Install starts the download; Later stops the automatic offer for this build. */
    fun offer(activity: Activity, build: Long, onClosed: () -> Unit = {}) {
        if (activity.isFinishing || activity.isDestroyed) return onClosed()
        AlertDialog.Builder(activity)
            .setTitle(R.string.update_title)
            .setMessage(activity.getString(R.string.update_message, "1.0.$build"))
            .setPositiveButton(R.string.update_install) { _, _ -> startInstall(activity) }
            .setNegativeButton(R.string.update_later) { _, _ ->
                prefs(activity).edit().putLong(KEY_LATER_BUILD, build).apply()
            }
            .setOnDismissListener { onClosed() }
            .show()
            .getButton(AlertDialog.BUTTON_POSITIVE)
            ?.requestFocus()
    }

    /** Checks the install permission, then downloads in the background; Android's own screen follows. */
    fun startInstall(activity: Activity) {
        if (!ensureInstallAllowed(activity)) return
        val app = activity.applicationContext
        installing = true
        Toast.makeText(activity, R.string.update_downloading, Toast.LENGTH_LONG).show()
        thread(name = "nl-update") {
            try {
                downloadAndInstall(app)
            } catch (e: Exception) {
                installing = false
                val why = e.message ?: e.javaClass.simpleName
                activity.runOnUiThread {
                    Toast.makeText(app, app.getString(R.string.update_failed, why), Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    private fun open(url: String): HttpURLConnection {
        // GitHub answers release downloads with a redirect to its file host; both are https.
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.instanceFollowRedirects = true
        conn.connectTimeout = 10000
        conn.readTimeout = 30000
        conn.setRequestProperty("User-Agent", "NorthernLightsTV")
        return conn
    }
}

/** Gets the install result. "Needs the viewer" opens Android's confirm screen; a failure shows why. */
class UpdateResultReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(confirm)
            }
            PackageInstaller.STATUS_SUCCESS -> Unit // Android restarts nothing; the viewer opens the app again.
            PackageInstaller.STATUS_FAILURE_ABORTED -> AppUpdater.installing = false // the viewer pressed Cancel
            else -> {
                AppUpdater.installing = false
                val why = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "unknown error"
                Toast.makeText(context, context.getString(R.string.update_failed, why), Toast.LENGTH_LONG).show()
            }
        }
    }
}
