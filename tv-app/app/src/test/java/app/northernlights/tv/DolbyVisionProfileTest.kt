package app.northernlights.tv

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DolbyVisionProfileTest {
    @Test
    fun profileSevenIsRecognised() {
        assertTrue(isDolbyVisionProfile7("video/dolby-vision", "dvhe.07.06"))
        assertTrue(isDolbyVisionProfile7("video/dolby-vision", "DVHE.07.06"))
        assertTrue(isDolbyVisionProfile7("video/hevc", "dvhe.07.06"))
        assertTrue(isDolbyVisionProfile7("video/dolby-vision", "dvh1.07.06"))
    }

    @Test
    fun otherProfilesAreNot() {
        assertFalse(isDolbyVisionProfile7("video/dolby-vision", "dvhe.05.06"))
        assertFalse(isDolbyVisionProfile7("video/dolby-vision", "dvhe.08.06"))
        assertFalse(isDolbyVisionProfile7("video/dolby-vision", "dvav.09.03"))
        assertFalse(isDolbyVisionProfile7("video/dolby-vision", "dvhe.17.06"))
    }

    @Test
    fun plainVideoAndMissingInfoAreNot() {
        assertFalse(isDolbyVisionProfile7("video/hevc", "hvc1.2.4.L153.B0"))
        assertFalse(isDolbyVisionProfile7("video/hevc", null))
        assertFalse(isDolbyVisionProfile7("video/dolby-vision", null))
        assertFalse(isDolbyVisionProfile7(null, "dvhe.07.06"))
        assertFalse(isDolbyVisionProfile7("video/avc", "dvhe.07.06"))
    }
}
