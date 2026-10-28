package app.northernlights.tv

import android.app.Activity
import android.app.ActivityManager
import android.content.Context
import android.graphics.Color
import android.net.Uri
import android.system.Os
import android.system.OsConstants
import android.os.SystemClock
import android.os.Handler
import android.os.Looper
import android.view.View
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.Tracks
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.util.Util
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.SeekParameters
import androidx.media3.exoplayer.analytics.AnalyticsListener
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.session.MediaSession
import androidx.media3.ui.CaptionStyleCompat
import androidx.media3.ui.PlayerView
import androidx.media3.ui.SubtitleView
import org.json.JSONObject
import java.io.File

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
    private val subtitleLook = SubtitleLook() // bridge v7: delay, and the style Media3's view can't apply
    private var speed = 1.0 // bridge v4: kept across streams, a new ExoPlayer would start at 1x
    private var selectedSubtitleIndex = -1 // the side-loaded track Media3 has selected, -1 = none
    private var audioOrder = -1 // bridge v5: wanted audio track among the file's audio groups, -1 = Media3's choice
    private var appliedAudioOrder = -1
    private var frameRateHandled = false
    private var previousDisplayModeId: Int? = null // set while we have asked the display for another mode
    private var subtitleTracks: List<SubtitleTrackInfo> = emptyList() // bridge v6: the page's list, to tell embedded from side-loaded
    private val seekTimer = SeekTimer()
    @Volatile private var stats = PlaybackStats()
    private var droppedFrames = 0L
    private var audioChannels = 0
    private var audioPath = ""
    private var bandwidthMbps = 0.0
    private var videoDecoder = ""
    private var statsSource = "original"

    val isActive: Boolean get() = player != null

    private val timeTick = object : Runnable {
        override fun run() {
            val p = player ?: return
            refreshStats()
            emit("time")
            ticks += 1
            if (ticks % PROGRESS_EVERY_TICKS == 0 && p.isPlaying) emit("progress")
            handler.postDelayed(this, TIME_MS)
        }
    }

    private var pendingSeekTargetMs: Long? = null
    private var pendingSeekPositionMs: Long? = null
    private val seekCompletionCheck = object : Runnable {
        override fun run() {
            val p = player ?: return
            if (pendingSeekTargetMs == null) return
            val actualPosition = pendingSeekPositionMs
            if (actualPosition == null) {
                handler.postDelayed(this, SEEK_CHECK_MS)
                return
            }
            val ready = p.playbackState == Player.STATE_READY || p.playbackState == Player.STATE_ENDED
            val playbackCanAdvance = p.isPlaying && p.currentPosition >= actualPosition + SEEK_PROGRESS_MS
            val playbackCannotAdvance = !p.playWhenReady || p.playbackSuppressionReason != Player.PLAYBACK_SUPPRESSION_REASON_NONE
            if (ready && (playbackCanAdvance || playbackCannotAdvance || p.playbackState == Player.STATE_ENDED)) {
                pendingSeekTargetMs = null
                pendingSeekPositionMs = null
                seekTimer.ready(SystemClock.elapsedRealtime()) // a jump inside the buffer may never leave STATE_READY
                emit("seeked")
            } else {
                handler.postDelayed(this, SEEK_CHECK_MS)
            }
        }
    }

    /** Numbers for the info panel (bridge v6 stats()). All callbacks arrive on the main thread. */
    private val analytics = object : AnalyticsListener {
        override fun onVideoDecoderInitialized(
            eventTime: AnalyticsListener.EventTime,
            decoderName: String,
            initializedTimestampMs: Long,
            initializationDurationMs: Long,
        ) {
            videoDecoder = decoderName
        }

        override fun onDroppedVideoFrames(eventTime: AnalyticsListener.EventTime, droppedFrames: Int, elapsedMs: Long) {
            this@PlayerController.droppedFrames += droppedFrames
        }

        override fun onAudioTrackInitialized(eventTime: AnalyticsListener.EventTime, audioTrackConfig: AudioSink.AudioTrackConfig) {
            val pcm = isLinearPcm(audioTrackConfig.encoding)
            audioPath = if (pcm) "decoded" else "passthrough"
            audioChannels = if (pcm) channelCountOf(audioTrackConfig.channelConfig) else 0
        }

        override fun onBandwidthEstimate(
            eventTime: AnalyticsListener.EventTime,
            totalLoadTimeMs: Int,
            totalBytesLoaded: Long,
            bitrateEstimate: Long,
        ) {
            bandwidthMbps = bitrateEstimate / 1_000_000.0
        }
    }

    private fun isLinearPcm(encoding: Int): Boolean = when (encoding) {
        C.ENCODING_PCM_8BIT, C.ENCODING_PCM_16BIT, C.ENCODING_PCM_16BIT_BIG_ENDIAN, C.ENCODING_PCM_24BIT,
        C.ENCODING_PCM_32BIT, C.ENCODING_PCM_FLOAT -> true
        else -> false
    }

    private fun refreshStats() {
        val p = player ?: return
        var width = 0
        var height = 0
        var hdr = ""
        var audioLabel = ""
        val audioGroups = p.currentTracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }
        val audioInFile = fileOrder(audioGroups.map { g -> if (g.length > 0) g.getTrackFormat(0).id else null })
        for (group in p.currentTracks.groups) {
            for (i in 0 until group.length) {
                if (!group.isTrackSelected(i)) continue
                val f = group.getTrackFormat(i)
                if (group.type == C.TRACK_TYPE_VIDEO) {
                    width = f.width
                    height = f.height
                    hdr = hdrLabel(f.codecs, f.colorInfo?.colorTransfer ?: Format.NO_VALUE)
                } else if (group.type == C.TRACK_TYPE_AUDIO) {
                    audioLabel = audioTrackLabel(f, audioInFile.indexOf(audioGroups.indexOf(group)))
                }
            }
        }
        stats = PlaybackStats(
            source = statsSource,
            videoDecoder = videoDecoder,
            width = width,
            height = height,
            hdr = hdr,
            droppedFrames = droppedFrames,
            audioLabel = audioLabel,
            audioPath = audioPath,
            audioChannels = audioChannels,
            bandwidthMbps = bandwidthMbps,
            bufferedAheadSeconds = p.totalBufferedDuration.coerceAtLeast(0L) / 1000.0,
            lastSeekMs = seekTimer.lastMs,
        )
    }

    /**
     * The info panel's audio line: the page's label of the track that is really playing ([place] is its place in the
     * file), plus the language and track number Media3 reports, so a wrong pick shows on the TV.
     */
    private fun audioTrackLabel(f: Format, place: Int): String {
        val fromPage = playingAudioLabels.getOrNull(place)
        val playing = listOfNotNull(f.language, f.id?.let { "track ${it.substringAfterLast(':')}" }).joinToString(", ")
        val name = fromPage ?: f.label ?: ""
        return if (playing.isEmpty()) name else if (name.isEmpty()) playing else "$name ($playing)"
    }
    private var playingAudioLabels: List<String> = emptyList()

    /** Bridge v6 stats(): the last snapshot (refreshed every 500 ms) plus the app's resident memory, read now (cheap). */
    fun statsJson(): String = stats.toJson(residentMbFromStatm(readStatm(), Os.sysconf(OsConstants._SC_PAGESIZE)))

    private fun availableMemoryBytes(): Long {
        val am = activity.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager ?: return 0L
        return ActivityManager.MemoryInfo().also { am.getMemoryInfo(it) }.availMem
    }

    private fun readStatm(): String? = try {
        File("/proc/self/statm").readText()
    } catch (e: Exception) {
        null
    }

    private val listener = object : Player.Listener {
        override fun onPositionDiscontinuity(
            oldPosition: Player.PositionInfo,
            newPosition: Player.PositionInfo,
            reason: Int,
        ) {
            if (reason == Player.DISCONTINUITY_REASON_SEEK && pendingSeekTargetMs != null) {
                pendingSeekPositionMs = newPosition.positionMs
                checkSeekCompletion()
            }
        }

        override fun onPlaybackStateChanged(state: Int) {
            if (player == null) return
            if (state == Player.STATE_BUFFERING && pendingSeekTargetMs != null) emit("time")
            if (state == Player.STATE_READY) seekTimer.ready(SystemClock.elapsedRealtime())
            checkSeekCompletion()
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
            checkSeekCompletion()
            emit(if (playWhenReady) "resumed" else "paused")
        }

        override fun onIsPlayingChanged(isPlaying: Boolean) {
            checkSeekCompletion()
        }

        override fun onPlayerError(error: PlaybackException) {
            clearPendingSeek()
            val message = "${error.errorCodeName}: ${error.message ?: "playback failed"}"
            emit("error", message = message)
            handler.post { close(sendStopped = false) }
        }

        override fun onTracksChanged(tracks: Tracks) {
            applyAudioOrder()
            matchFrameRate(tracks)
            val wanted = pendingSubtitleIndex ?: return
            if (wanted >= 0 && selectSubtitleLocally(wanted)) pendingSubtitleIndex = null
        }
    }

    fun start(req: PlayRequest) {
        close(sendStopped = false, notifyClosed = false)
        startedSent = false
        pendingSubtitleIndex = req.subtitleIndex
        audioOrder = if (req.kind == "file") req.audioOrder else -1
        appliedAudioOrder = -1
        frameRateHandled = false
        selectedSubtitleIndex = -1
        subtitleTracks = req.subtitleTracks
        subtitleLook.reset() // a delay belongs to one file; the page sends it again after play()
        playingAudioLabels = if (req.kind == "file") req.audioTracks.map { it.label } else emptyList()
        statsSource = if (req.kind == "file") "original" else "converted"
        droppedFrames = 0
        videoDecoder = ""
        audioPath = ""
        audioChannels = 0
        seekTimer.reset()
        stats = PlaybackStats(source = statsSource)
        playerView.subtitleView?.visibility = View.VISIBLE
        ticks = 0
        clearPendingSeek()

        val mediaSourceFactory = DefaultMediaSourceFactory(activity)
            .setDataSourceFactory(
                if (req.kind == "file") PlaybackCache.directDataSourceFactory(activity) else PlaybackCache.dataSourceFactory(activity)
            )
        val plan = bufferPlan(Runtime.getRuntime().maxMemory(), availableMemoryBytes())
        val exo = ExoPlayer.Builder(activity, NlRenderersFactory(activity, subtitleLook))
            .setMediaSourceFactory(mediaSourceFactory)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(C.USAGE_MEDIA)
                    .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE)
                    .build(),
                true,
            )
            .setHandleAudioBecomingNoisy(true)
            // Start (and restart after a seek) with 1 s buffered instead of Media3's 2.5 s / 5 s, and keep the last
            // 10 s in memory when the TV has room (a skip back then needs no download).
            .setLoadControl(
                DefaultLoadControl.Builder()
                    .setBufferDurationsMs(
                        DefaultLoadControl.DEFAULT_MIN_BUFFER_MS,
                        DefaultLoadControl.DEFAULT_MAX_BUFFER_MS,
                        START_BUFFER_MS,
                        REBUFFER_MS,
                    )
                    .setTargetBufferBytes(plan.targetBytes)
                    .setBackBuffer(plan.backBufferMs, true)
                    .build()
            )
            .build()
        exo.addListener(listener)
        exo.addAnalyticsListener(analytics)
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
        clearPendingSeek()
        player = null
        playerView.keepScreenOn = false
        playerView.player = null
        playerView.visibility = View.GONE
        session?.release()
        session = null
        p.removeListener(listener)
        p.removeAnalyticsListener(analytics)
        p.release()
        restoreDisplayMode()
        if (notifyClosed) onClosed()
    }

    fun pause() {
        player?.pause()
    }

    fun resume() {
        player?.play()
    }

    /** Page-driven seek. "seeked" follows when playback is ready at the new position. */
    fun seekTo(seconds: Double) {
        val p = player ?: return
        if (seconds.isNaN() || seconds.isInfinite()) return
        var target = (seconds * 1000).toLong()
        if (target < 0) target = 0
        val dur = p.duration
        if (dur != C.TIME_UNSET && dur > 0 && target > dur) target = dur
        pendingSeekTargetMs = target
        pendingSeekPositionMs = null
        handler.removeCallbacks(seekCompletionCheck)
        seekTimer.start(SystemClock.elapsedRealtime())
        // A jump inside what is already loaded is served from the buffer; Media3 does that by itself, so nothing here
        // drops or flushes the buffer.
        p.seekTo(target)
        checkSeekCompletion()
    }

    /**
     * Bridge v5: select the [order]-th audio track group of the playing file (file order), without reloading. Before the
     * tracks are known the choice is kept and applied when they are.
     */
    fun selectAudioByOrder(order: Int) {
        if (player == null) return
        audioOrder = order
        appliedAudioOrder = -1
        applyAudioOrder()
    }

    private fun applyAudioOrder() {
        val p = player ?: return
        val wanted = audioOrder
        if (wanted < 0 || wanted == appliedAudioOrder) return
        val groups = p.currentTracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }
        // The page's order is the file's; Media3's list puts tracks only FFmpeg decodes last.
        val inFile = fileOrder(groups.map { g -> if (g.length > 0) g.getTrackFormat(0).id else null })
        if (wanted >= inFile.size) return
        p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
            .setOverrideForType(TrackSelectionOverride(groups[inFile[wanted]].mediaTrackGroup, 0))
            .build()
        appliedAudioOrder = wanted
    }

    /** Ask the display for a mode whose refresh rate is a multiple of the film's frame rate (once per film). */
    private fun matchFrameRate(tracks: Tracks) {
        if (frameRateHandled) return
        var frameRate = 0f
        for (group in tracks.groups) {
            if (group.type != C.TRACK_TYPE_VIDEO) continue
            for (i in 0 until group.length) {
                if (group.isTrackSelected(i)) frameRate = group.getTrackFormat(i).frameRate
            }
        }
        if (frameRate <= 0f) return
        frameRateHandled = true
        @Suppress("DEPRECATION")
        val display = activity.windowManager.defaultDisplay ?: return
        val current = display.mode
        val modes = display.supportedModes.map {
            DisplayModeInfo(it.modeId, it.physicalWidth, it.physicalHeight, it.refreshRate)
        }
        val id = pickDisplayModeId(modes, current.physicalWidth, current.physicalHeight, frameRate) ?: return
        val attrs = activity.window.attributes
        if (attrs.preferredDisplayModeId == id) return
        previousDisplayModeId = attrs.preferredDisplayModeId
        attrs.preferredDisplayModeId = id
        activity.window.attributes = attrs
    }

    private fun restoreDisplayMode() {
        val previous = previousDisplayModeId ?: return
        previousDisplayModeId = null
        val attrs = activity.window.attributes
        attrs.preferredDisplayModeId = previous
        activity.window.attributes = attrs
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
     * to the next stream. The view styles plain text lines; [SubtitleLook] does the rest (picture subtitles, and text
     * lines that carry their own position).
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
            else -> MEDIUM_SCALE
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
        subtitleLook.setStyle(scale / MEDIUM_SCALE, RAISE_STEP_FRACTION * raise, background)
    }

    /** Bridge v7: subtitle delay in seconds, positive = later. For this stream only; start() sets it back to 0. */
    fun setSubtitleDelay(seconds: Double) {
        subtitleLook.delayUs = subtitleDelayUs(seconds)
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

    private fun checkSeekCompletion() {
        if (pendingSeekTargetMs == null) return
        handler.removeCallbacks(seekCompletionCheck)
        seekCompletionCheck.run()
    }

    private fun clearPendingSeek() {
        handler.removeCallbacks(seekCompletionCheck)
        pendingSeekTargetMs = null
        pendingSeekPositionMs = null
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
        val embedded = subtitleTracks.firstOrNull { it.index == index && it.kind == "embedded" }
        if (embedded != null) return selectEmbeddedSubtitle(p, embedded.index, embedded.order)
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

    /**
     * Bridge v6: a subtitle stream inside the file (a disc PGS track) that Media3 reads and draws itself. [order] is its
     * position among the file's subtitle streams; the side-loaded text groups are not counted. The language is checked
     * too: if Media3 skipped a stream the server counted, the order alone lands on the next track.
     */
    private fun selectEmbeddedSubtitle(p: ExoPlayer, index: Int, order: Int): Boolean {
        val groups = p.currentTracks.groups.filter { it.type == C.TRACK_TYPE_TEXT }
        val infos = groups.map { g ->
            val format = if (g.length > 0) g.getTrackFormat(0) else null
            TextGroupInfo(format?.id, normalizeLanguage(format?.language))
        }
        val wanted = subtitleTracks.firstOrNull { it.index == index }
        val language = normalizeLanguage(wanted?.language)
        val rank = subtitleTracks
            .filter { it.kind == "embedded" && normalizeLanguage(it.language) == language }
            .sortedBy { it.order }
            .indexOfFirst { it.index == index }
        val position = embeddedTextGroupPosition(infos, order, language, rank.coerceAtLeast(0))
            ?: return false
        p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
            .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
            .setOverrideForType(TrackSelectionOverride(groups[position].mediaTrackGroup, 0))
            .build()
        selectedSubtitleIndex = index
        return true
    }

    private fun normalizeLanguage(language: String?): String =
        if (language.isNullOrEmpty() || language == C.LANGUAGE_UNDETERMINED) "" else Util.normalizeLanguageCode(language)

    companion object {
        private const val TIME_MS = 500L
        private const val PROGRESS_EVERY_TICKS = 20 // 20 x 500 ms = 10 s
        private const val SEEK_CHECK_MS = 50L
        private const val SEEK_PROGRESS_MS = 200L
        private const val SUB_ID_PREFIX = SIDE_LOADED_SUBTITLE_PREFIX
        private const val MEDIUM_SCALE = 1.15f
        private const val RAISE_STEP_FRACTION = 0.07f // each "raise" step lifts the lines by 7% of the screen height
        private const val START_BUFFER_MS = 1_000
        // Buffered before playback resumes after a seek or a stall. The network measured 294 Mbps against a 92 Mbps film,
        // so 1 s is quickly refilled and a jump starts about a second sooner. Other buffer sizes are Media3's.
        private const val REBUFFER_MS = 1_000
    }
}
