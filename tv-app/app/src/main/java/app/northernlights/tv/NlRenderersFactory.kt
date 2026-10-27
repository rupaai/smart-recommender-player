package app.northernlights.tv

import android.content.Context
import android.os.Handler
import android.os.Looper
import androidx.media3.common.AudioAttributes
import androidx.media3.common.audio.ChannelMixingAudioProcessor
import androidx.media3.common.audio.ChannelMixingMatrix
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.Renderer
import androidx.media3.exoplayer.audio.AudioCapabilities
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.mediacodec.MediaCodecSelector
import androidx.media3.exoplayer.text.TextOutput
import androidx.media3.exoplayer.text.TextRenderer
import androidx.media3.exoplayer.video.VideoRendererEventListener

/**
 * Renderers for the TV:
 *  - audio: platform decoders first (so TrueHD / DTS go out untouched over HDMI when the output accepts them), then
 *    Jellyfin's FFmpeg decoder (EXTENSION_RENDERER_MODE_ON) for what the output cannot take;
 *  - when the output is stereo, 5.1 and 7.1 PCM is downmixed by [AudioDownmix] instead of the platform's quiet default;
 *  - video: [VideoRenderer] plays Dolby Vision profile 7 as its HDR10 base layer;
 *  - subtitles: delayed and styled through [look] (see [SubtitleLook]).
 */
class NlRenderersFactory(context: Context, private val look: SubtitleLook) : DefaultRenderersFactory(context) {
    init {
        setExtensionRendererMode(EXTENSION_RENDERER_MODE_ON)
    }

    override fun buildAudioSink(
        context: Context,
        enableFloatOutput: Boolean,
        enableAudioTrackPlaybackParams: Boolean,
    ): AudioSink? {
        val builder = DefaultAudioSink.Builder(context)
            .setEnableFloatOutput(enableFloatOutput)
            .setEnableAudioTrackPlaybackParams(enableAudioTrackPlaybackParams)
        val capabilities = AudioCapabilities.getCapabilities(context, AudioAttributes.DEFAULT, null)
        if (capabilities.maxChannelCount <= 2) {
            // The mixer needs a matrix for every channel count it may be given; identity means "leave as is".
            val mixer = ChannelMixingAudioProcessor()
            for (channels in 1..8) {
                val coefficients = AudioDownmix.stereoMatrix(channels)
                mixer.putChannelMixingMatrix(
                    if (coefficients != null) ChannelMixingMatrix(channels, 2, coefficients)
                    else ChannelMixingMatrix.create(channels, channels)
                )
            }
            builder.setAudioProcessors(arrayOf(mixer))
        }
        return builder.build()
    }

    override fun buildVideoRenderers(
        context: Context,
        extensionRendererMode: Int,
        mediaCodecSelector: MediaCodecSelector,
        enableDecoderFallback: Boolean,
        eventHandler: Handler,
        eventListener: VideoRendererEventListener,
        allowedVideoJoiningTimeMs: Long,
        out: ArrayList<Renderer>,
    ) {
        out.add(
            VideoRenderer(
                context, codecAdapterFactory, mediaCodecSelector, allowedVideoJoiningTimeMs,
                enableDecoderFallback, eventHandler, eventListener,
            )
        )
    }

    override fun buildTextRenderers(
        context: Context,
        output: TextOutput,
        outputLooper: Looper,
        extensionRendererMode: Int,
        out: ArrayList<Renderer>,
    ) {
        out.add(DelayedTextRenderer(TextRenderer(look.textOutput(output), outputLooper), look))
    }
}
