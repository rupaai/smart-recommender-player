package app.northernlights.tv

import org.json.JSONObject

/**
 * Resident memory in MB from the text of /proc/self/statm ("size resident shared ..." in pages). Reading that one
 * line is cheap; Debug.getPss() walks every memory mapping of the process, which can hold up the player, and the panel
 * asks once a second. 0 when the text cannot be read.
 */
fun residentMbFromStatm(statm: String?, pageBytes: Long = 4096L): Int {
    val pages = statm?.trim()?.split(Regex("\\s+"))?.getOrNull(1)?.toLongOrNull() ?: return 0
    return (pages * pageBytes / (1024L * 1024L)).toInt()
}

private const val MB = 1024L * 1024L

/** Media3's own memory buffer: 2000 + 200 segments of 64 KB (video + audio), about 15 s of a 92 Mbps remux. */
const val DEFAULT_BUFFER_BYTES = 2_200 * 65_536

/** Extra room for the back buffer: about 10 s of a 100 Mbps remux (lighter films keep 10 s in less). */
const val BACK_BUFFER_BYTES = 128 * 1024 * 1024
const val BACK_BUFFER_MS = 10_000

/** How much the player may hold in memory: [targetBytes] in all, of which the last [backBufferMs] already played. */
data class BufferPlan(val targetBytes: Int, val backBufferMs: Int)

/**
 * Keeps the last 10 s in memory (instant skip back) only when the app's heap cap ([maxHeapBytes], 384 MB with
 * largeHeap) and the TV's free memory at the start of playback ([availBytes]) both have room for it; the forward
 * buffer stays the size Media3 picks. Never more than 70% of the heap, so the rest of the app keeps its share.
 */
fun bufferPlan(maxHeapBytes: Long, availBytes: Long): BufferPlan {
    val withBack = DEFAULT_BUFFER_BYTES.toLong() + BACK_BUFFER_BYTES
    val fits = withBack <= maxHeapBytes * 7 / 10 && availBytes >= BACK_BUFFER_BYTES + 96 * MB
    return if (fits) BufferPlan(withBack.toInt(), BACK_BUFFER_MS) else BufferPlan(DEFAULT_BUFFER_BYTES, 0)
}

/** Id prefix of the text tracks the page asks Media3 to side-load (see PlayerController). */
const val SIDE_LOADED_SUBTITLE_PREFIX = "nlsub"

/** Media3 prefixes side-loaded track ids with the source number ("1:nlsub3"). */
fun isSideLoadedSubtitleId(id: String?): Boolean =
    id != null && (id.startsWith(SIDE_LOADED_SUBTITLE_PREFIX) || id.contains(":$SIDE_LOADED_SUBTITLE_PREFIX"))

/**
 * The position, in the player's list of text track groups, of the [order]-th subtitle stream that is inside the file.
 * [formatIds] has one entry per text group (the id of its first format). Side-loaded groups are skipped, so the
 * server's `order` (position among the file's own subtitle streams, file order) maps to the right group.
 * Null when [order] is negative or the file has fewer such groups.
 */
fun embeddedTextGroupPosition(formatIds: List<String?>, order: Int): Int? {
    if (order < 0) return null
    var seen = 0
    for ((position, id) in formatIds.withIndex()) {
        if (isSideLoadedSubtitleId(id)) continue
        if (seen == order) return position
        seen++
    }
    return null
}

// C.COLOR_TRANSFER_* values, kept here so this file stays free of Media3 (unit tests run on a plain JVM).
private const val COLOR_TRANSFER_ST2084 = 6
private const val COLOR_TRANSFER_HLG = 7

/** Plain-words picture type for the info panel. Profile 7 is played as its HDR10 base layer (see VideoSupport.kt). */
fun hdrLabel(codecs: String?, colorTransfer: Int): String {
    val c = codecs?.trim()?.lowercase() ?: ""
    if (c.startsWith("dvhe.07") || c.startsWith("dvh1.07")) return "Dolby Vision 7 (HDR10 base layer)"
    if (c.startsWith("dvhe") || c.startsWith("dvh1") || c.startsWith("dvav") || c.startsWith("dva1")) return "Dolby Vision"
    return when (colorTransfer) {
        COLOR_TRANSFER_ST2084 -> "HDR10"
        COLOR_TRANSFER_HLG -> "HLG"
        else -> "SDR"
    }
}

/** Number of speakers in an AudioTrack.CHANNEL_OUT_* mask (one bit each). */
fun channelCountOf(channelConfig: Int): Int = Integer.bitCount(channelConfig)

/** Times a jump: from the page's seekTo to the player being ready again. Pure, the caller passes the clock. */
class SeekTimer {
    private var startedAt = -1L

    /** Last finished jump in milliseconds, -1 before the first one. */
    var lastMs = -1L
        private set

    fun start(nowMs: Long) {
        startedAt = nowMs
    }

    /** Ends the running jump, if any (a second call without a new [start] does nothing). */
    fun ready(nowMs: Long) {
        if (startedAt < 0) return
        lastMs = (nowMs - startedAt).coerceAtLeast(0)
        startedAt = -1
    }

    fun reset() {
        startedAt = -1
        lastMs = -1
    }
}

/** What the info panel shows; built on the main thread, serialised by [toJson] on whichever thread asks. */
data class PlaybackStats(
    val source: String = "original",
    val videoDecoder: String = "",
    val width: Int = 0,
    val height: Int = 0,
    val hdr: String = "",
    val droppedFrames: Long = 0,
    val audioLabel: String = "",
    /** "passthrough", "decoded" or "" before the audio output exists. */
    val audioPath: String = "",
    val audioChannels: Int = 0,
    val bandwidthMbps: Double = 0.0,
    val bufferedAheadSeconds: Double = 0.0,
    val lastSeekMs: Long = -1,
) {
    fun toJson(memoryMb: Int): String = JSONObject()
        .put("source", source)
        .put("videoDecoder", videoDecoder)
        .put("width", width)
        .put("height", height)
        .put("hdr", hdr)
        .put("droppedFrames", droppedFrames)
        .put("audioLabel", audioLabel)
        .put("audioPath", audioPath)
        .put("audioChannels", audioChannels)
        .put("bandwidthMbps", bandwidthMbps)
        .put("bufferedAheadSeconds", bufferedAheadSeconds)
        .put("lastSeekMs", lastSeekMs)
        .put("memoryMb", memoryMb)
        .toString()
}
