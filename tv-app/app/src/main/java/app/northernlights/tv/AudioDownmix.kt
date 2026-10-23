package app.northernlights.tv

/**
 * Stereo downmix for the TV's own speakers (ITU-R BS.775 recipe, dialogue kept strong).
 *
 * Channel order is Android/Media3 order (the order AudioTrack and the FFmpeg decoder deliver samples):
 *   5.1: FL, FR, FC, LFE, BL, BR            7.1: FL, FR, FC, LFE, BL, BR, SL, SR
 * Output channels: 0 = left, 1 = right. The LFE is dropped.
 */
object AudioDownmix {
    private const val CENTER = 1.0f
    private const val SURROUND = 0.707f

    /**
     * Row-major coefficients for Media3's ChannelMixingMatrix: `coefficients[input * 2 + output]`. Null when
     * [inputChannelCount] is not 6 or 8 (other layouts are left as they are).
     *   L' = L + 1.0*C + 0.707*BL (+ 0.707*SL for 7.1), R' likewise.
     * The whole matrix is then scaled so the larger of the two output sums is 1.0, so a full-scale signal can never
     * clip. No other gain is applied.
     */
    fun stereoMatrix(inputChannelCount: Int): FloatArray? {
        if (inputChannelCount != 6 && inputChannelCount != 8) return null
        val m = FloatArray(inputChannelCount * 2)
        fun put(input: Int, output: Int, gain: Float) {
            m[input * 2 + output] = gain
        }
        put(0, 0, 1.0f) // FL -> L
        put(1, 1, 1.0f) // FR -> R
        put(2, 0, CENTER) // FC -> both
        put(2, 1, CENTER)
        // channel 3 is the LFE: dropped
        put(4, 0, SURROUND) // BL -> L
        put(5, 1, SURROUND) // BR -> R
        if (inputChannelCount == 8) {
            put(6, 0, SURROUND) // SL -> L
            put(7, 1, SURROUND) // SR -> R
        }
        var worst = 0f
        for (output in 0..1) {
            var sum = 0f
            for (input in 0 until inputChannelCount) sum += m[input * 2 + output]
            if (sum > worst) worst = sum
        }
        for (i in m.indices) m[i] = m[i] / worst
        return m
    }
}
