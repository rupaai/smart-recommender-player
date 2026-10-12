package app.northernlights.tv

import android.app.Activity
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.ListView
import android.widget.TextView
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.Tracks
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.SeekParameters
import androidx.media3.session.MediaSession
import androidx.media3.ui.PlayerView
import org.json.JSONObject

/**
 * Full-screen native player drawn as an overlay inside MainActivity, so the WebView (and the
 * page state under it) is never destroyed. Reports back through [sendEvent] using the
 * NLNativeEvents contract: started, progress (every 10 s), paused, resumed, seeked, stopped,
 * ended, error, audio, subtitle.
 */
class PlayerController(
    private val activity: Activity,
    private val container: View,
    private val playerView: PlayerView,
    private val titleBox: View,
    private val titleText: TextView,
    private val subtitleText: TextView,
    private val trackMenu: View,
    private val audioList: ListView,
    private val subtitleList: ListView,
    private val sendEvent: (JSONObject) -> Unit,
    private val onClosed: () -> Unit,
) {
    private val handler = Handler(Looper.getMainLooper())
    private var player: ExoPlayer? = null
    private var session: MediaSession? = null
    private var request: PlayRequest? = null
    private var startedSent = false
    private var pendingSubtitleIndex: Int? = null
    private var menuOpen = false

    val isActive: Boolean get() = player != null

    private val progressTick = object : Runnable {
        override fun run() {
            val p = player ?: return
            if (p.isPlaying) emit("progress")
            handler.postDelayed(this, PROGRESS_MS)
        }
    }

    private val seekedTick = Runnable { emit("seeked") }

    private val listener = object : Player.Listener {
        override fun onPlaybackStateChanged(state: Int) {
            val p = player ?: return
            if (state == Player.STATE_READY && !startedSent) {
                startedSent = true
                emit("started")
            } else if (state == Player.STATE_ENDED) {
                emit("ended")
                handler.post { close(sendStopped = false) }
            }
            if (p.playbackState == Player.STATE_BUFFERING) playerView.showController()
        }

        override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
            if (!startedSent) return
            emit(if (playWhenReady) "resumed" else "paused")
            if (!playWhenReady) playerView.showController()
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

    init {
        audioList.setOnItemClickListener { _, _, position, _ ->
            val req = request ?: return@setOnItemClickListener
            val track = req.audioTracks.getOrNull(position) ?: return@setOnItemClickListener
            audioList.setItemChecked(position, true)
            closeMenu()
            emit("audio", index = track.index)
        }
        subtitleList.setOnItemClickListener { _, _, position, _ ->
            val req = request ?: return@setOnItemClickListener
            // Position 0 is "Off".
            val index = if (position == 0) -1 else req.subtitleTracks.getOrNull(position - 1)?.index
                ?: return@setOnItemClickListener
            subtitleList.setItemChecked(position, true)
            closeMenu()
            if (index < 0) setSubtitlesOff() else selectSubtitleLocally(index)
            emit("subtitle", index = index)
        }
    }

    fun start(req: PlayRequest) {
        close(sendStopped = false, notifyClosed = false)
        request = req
        startedSent = false
        pendingSubtitleIndex = req.subtitleIndex

        val exo = ExoPlayer.Builder(activity)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(C.USAGE_MEDIA)
                    .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE)
                    .build(),
                true,
            )
            .setHandleAudioBecomingNoisy(true)
            // The on-screen rewind / forward buttons use the same step as Left / Right.
            .setSeekBackIncrementMs(req.skipSeconds * 1000L)
            .setSeekForwardIncrementMs(req.skipSeconds * 1000L)
            .build()
        exo.addListener(listener)
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
        playerView.controllerShowTimeoutMs = 4000
        playerView.setControllerVisibilityListener(
            PlayerView.ControllerVisibilityListener { visibility ->
                titleBox.visibility = if (visibility == View.VISIBLE) View.VISIBLE else View.GONE
            }
        )
        titleText.text = req.title
        subtitleText.text = req.subtitle
        subtitleText.visibility = if (req.subtitle.isEmpty()) View.GONE else View.VISIBLE

        session = MediaSession.Builder(activity, exo).setId("northern-lights-tv").build()
        player = exo
        buildMenus(req)
        closeMenu()
        container.visibility = View.VISIBLE
        container.requestFocus()
        playerView.keepScreenOn = true
        exo.prepare()
        playerView.showController()
        handler.removeCallbacks(progressTick)
        handler.postDelayed(progressTick, PROGRESS_MS)
    }

    /** Called for page-initiated stop() and for Back. */
    fun close(sendStopped: Boolean, notifyClosed: Boolean = true) {
        val p = player ?: return
        if (sendStopped) emit("stopped")
        handler.removeCallbacks(progressTick)
        handler.removeCallbacks(seekedTick)
        player = null
        playerView.keepScreenOn = false
        playerView.player = null
        session?.release()
        session = null
        p.removeListener(listener)
        p.release()
        request = null
        menuOpen = false
        trackMenu.visibility = View.GONE
        container.visibility = View.GONE
        if (notifyClosed) onClosed()
    }

    fun pause() {
        player?.pause()
    }

    private fun emit(type: String, message: String? = null, index: Int? = null) {
        val p = player
        val o = JSONObject()
        o.put("type", type)
        val pos = p?.currentPosition ?: 0L
        val dur = p?.duration ?: C.TIME_UNSET
        o.put("positionSeconds", pos / 1000.0)
        o.put("durationSeconds", if (dur == C.TIME_UNSET || dur < 0) 0.0 else dur / 1000.0)
        if (message != null) o.put("message", message)
        if (index != null) o.put("index", index)
        sendEvent(o)
    }

    // ---- track menu -------------------------------------------------------------------

    private fun buildMenus(req: PlayRequest) {
        val audioLabels = req.audioTracks.map { it.label }
        audioList.adapter = menuAdapter(audioLabels)
        val checkedAudio = req.audioTracks.indexOfFirst { it.index == req.audioIndex }
            .let { if (it >= 0) it else req.audioTracks.indexOfFirst { t -> t.isDefault } }
        audioList.choiceMode = ListView.CHOICE_MODE_SINGLE
        if (checkedAudio >= 0) audioList.setItemChecked(checkedAudio, true)

        val subLabels = listOf(activity.getString(R.string.subtitles_off)) + req.subtitleTracks.map { it.label }
        subtitleList.adapter = menuAdapter(subLabels)
        subtitleList.choiceMode = ListView.CHOICE_MODE_SINGLE
        val checkedSub = req.subtitleTracks.indexOfFirst { it.index == req.subtitleIndex }
        subtitleList.setItemChecked(if (checkedSub >= 0) checkedSub + 1 else 0, true)
    }

    private fun menuAdapter(labels: List<String>): ArrayAdapter<String> =
        object : ArrayAdapter<String>(activity, android.R.layout.simple_list_item_single_choice, labels) {
            override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
                val v = super.getView(position, convertView, parent)
                (v as? TextView)?.apply {
                    textSize = 22f
                    setTextColor(0xFFFFFFFF.toInt())
                    gravity = Gravity.CENTER_VERTICAL
                    setPadding(8, 20, 8, 20)
                }
                return v
            }
        }

    private fun openMenu() {
        val req = request ?: return
        if (req.audioTracks.isEmpty() && req.subtitleTracks.isEmpty()) return
        menuOpen = true
        trackMenu.visibility = View.VISIBLE
        playerView.hideController()
        (if (req.audioTracks.isNotEmpty()) audioList else subtitleList).requestFocus()
    }

    private fun closeMenu() {
        menuOpen = false
        trackMenu.visibility = View.GONE
        container.requestFocus()
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
            if (g.length > 0 && g.getFormat(0).id == wantedId) {
                p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
                    .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
                    .setOverrideForType(TrackSelectionOverride(g, 0))
                    .build()
                return true
            }
        }
        return false
    }

    // ---- remote keys ------------------------------------------------------------------

    /** Returns true when the key was consumed. Called by MainActivity before normal dispatch. */
    fun onKey(event: KeyEvent): Boolean {
        val p = player ?: return false
        val down = event.action == KeyEvent.ACTION_DOWN
        val up = event.action == KeyEvent.ACTION_UP

        if (event.keyCode == KeyEvent.KEYCODE_BACK) {
            if (up) {
                if (menuOpen) closeMenu() else close(sendStopped = true)
            }
            return true
        }
        if (menuOpen) return false // let the lists handle D-pad / Enter

        val skip = request?.skipSeconds ?: 10
        when (event.keyCode) {
            KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_MEDIA_REWIND -> {
                if (down) seekBy(p, -skip, event.repeatCount)
                return true
            }
            KeyEvent.KEYCODE_DPAD_RIGHT, KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> {
                if (down) seekBy(p, skip, event.repeatCount)
                return true
            }
            KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_NUMPAD_ENTER,
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> {
                if (down && event.repeatCount == 0) {
                    togglePlay(p)
                    playerView.showController()
                }
                return true
            }
            KeyEvent.KEYCODE_MEDIA_PLAY -> {
                if (down) p.play()
                return true
            }
            KeyEvent.KEYCODE_MEDIA_PAUSE -> {
                if (down) p.pause()
                return true
            }
            KeyEvent.KEYCODE_MEDIA_STOP -> {
                if (up) close(sendStopped = true)
                return true
            }
            KeyEvent.KEYCODE_DPAD_UP -> {
                if (down && event.repeatCount == 0) openMenu()
                return true
            }
            KeyEvent.KEYCODE_DPAD_DOWN -> {
                if (down) playerView.showController()
                return true
            }
        }
        return false
    }

    private fun togglePlay(p: ExoPlayer) {
        if (p.playbackState == Player.STATE_ENDED) {
            p.seekTo(0)
            p.play()
        } else if (p.playWhenReady) {
            p.pause()
        } else {
            p.play()
        }
    }

    /** Holding the key repeats; the step grows from 1x up to 6x the chosen skip length. */
    private fun seekBy(p: ExoPlayer, seconds: Int, repeatCount: Int) {
        val multiplier = 1 + (repeatCount / 4).coerceAtMost(5)
        val dur = p.duration
        var target = p.currentPosition + seconds.toLong() * multiplier * 1000L
        if (target < 0) target = 0
        if (dur != C.TIME_UNSET && dur > 0 && target > dur) target = dur
        p.seekTo(target)
        playerView.showController()
        handler.removeCallbacks(seekedTick)
        handler.postDelayed(seekedTick, SEEKED_DEBOUNCE_MS)
    }

    companion object {
        private const val PROGRESS_MS = 10_000L
        private const val SEEKED_DEBOUNCE_MS = 700L
        private const val SUB_ID_PREFIX = "nlsub"
    }
}
