package app.northernlights.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdateCheckTest {
    @Test
    fun readsTheBuildFromVersionJson() {
        assertEquals(57L, UpdateCheck.versionCodeFrom("""{"versionCode": 57, "sha": "abc1234"}"""))
        assertEquals(3L, UpdateCheck.versionCodeFrom("""{"versionCode":3}"""))
    }

    @Test
    fun aBrokenVersionJsonGivesNoBuild() {
        assertNull(UpdateCheck.versionCodeFrom(""))
        assertNull(UpdateCheck.versionCodeFrom("<html>Not Found</html>"))
        assertNull(UpdateCheck.versionCodeFrom("""{"versionCode": "57"}"""))
    }

    @Test
    fun offersOnlyANewerBuild() {
        assertEquals(58L, UpdateCheck.newerBuild(58, 57))
        assertNull(UpdateCheck.newerBuild(57, 57))
        assertNull(UpdateCheck.newerBuild(40, 57))
        assertNull(UpdateCheck.newerBuild(null, 57))
    }

    @Test
    fun checksAtMostEverySixHours() {
        val six = UpdateCheck.CHECK_EVERY_MS
        assertTrue(UpdateCheck.shouldCheck(0, 1_000_000_000))
        assertFalse(UpdateCheck.shouldCheck(1_000, 1_000 + six - 1))
        assertTrue(UpdateCheck.shouldCheck(1_000, 1_000 + six))
        assertTrue(UpdateCheck.shouldCheck(5_000, 1_000)) // clock went backwards
    }

    @Test
    fun downloadsComeFromTheFixedRelease() {
        assertTrue(UpdateCheck.VERSION_URL.endsWith("/releases/download/tv-app/version.json"))
        assertTrue(UpdateCheck.APK_URL.endsWith("/releases/download/tv-app/northern-lights-tv.apk"))
    }
}
