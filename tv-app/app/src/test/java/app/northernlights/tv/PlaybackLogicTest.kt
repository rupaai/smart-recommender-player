package app.northernlights.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackLogicTest {
    @Test
    fun residentMemoryComesFromTheSecondStatmField() {
        assertEquals(400, residentMbFromStatm("2000000 102400 3000 10 0 50000 0\n"))   // 102400 pages x 4 KB
        assertEquals(0, residentMbFromStatm(null))
        assertEquals(0, residentMbFromStatm("garbage"))
    }

    @Test
    fun backBufferOnlyWhenMemoryAllows() {
        val mb = 1024L * 1024L
        // This TV with largeHeap: 384 MB cap, about 390 MB free before the buffer fills.
        assertEquals(BufferPlan(DEFAULT_BUFFER_BYTES + BACK_BUFFER_BYTES, BACK_BUFFER_MS), bufferPlan(384 * mb, 390 * mb))
        // Without largeHeap (192 MB) or with little free memory, Media3's defaults and no back buffer.
        assertEquals(BufferPlan(DEFAULT_BUFFER_BYTES, 0), bufferPlan(192 * mb, 390 * mb))
        assertEquals(BufferPlan(DEFAULT_BUFFER_BYTES, 0), bufferPlan(384 * mb, 150 * mb))
    }

    @Test
    fun sideLoadedIdsAreRecognised() {
        assertTrue(isSideLoadedSubtitleId("nlsub3"))
        assertTrue(isSideLoadedSubtitleId("1:nlsub3"))
        assertFalse(isSideLoadedSubtitleId("4"))
        assertFalse(isSideLoadedSubtitleId(null))
    }

    private fun groups(vararg ids: String?) = ids.map { TextGroupInfo(it, null) }

    @Test
    fun embeddedOrderSkipsSideLoadedGroups() {
        // Text groups as Media3 lists them: two from the file, one side-loaded, one more from the file.
        val g = groups("3", "5", "1:nlsub7", "9")
        assertEquals(0, embeddedTextGroupPosition(g, 0))
        assertEquals(1, embeddedTextGroupPosition(g, 1))
        assertEquals(3, embeddedTextGroupPosition(g, 2))
        assertNull(embeddedTextGroupPosition(g, 3))
    }

    @Test
    fun embeddedOrderEdgeCases() {
        assertNull(embeddedTextGroupPosition(emptyList(), 0))
        assertNull(embeddedTextGroupPosition(groups("1"), -1))
        assertNull(embeddedTextGroupPosition(groups("nlsub1", "nlsub2"), 0))
        assertEquals(0, embeddedTextGroupPosition(groups(null), 0))
    }

    @Test
    fun languageWinsWhenMedia3SkippedAStream() {
        // The server counted English, German, Italian, Hindi; Media3 left out one stream before Italian, so order 2
        // (Italian) would land on Hindi. That is the "off by one" seen on the TV.
        val g = listOf(TextGroupInfo("3", "en"), TextGroupInfo("5", "it"), TextGroupInfo("6", "hi"))
        assertEquals(1, embeddedTextGroupPosition(g, 2, "it", 0))
        // Order and language agree: the order is used.
        assertEquals(2, embeddedTextGroupPosition(listOf(TextGroupInfo("3", "en"), TextGroupInfo("4", "de"),
            TextGroupInfo("5", "it")), 2, "it", 0))
        // Two Italian tracks (full and forced): the rank picks between them.
        val two = listOf(TextGroupInfo("3", "it"), TextGroupInfo("4", "hi"), TextGroupInfo("5", "it"))
        assertEquals(2, embeddedTextGroupPosition(two, 3, "it", 1))
        assertEquals(0, embeddedTextGroupPosition(two, 3, "it", 0))
        // No group in that language, or groups without a language: fall back to the order.
        assertEquals(1, embeddedTextGroupPosition(g, 1, "fr", 0))
        assertEquals(1, embeddedTextGroupPosition(groups("3", "5"), 1, "it", 0))
        // Side-loaded groups never count, even in the right language.
        assertEquals(2, embeddedTextGroupPosition(listOf(TextGroupInfo("3", "en"), TextGroupInfo("nlsub9", "it"),
            TextGroupInfo("5", "it")), 5, "it", 0))
    }

    @Test
    fun audioGroupsGoBackInFileOrder() {
        // "Michael (2026)": TrueHD (track 2) is played by the FFmpeg decoder, so Media3 lists it after Russian (track 9).
        // File order: TrueHD, AC3, Spanish, French, German, Italian, Hindi, Russian.
        val media3 = listOf("3", "4", "5", "6", "7", "8", "9", "2")
        val inFile = fileOrder(media3)
        assertEquals("7", media3[inFile[5]]) // the 6th audio track (Italian) is track 7, not Hindi (8)
        assertEquals("2", media3[inFile[0]])
        // With side-loaded subtitles the ids carry a source prefix.
        assertEquals(listOf(1, 0), fileOrder(listOf("0:5", "0:2")))
        // Ids that are not track numbers: keep Media3's order.
        assertEquals(listOf(0, 1), fileOrder(listOf("5", null)))
        assertEquals(listOf(0, 1), fileOrder(listOf("b", "a")))
    }

    @Test
    fun delayIsClampedToAMinute() {
        assertEquals(1_250_000L, subtitleDelayUs(1.25))
        assertEquals(-500_000L, subtitleDelayUs(-0.5))
        assertEquals(60_000_000L, subtitleDelayUs(600.0))
        assertEquals(0L, subtitleDelayUs(Double.NaN))
    }

    @Test
    fun pictureSubtitlesScaleAroundTheirBottomCentreAndRise() {
        val box = CueBox(0.3f, 0.8f, 0.4f, 0.1f) // bottom at 0.9, centre at 0.5
        val same = placePictureCue(box, 1f, 0f)
        assertEquals(0.3f, same.left, 1e-5f)
        assertEquals(0.8f, same.top, 1e-5f)
        val raised = placePictureCue(box, 1f, 0.07f)
        assertEquals(0.73f, raised.top, 1e-5f)
        assertEquals(0.3f, raised.left, 1e-5f)
        val bigger = placePictureCue(box, 1.5f, 0f)
        assertEquals(0.6f, bigger.width, 1e-5f)
        assertEquals(0.2f, bigger.left, 1e-5f)
        assertEquals(0.9f, bigger.top + bigger.height, 1e-5f)
        // Never pushed off the screen.
        val huge = placePictureCue(CueBox(0.1f, 0.0f, 0.8f, 0.2f), 2f, 0.14f)
        assertEquals(0f, huge.top, 1e-5f)
        assertEquals(0f, huge.left, 1e-5f)
    }

    @Test
    fun anchorsAndPadding() {
        assertEquals(0.5f, anchoredStart(0.5f, 0.2f, 0), 1e-5f)
        assertEquals(0.4f, anchoredStart(0.5f, 0.2f, 1), 1e-5f)
        assertEquals(0.3f, anchoredStart(0.5f, 0.2f, 2), 1e-5f)
        val padded = padPictureBox(CueBox(0.2f, 0.8f, 0.4f, 0.1f), 400, 100, 10)
        assertEquals(0.19f, padded.left, 1e-5f)
        assertEquals(0.79f, padded.top, 1e-5f)
        assertEquals(0.42f, padded.width, 1e-5f)
        assertEquals(0.12f, padded.height, 1e-5f)
    }

    @Test
    fun onlyLowTextLinesRise() {
        assertEquals(0.88f, raiseTextLine(0.95f, 0.07f), 1e-5f)
        assertEquals(0.1f, raiseTextLine(0.1f, 0.07f), 1e-5f) // a sign at the top stays
    }

    @Test
    fun pictureTypeInPlainWords() {
        assertEquals("Dolby Vision 7 (HDR10 base layer)", hdrLabel("dvhe.07.06", 6))
        assertEquals("Dolby Vision", hdrLabel("dvh1.08.06", 6))
        assertEquals("HDR10", hdrLabel("hvc1.2.4.L153.B0", 6))
        assertEquals("HLG", hdrLabel(null, 7))
        assertEquals("SDR", hdrLabel("avc1.640028", 3))
    }

    @Test
    fun channelsAreCountedFromTheMask() {
        assertEquals(2, channelCountOf(0x4 or 0x8)) // front left + front right
        assertEquals(6, channelCountOf(0xfc))       // CHANNEL_OUT_5POINT1
        assertEquals(0, channelCountOf(0))
    }

    @Test
    fun seekTimerMeasuresOneJump() {
        val t = SeekTimer()
        assertEquals(-1L, t.lastMs)
        t.ready(500) // no jump running: ignored
        assertEquals(-1L, t.lastMs)
        t.start(1_000)
        t.ready(1_900)
        assertEquals(900L, t.lastMs)
        t.ready(5_000) // already finished: unchanged
        assertEquals(900L, t.lastMs)
        t.start(6_000)
        t.start(6_100) // a second jump before the first ended restarts the clock
        t.ready(6_400)
        assertEquals(300L, t.lastMs)
        t.reset()
        assertEquals(-1L, t.lastMs)
    }
}
