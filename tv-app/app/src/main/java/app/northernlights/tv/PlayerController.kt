package app.northernlights.tv

import android.app.Activity
import android.graphics.Color
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.view.View
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.Tracks
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.SeekParameters
import androidx.media3.session.MediaSession
import androidx.media3.ui.CaptionStyleCompat
import androidx.media3.ui.PlayerView
import androidx.media3.ui.SubtitleView
import org.json.JSONObject

/**
 * Headless native player (bridge v2): Media3 decodes and draws the picture (and text subtitles)
 * into [playerView], which sits BEHIND the transparent WebView. There is no native UI at all; the
 * page draws the controls and drives this class through the NorthernLightsNative bridge
 * (play / pause / resume / seekTo / setSubtitle). Reports back through [sendEvent] using the
 * NLNativeEvents contract: started, time (every 500 ms), progress (every 10 s), paused, resumed,
 * seeked, stopped, ended, error.
 */
class PlayerController(
    private val activity: Activity,
    private val playerView: PlayerView,
    private val sendEvent: (JSONObject) -> Unit,
    private val onOpened: () -> Unit,
    private val onClosed: () -> Unit,
) {
    private val handler = Handler(Looper.getMainLooper())
    private var player: ExoPlayer? = null
    private var session: MediaSession? = null
    private var startedSent = false
    private var pendingSubtitleIndex: Int? = null
    private var ticks = 0
    private var subtitleStyleJson: String? = null
    private var speed = 1.0 // bridge v4: kept across streams, a new ExoPlayer would start at 1x
    private var selectedSubtitleIndex = -1 // the side-loaded track Media3 has selected, -1 = none

    val isActive: Boolean get() = player != null

    private val timeTick = object : Runnable {
        override fun run() {
            val p = player ?: return
            emit("time")
            ticks += 1
            if (ticks % PROGRESS_EVERY_TICKS == 0 && p.isPlaying) emit("progress")
            handler.postDelayed(this, TIME_MS)
        }
    }

    private val seekedTick = Runnable { emit("seeked") }

    private val listener = object : Player.Listener {
        override fun onPlaybackStateChanged(state: Int) {
            if (player == null) return
            if (state == Player.STATE_READY && !startedSent) {
                startedSent = true
                emit("started")
            } else if (state == Player.STATE_ENDED) {
                emit("ended")
                handler.post { close(sendStopped = false) }
            }
        }

        override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
            if (!startedSent) return
            emit(if (playWhenReady) "resumed" else "paused")
        }

        override fun onPlayerError(error: PlaybackException) {
            val message = "${error.errorCodeName}: ${error.message ?: "playback failed"}"
            emit("error", message = message)
            handler.post { close(sendStopped = false) }
        }

        override fun onTracksChanged(tracks: Tracks) {
            val wanted = pendingSubtitleIndex ?: return
            if (wanted >= 0 && selectSubtitleLocally(wanted)) pendingSubtitleIndex = null
        }
    }

    fun start(req: PlayRequest) {
        close(sendStopped = false, notifyClosed = false)
        startedSent = false
        pendingSubtitleIndex = req.subtitleIndex
        selectedSubtitleIndex = -1
        playerView.subtitleView?.visibility = View.VISIBLE
        ticks = 0

        val exo = ExoPlayer.Builder(activity)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(C.USAGE_MEDIA)
                    .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE)
                    .build(),
                true,
            )
            .setHandleAudioBecomingNoisy(true)
            // Start (and restart after a seek) with 1 s buffered instead of Media3's 2.5 s / 5 s.
            .setLoadControl(
                DefaultLoadControl.Builder()
                    .setBufferDurationsMs(
                        DefaultLoadControl.DEFAULT_MIN_BUFFER_MS,
                        DefaultLoadControl.DEFAULT_MAX_BUFFER_MS,
                        START_BUFFER_MS,
                        REBUFFER_MS,
                    )
                    .build()
            )
            .build()
        exo.addListener(listener)
        if (speed != 1.0) exo.setPlaybackSpeed(speed.toFloat())
        // Land on the nearest keyframe: no decoding up to an exact frame, so seeks start sooner.
        exo.setSeekParameters(SeekParameters.CLOSEST_SYNC)
        if (req.subtitleIndex < 0) setSubtitlesOff(exo)

        val subConfigs = req.subtitleTracks
            .filter { it.url != null }
            .map { t ->
                MediaItem.SubtitleConfiguration.Builder(Uri.parse(t.url))
                    .setId(SUB_ID_PREFIX + t.index)
                    .setMimeType(MimeTypes.TEXT_VTT)
                    .setLanguage(t.language.ifEmpty { null })
                    .setLabel(t.label)
                    .build()
            }
        val item = MediaItem.Builder()
            .setUri(req.url)
            .apply { if (req.kind == "hls") setMimeType(MimeTypes.APPLICATION_M3U8) }
            .setSubtitleConfigurations(subConfigs)
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(req.title)
                    .setSubtitle(req.subtitle)
                    .build()
            )
            .build()
        exo.setMediaItem(item, (req.startSeconds * 1000).toLong())
        exo.playWhenReady = true

        playerView.player = exo
        subtitleStyleJson?.let { applySubtitleStyle(it) }
        session = MediaSession.Builder(activity, exo).setId("northern-lights-tv").build()
        player = exo
        playerView.visibility = View.VISIBLE
        playerView.keepScreenOn = true
        onOpened()
        exo.prepare()
        handler.removeCallbacks(timeTick)
        handler.postDelayed(timeTick, TIME_MS)
    }

    /** Called for page-initiated stop() and for Back. */
    fun close(sendStopped: Boolean, notifyClosed: Boolean = true) {
        val p = player ?: return
        if (sendStopped) emit("stopped")
        handler.removeCallbacks(timeTick)
        handler.removeCallbacks(seekedTick)
        player = null
        playerView.keepScreenOn = false
        playerView.player = null
        playerView.visibility = View.GONE
        session?.release()
        session = null
        p.removeListener(listener)
        p.release()
        if (notifyClosed) onClosed()
    }

    fun pause() {
        player?.pause()
    }

    fun resume() {
        player?.play()
    }

    /** Page-driven seek. A debounced "seeked" event follows, like the web player's. */
    fun seekTo(seconds: Double) {
        val p = player ?: return
        if (seconds.isNaN() || seconds.isInfinite()) return
        var target = (seconds * 1000).toLong()
        if (target < 0) target = 0
        val dur = p.duration
        if (dur != C.TIME_UNSET && dur > 0 && target > dur) target = dur
        p.seekTo(target)
        handler.removeCallbacks(seekedTick)
        handler.postDelayed(seekedTick, SEEKED_DEBOUNCE_MS)
    }

    /** Bridge v4: playback speed (0.75x .. 2x from the page's menu). Also applies to the streams that follow. */
    fun setSpeed(rate: Double) {
        if (rate.isNaN() || rate.isInfinite() || rate <= 0.0) return
        speed = rate
        player?.setPlaybackSpeed(rate.toFloat())
    }

    /**
     * Select a side-loaded text track by the page's track index; -1 = off.
     * A track selection change makes Media3 re-select every renderer's tracks, which can stutter the picture,
     * so "off" only hides the subtitle view (the track stays selected) and picking the same track again
     * just shows it. Only a different track changes the selection.
     */
    fun setSubtitle(index: Int) {
        if (player == null) return
        val view = playerView.subtitleView
        if (index < 0) {
            pendingSubtitleIndex = null
            view?.visibility = View.INVISIBLE
            return
        }
        view?.visibility = View.VISIBLE
        if (index == selectedSubtitleIndex) return
        if (!selectSubtitleLocally(index)) {
            pendingSubtitleIndex = index // tracks not loaded yet; onTracksChanged retries
        }
    }

    /**
     * Bridge v3: size, background and position of the subtitles Media3 draws. Remembered, so it also applies
     * to the next stream. Media3 1.5.1 has no subtitle delay, so there is none.
     */
    fun setSubtitleStyle(json: String) {
        subtitleStyleJson = json
        applySubtitleStyle(json)
    }

    private fun applySubtitleStyle(json: String) {
        val o = try {
            JSONObject(json)
        } catch (e: Exception) {
            return
        }
        val view = playerView.subtitleView ?: return
        val scale = when (o.optString("size", "M")) {
            "S" -> 0.8f
            "L" -> 1.5f
            "XL" -> 2.0f
            else -> 1.15f
        }
        val boxColor = Color.argb(184, 0, 0, 0)
        val background: Int
        val edge: Int
        when (o.optString("background", "box")) {
            "none" -> {
                background = Color.TRANSPARENT
                edge = CaptionStyleCompat.EDGE_TYPE_NONE
            }
            "shadow" -> {
                background = Color.TRANSPARENT
                edge = CaptionStyleCompat.EDGE_TYPE_DROP_SHADOW
            }
            else -> {
                background = boxColor
                edge = CaptionStyleCompat.EDGE_TYPE_NONE
            }
        }
        val raise = o.optInt("raise", 0).coerceIn(0, 2)
        view.setStyle(
            CaptionStyleCompat(Color.WHITE, background, Color.TRANSPARENT, edge, Color.BLACK, null)
        )
        view.setFractionalTextSize(SubtitleView.DEFAULT_TEXT_SIZE_FRACTION * scale)
        view.setBottomPaddingFraction(
            SubtitleView.DEFAULT_BOTTOM_PADDING_FRACTION + RAISE_STEP_FRACTION * raise
        )
    }

    private fun emit(type: String, message: String? = null) {
        val p = player
        val o = JSONObject()
        o.put("type", type)
        val pos = p?.currentPosition ?: 0L
        val dur = p?.duration ?: C.TIME_UNSET
        o.put("positionSeconds", pos / 1000.0)
        o.put("durationSeconds", if (dur == C.TIME_UNSET || dur < 0) 0.0 else dur / 1000.0)
        // Seconds buffered ahead of the playhead.
        val ahead = if (p == null) 0L else (p.bufferedPosition - p.currentPosition).coerceAtLeast(0L)
        o.put("bufferedSeconds", ahead / 1000.0)
        o.put("playing", p?.isPlaying ?: false)
        if (message != null) o.put("message", message)
        sendEvent(o)
    }

    // ---- local text-track switching ---------------------------------------------------

    private fun setSubtitlesOff(target: ExoPlayer? = player) {
        target ?: return
        target.trackSelectionParameters = target.trackSelectionParameters.buildUpon()
            .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true)
            .clearOverridesOfType(C.TRACK_TYPE_TEXT)
            .build()
    }

    /** Returns true when a matching side-loaded text track was found and selected. */
    private fun selectSubtitleLocally(index: Int): Boolean {
        val p = player ?: return false
        val wantedId = SUB_ID_PREFIX + index
        for (group in p.currentTracks.groups) {
            if (group.type != C.TRACK_TYPE_TEXT) continue
            val g = group.mediaTrackGroup
            // Media3 prefixes side-loaded track ids with the source number ("1:nlsub3").
            val id = if (g.length > 0) g.getFormat(0).id else null
            if (id == wantedId || id?.endsWith(":$wantedId") == true) {
                p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
                    .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
                    .setOverrideForType(TrackSelectionOverride(g, 0))
                    .build()
                selectedSubtitleIndex = index
                return true
            }
        }
        return false
    }

    companion object {
        private const val TIME_MS = 500L
        private const val PROGRESS_EVERY_TICKS = 20 // 20 x 500 ms = 10 s
        private const val SEEKED_DEBOUNCE_MS = 700L
        private const val SUB_ID_PREFIX = "nlsub"
        private const val RAISE_STEP_FRACTION = 0.07f // each "raise" step lifts the lines by 7% of the screen height
        private const val START_BUFFER_MS = 1_000
        private const val REBUFFER_MS = 2_000
    }
}
