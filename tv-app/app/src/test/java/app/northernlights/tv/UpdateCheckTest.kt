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
    fun laterSkipsOnlyThatBuild() {
        assertTrue(UpdateCheck.shouldOffer(97, 0)) // never pressed Later
        assertFalse(UpdateCheck.shouldOffer(97, 97))
        assertTrue(UpdateCheck.shouldOffer(98, 97))
    }

    @Test
    fun downloadsComeFromTheFixedRelease() {
        assertTrue(UpdateCheck.VERSION_URL.endsWith("/releases/download/tv-app/version.json"))
        assertTrue(UpdateCheck.APK_URL.endsWith("/releases/download/tv-app/northern-lights-tv.apk"))
    }
}
