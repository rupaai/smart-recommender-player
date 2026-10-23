package app.northernlights.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackLogicTest {
    @Test
    fun sideLoadedIdsAreRecognised() {
        assertTrue(isSideLoadedSubtitleId("nlsub3"))
        assertTrue(isSideLoadedSubtitleId("1:nlsub3"))
        assertFalse(isSideLoadedSubtitleId("4"))
        assertFalse(isSideLoadedSubtitleId(null))
    }

    @Test
    fun embeddedOrderSkipsSideLoadedGroups() {
        // Text groups as Media3 lists them: two from the file, one side-loaded, one more from the file.
        val ids = listOf("3", "5", "1:nlsub7", "9")
        assertEquals(0, embeddedTextGroupPosition(ids, 0))
        assertEquals(1, embeddedTextGroupPosition(ids, 1))
        assertEquals(3, embeddedTextGroupPosition(ids, 2))
        assertNull(embeddedTextGroupPosition(ids, 3))
    }

    @Test
    fun embeddedOrderEdgeCases() {
        assertNull(embeddedTextGroupPosition(emptyList(), 0))
        assertNull(embeddedTextGroupPosition(listOf("1"), -1))
        assertNull(embeddedTextGroupPosition(listOf("nlsub1", "nlsub2"), 0))
        assertEquals(0, embeddedTextGroupPosition(listOf(null), 0))
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
