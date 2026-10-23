package app.northernlights.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioDownmixTest {
    private fun coef(m: FloatArray, input: Int, output: Int) = m[input * 2 + output]

    private fun outputSum(m: FloatArray, channels: Int, output: Int): Float {
        var sum = 0f
        for (i in 0 until channels) sum += coef(m, i, output)
        return sum
    }

    @Test
    fun onlyFiveOneAndSevenOneAreMixed() {
        assertNull(AudioDownmix.stereoMatrix(2))
        assertNull(AudioDownmix.stereoMatrix(4))
        assertNull(AudioDownmix.stereoMatrix(7))
        assertEquals(12, AudioDownmix.stereoMatrix(6)!!.size)
        assertEquals(16, AudioDownmix.stereoMatrix(8)!!.size)
    }

    @Test
    fun dialogueSitsAtMinusThreeDecibels() {
        for (channels in intArrayOf(6, 8)) {
            val m = AudioDownmix.stereoMatrix(channels)!!
            assertEquals(0.707f, coef(m, 2, 0), 1e-5f)
            assertEquals(0.707f, coef(m, 2, 1), 1e-5f)
        }
    }

    @Test
    fun bothSidesGetTheSameTotalGain() {
        for (channels in intArrayOf(6, 8)) {
            val m = AudioDownmix.stereoMatrix(channels)!!
            assertEquals(outputSum(m, channels, 0), outputSum(m, channels, 1), 1e-6f)
        }
    }

    @Test
    fun lfeIsDroppedAndCentreIsInBothSides() {
        for (channels in intArrayOf(6, 8)) {
            val m = AudioDownmix.stereoMatrix(channels)!!
            assertEquals(0f, coef(m, 3, 0), 0f)
            assertEquals(0f, coef(m, 3, 1), 0f)
            assertEquals(coef(m, 2, 0), coef(m, 2, 1), 0f)
            assertTrue(coef(m, 2, 0) > 0f)
        }
    }

    @Test
    fun dialogueIsAsLoudAsTheFrontChannelAndLouderThanTheSurrounds() {
        for (channels in intArrayOf(6, 8)) {
            val m = AudioDownmix.stereoMatrix(channels)!!
            assertEquals(coef(m, 0, 0), coef(m, 2, 0), 1e-6f)
            assertEquals(0.707f, coef(m, 4, 0) / coef(m, 2, 0), 1e-3f)
        }
    }

    @Test
    fun leftChannelsGoLeftAndRightChannelsGoRight() {
        val m = AudioDownmix.stereoMatrix(8)!!
        // Android order: FL, FR, FC, LFE, BL, BR, SL, SR
        for (left in intArrayOf(0, 4, 6)) {
            assertTrue(coef(m, left, 0) > 0f)
            assertEquals(0f, coef(m, left, 1), 0f)
        }
        for (right in intArrayOf(1, 5, 7)) {
            assertTrue(coef(m, right, 1) > 0f)
            assertEquals(0f, coef(m, right, 0), 0f)
        }
    }

    @Test
    fun fiveOneValuesFollowTheRecipe() {
        val m = AudioDownmix.stereoMatrix(6)!!
        val scale = AudioDownmix.LEVEL
        assertEquals(scale, coef(m, 0, 0), 1e-5f)
        assertEquals(scale, coef(m, 2, 0), 1e-5f)
        assertEquals(0.707f * scale, coef(m, 4, 0), 1e-5f)
    }

    @Test
    fun sevenOneAddsTheSideChannelsAtTheSameGain() {
        val m = AudioDownmix.stereoMatrix(8)!!
        assertEquals(coef(m, 4, 0), coef(m, 6, 0), 0f)
        val scale = AudioDownmix.LEVEL
        assertEquals(scale, coef(m, 0, 0), 1e-5f)
    }
}
