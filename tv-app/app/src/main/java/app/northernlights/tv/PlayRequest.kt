package app.northernlights.tv

import org.json.JSONArray
import org.json.JSONObject

data class AudioTrackInfo(val index: Int, val label: String, val language: String, val isDefault: Boolean)

data class SubtitleTrackInfo(
    val index: Int,
    val label: String,
    val language: String,
    val url: String?,
    val kind: String,
    /** Bridge v6: for kind "embedded", the position among the file's own subtitle streams; otherwise -1. */
    val order: Int = -1,
)

/** The JSON the web page passes to NorthernLightsNative.play(). */
data class PlayRequest(
    val url: String,
    val kind: String,
    val startSeconds: Double,
    val title: String,
    val subtitle: String,
    val skipSeconds: Int,
    val audioTracks: List<AudioTrackInfo>,
    val subtitleTracks: List<SubtitleTrackInfo>,
    val audioIndex: Int,
    /** Bridge v5: position of the chosen audio track among the file's audio tracks, -1 = leave Media3's choice. */
    val audioOrder: Int,
    val subtitleIndex: Int,
    val nextUpLabel: String?,
) {
    companion object {
        /**
         * Parses and validates. Every URL (stream and subtitles) must be on [serverOrigin];
         * anything else throws IllegalArgumentException and nothing is played.
         */
        fun parse(json: String, serverOrigin: String): PlayRequest {
            val o = JSONObject(json)
            val url = o.optString("url", "")
            require(url.isNotEmpty()) { "missing url" }
            require(ServerConfig.originOf(url) == serverOrigin) { "stream url is not on the server" }
            val kind = o.optString("kind", "file").let { if (it == "hls") "hls" else "file" }

            val audio = ArrayList<AudioTrackInfo>()
            val audioArr: JSONArray? = o.optJSONArray("audioTracks")
            if (audioArr != null) {
                for (i in 0 until audioArr.length()) {
                    val t = audioArr.optJSONObject(i) ?: continue
                    audio.add(
                        AudioTrackInfo(
                            index = t.optInt("index", i),
                            label = t.optString("label", "Track ${i + 1}"),
                            language = t.optString("language", ""),
                            isDefault = t.optBoolean("isDefault", false),
                        )
                    )
                }
            }

            val subs = ArrayList<SubtitleTrackInfo>()
            val subArr: JSONArray? = o.optJSONArray("subtitleTracks")
            if (subArr != null) {
                for (i in 0 until subArr.length()) {
                    val t = subArr.optJSONObject(i) ?: continue
                    val subUrl = if (t.isNull("url")) null else t.optString("url", "").ifEmpty { null }
                    if (subUrl != null) {
                        require(ServerConfig.originOf(subUrl) == serverOrigin) { "subtitle url is not on the server" }
                    }
                    subs.add(
                        SubtitleTrackInfo(
                            index = t.optInt("index", i),
                            label = t.optString("label", "Subtitle ${i + 1}"),
                            language = t.optString("language", ""),
                            url = subUrl,
                            kind = t.optString("kind", "text"),
                            order = if (t.isNull("order")) -1 else t.optInt("order", -1),
                        )
                    )
                }
            }

            val next = o.optJSONObject("nextUp")?.optString("label", "")?.ifEmpty { null }

            return PlayRequest(
                url = url,
                kind = kind,
                startSeconds = o.optDouble("startSeconds", 0.0).let { if (it.isNaN() || it < 0) 0.0 else it },
                title = o.optString("title", ""),
                subtitle = o.optString("subtitle", ""),
                skipSeconds = o.optInt("skipSeconds", 10).coerceIn(1, 600),
                audioTracks = audio,
                subtitleTracks = subs,
                audioIndex = o.optInt("audioIndex", -1),
                audioOrder = if (o.isNull("audioOrder")) -1 else o.optInt("audioOrder", -1),
                subtitleIndex = o.optInt("subtitleIndex", -1),
                nextUpLabel = next,
            )
        }
    }
}
