package app.northernlights.tv

import android.content.Context
import android.os.Handler
import androidx.media3.common.Format
import androidx.media3.common.MimeTypes
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.Renderer
import androidx.media3.exoplayer.mediacodec.MediaCodecInfo
import androidx.media3.exoplayer.mediacodec.MediaCodecSelector
import androidx.media3.exoplayer.video.MediaCodecVideoRenderer
import androidx.media3.exoplayer.video.VideoRendererEventListener
import kotlin.math.abs
import kotlin.math.roundToInt

/** True for Dolby Vision profile 7 (dual layer). Pure, so it can be unit-tested. */
fun isDolbyVisionProfile7(sampleMimeType: String?, codecs: String?): Boolean {
    val mime = sampleMimeType?.lowercase()
    if (mime != "video/dolby-vision" && mime != "video/hevc") return false
    val c = codecs?.trim()?.lowercase() ?: return false
    return c.startsWith("dvhe.07") || c.startsWith("dvh1.07")
}

/**
 * Profile 7 is played as its HDR10 base layer: only HEVC decoders are offered, so the TV never feeds profile 7 to its
 * Dolby Vision decoder. Every other format (including DV profiles 5 and 8) uses [base] unchanged.
 * A MediaCodecSelector only sees the MIME type, so [VideoRenderer] picks the selector per Format.
 */
fun selectorFor(base: MediaCodecSelector, format: Format): MediaCodecSelector =
    if (isDolbyVisionProfile7(format.sampleMimeType, format.codecs)) {
        MediaCodecSelector { _, secure, tunneling -> base.getDecoderInfos(MimeTypes.VIDEO_H265, secure, tunneling) }
    } else {
        base
    }

class VideoRenderer(
    context: Context,
    factory: androidx.media3.exoplayer.mediacodec.MediaCodecAdapter.Factory,
    selector: MediaCodecSelector,
    joiningMs: Long,
    fallback: Boolean,
    handler: Handler,
    listener: VideoRendererEventListener,
) : MediaCodecVideoRenderer(
    context, factory, selector, joiningMs, fallback, handler, listener,
    DefaultRenderersFactory.MAX_DROPPED_VIDEO_FRAME_COUNT_TO_NOTIFY,
) {
    override fun supportsFormat(mediaCodecSelector: MediaCodecSelector, format: Format): Int =
        super.supportsFormat(selectorFor(mediaCodecSelector, format), format)

    override fun getDecoderInfos(
        mediaCodecSelector: MediaCodecSelector,
        format: Format,
        requiresSecureDecoder: Boolean,
    ): List<MediaCodecInfo> = super.getDecoderInfos(selectorFor(mediaCodecSelector, format), format, requiresSecureDecoder)
}

/** One mode of the display (id, size in pixels, refresh rate in Hz). */
data class DisplayModeInfo(val id: Int, val width: Int, val height: Int, val refreshRate: Float)

/**
 * The display mode to ask for so a film plays without judder: same resolution as the current one, with a refresh rate
 * that is a whole multiple of [frameRate] (23.976 -> 23.976 / 47.95 / 119.88 Hz). The smallest multiple wins, then the
 * lowest id. Null when the frame rate is unknown or no mode fits. Pure, so it can be unit-tested.
 */
fun pickDisplayModeId(modes: List<DisplayModeInfo>, currentWidth: Int, currentHeight: Int, frameRate: Float): Int? {
    if (frameRate.isNaN() || frameRate < 10f || frameRate > 125f) return null
    var best: DisplayModeInfo? = null
    var bestMultiple = Int.MAX_VALUE
    for (m in modes) {
        if (m.width != currentWidth || m.height != currentHeight) continue
        val multiple = (m.refreshRate / frameRate).roundToInt()
        if (multiple < 1) continue
        if (abs(m.refreshRate - multiple * frameRate) > 0.01f * multiple) continue
        if (multiple < bestMultiple || (multiple == bestMultiple && m.id < (best?.id ?: Int.MAX_VALUE))) {
            best = m
            bestMultiple = multiple
        }
    }
    return best?.id
}
