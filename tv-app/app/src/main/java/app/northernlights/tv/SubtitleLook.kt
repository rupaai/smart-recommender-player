package app.northernlights.tv

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import androidx.media3.common.text.Cue
import androidx.media3.common.text.CueGroup
import androidx.media3.exoplayer.Renderer
import androidx.media3.exoplayer.text.TextOutput

/**
 * What the subtitle view's own style cannot do, done on the cues before Media3 draws them (bridge v7):
 *  - delay: the text renderer is asked for the subtitles of a shifted time ([DelayedTextRenderer]);
 *  - picture subtitles (PGS, DVD, DVB): size, position and the dark box ([styleCue]);
 *  - text subtitles that carry their own line (ASS/SSA): position.
 * The renderer reads [delayUs] on the playback thread; everything else runs on the main thread, where Media3 hands
 * out the cues.
 */
class SubtitleLook {
    @Volatile var delayUs = 0L

    private var pictureScale = 1f
    private var raise = 0f
    private var pictureBox = Color.TRANSPARENT
    private var output: TextOutput? = null
    private var lastCues: CueGroup? = null
    private var boxedSource: Bitmap? = null
    private var boxed: Bitmap? = null

    /** [scale] is relative to the medium size, [raise] a fraction of the screen height, [box] transparent for none. */
    fun setStyle(scale: Float, raise: Float, box: Int) {
        pictureScale = scale
        this.raise = raise
        if (box != pictureBox) {
            boxedSource = null
            boxed = null
        }
        pictureBox = box
        val cues = lastCues ?: return
        output?.let { deliver(it, cues) } // restyle what is on screen now, not just the next line
    }

    /** A new stream: no delay, nothing on screen. */
    fun reset() {
        delayUs = 0L
        lastCues = null
        boxedSource = null
        boxed = null
    }

    fun textOutput(inner: TextOutput): TextOutput {
        output = inner
        return object : TextOutput {
            @Deprecated("Media3 still calls both")
            override fun onCues(cues: List<Cue>) {
                // The styled list goes out with onCues(CueGroup) below; this one only keeps old listeners in step.
                @Suppress("DEPRECATION")
                inner.onCues(cues.map { styleCue(it) })
            }

            override fun onCues(cueGroup: CueGroup) {
                lastCues = cueGroup
                inner.onCues(CueGroup(cueGroup.cues.map { styleCue(it) }, cueGroup.presentationTimeUs))
            }
        }
    }

    private fun deliver(inner: TextOutput, cues: CueGroup) {
        val styled = cues.cues.map { styleCue(it) }
        @Suppress("DEPRECATION")
        inner.onCues(styled)
        inner.onCues(CueGroup(styled, cues.presentationTimeUs))
    }

    private fun styleCue(cue: Cue): Cue {
        val bitmap = cue.bitmap
        if (bitmap == null) {
            if (raise <= 0f || cue.line == Cue.DIMEN_UNSET || cue.lineType != Cue.LINE_TYPE_FRACTION) return cue
            return cue.buildUpon().setLine(raiseTextLine(cue.line, raise), Cue.LINE_TYPE_FRACTION).build()
        }
        if (cue.line == Cue.DIMEN_UNSET || cue.lineType != Cue.LINE_TYPE_FRACTION || cue.position == Cue.DIMEN_UNSET ||
            cue.size == Cue.DIMEN_UNSET || cue.bitmapHeight == Cue.DIMEN_UNSET
        ) return cue
        var box = CueBox(
            anchoredStart(cue.position, cue.size, cue.positionAnchor),
            anchoredStart(cue.line, cue.bitmapHeight, cue.lineAnchor),
            cue.size,
            cue.bitmapHeight,
        )
        var image = bitmap
        if (Color.alpha(pictureBox) > 0) {
            val pad = (bitmap.height * BOX_PAD_FRACTION).toInt().coerceAtLeast(1)
            image = boxedBitmap(bitmap, pad)
            box = padPictureBox(box, bitmap.width, bitmap.height, pad)
        }
        if (image === bitmap && pictureScale == 1f && raise <= 0f) return cue
        val placed = placePictureCue(box, pictureScale, raise)
        return cue.buildUpon()
            .setBitmap(image)
            .setPosition(placed.left)
            .setPositionAnchor(Cue.ANCHOR_TYPE_START)
            .setLine(placed.top, Cue.LINE_TYPE_FRACTION)
            .setLineAnchor(Cue.ANCHOR_TYPE_START)
            .setSize(placed.width)
            .setBitmapHeight(placed.height)
            .build()
    }

    /** The image on a dark box [pad] pixels larger on each side. The last one is kept: a line stays up for seconds. */
    private fun boxedBitmap(source: Bitmap, pad: Int): Bitmap {
        if (boxedSource === source) boxed?.let { return it }
        val out = Bitmap.createBitmap(source.width + 2 * pad, source.height + 2 * pad, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        canvas.drawColor(pictureBox)
        canvas.drawBitmap(source, pad.toFloat(), pad.toFloat(), null)
        boxedSource = source
        boxed = out
        return out
    }

    companion object {
        private const val BOX_PAD_FRACTION = 0.15f
    }
}

/**
 * Media3's text renderer, asked for the subtitles [SubtitleLook.delayUs] earlier than the picture: a positive delay
 * shows each line later. Media3 1.5.1 has no subtitle offset of its own. The renderer reads subtitle samples ahead of
 * the picture, so a negative delay (lines sooner) works too, by up to what is buffered.
 */
class DelayedTextRenderer(private val inner: Renderer, private val look: SubtitleLook) : Renderer by inner {
    override fun render(positionUs: Long, elapsedRealtimeUs: Long) {
        inner.render(positionUs - look.delayUs, elapsedRealtimeUs)
    }

    // With a delay the last line may still be waiting when the film ends; don't hold the end of the film for it.
    override fun isEnded(): Boolean = inner.isEnded() || (look.delayUs != 0L && inner.hasReadStreamToEnd())

    // Renderer's default methods, passed on explicitly so the inner renderer's own versions run.
    override fun getDurationToProgressUs(positionUs: Long, elapsedRealtimeUs: Long): Long =
        inner.getDurationToProgressUs(positionUs, elapsedRealtimeUs)

    override fun setPlaybackSpeed(currentPlaybackSpeed: Float, targetPlaybackSpeed: Float) {
        inner.setPlaybackSpeed(currentPlaybackSpeed, targetPlaybackSpeed)
    }

    override fun enableMayRenderStartOfStream() {
        inner.enableMayRenderStartOfStream()
    }

    override fun release() {
        inner.release()
    }
}
