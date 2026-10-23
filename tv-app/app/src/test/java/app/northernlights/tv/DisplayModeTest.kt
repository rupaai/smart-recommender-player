package app.northernlights.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DisplayModeTest {
    private val modes = listOf(
        DisplayModeInfo(1, 3840, 2160, 60f),
        DisplayModeInfo(2, 3840, 2160, 59.94f),
        DisplayModeInfo(3, 3840, 2160, 23.976f),
        DisplayModeInfo(4, 3840, 2160, 24f),
        DisplayModeInfo(5, 3840, 2160, 50f),
        DisplayModeInfo(6, 1920, 1080, 23.976f),
        DisplayModeInfo(7, 3840, 2160, 119.88f),
    )

    @Test
    fun filmAt23976PicksTheExactModeNotThe24() {
        assertEquals(3, pickDisplayModeId(modes, 3840, 2160, 23.976f))
    }

    @Test
    fun aMultipleIsUsedWhenThereIsNoExactMode() {
        val noExact = modes.filter { it.id != 3 }
        assertEquals(7, pickDisplayModeId(noExact, 3840, 2160, 23.976f))
        val only = listOf(DisplayModeInfo(9, 3840, 2160, 47.952f), DisplayModeInfo(1, 3840, 2160, 60f))
        assertEquals(9, pickDisplayModeId(only, 3840, 2160, 23.976f))
    }

    @Test
    fun theResolutionMustMatch() {
        assertEquals(6, pickDisplayModeId(modes, 1920, 1080, 23.976f))
        assertNull(pickDisplayModeId(modes, 1280, 720, 23.976f))
    }

    @Test
    fun filmAt25And24() {
        assertEquals(5, pickDisplayModeId(modes, 3840, 2160, 25f))
        assertEquals(4, pickDisplayModeId(modes, 3840, 2160, 24f))
    }

    @Test
    fun aDoubleRateModeServesAHalfRateFilm() {
        assertEquals(2, pickDisplayModeId(modes, 3840, 2160, 29.97f))
    }

    @Test
    fun unknownOrOddFrameRatesDoNothing() {
        assertNull(pickDisplayModeId(modes, 3840, 2160, 0f))
        assertNull(pickDisplayModeId(modes, 3840, 2160, -1f))
        assertNull(pickDisplayModeId(modes, 3840, 2160, Float.NaN))
        assertNull(pickDisplayModeId(modes, 3840, 2160, 17f))
    }
}
