package app.northernlights.tv

/**
 * Pure rules for the self-update (no Android calls, so JUnit can test them).
 * CI puts the newest main build on the GitHub release `tv-app`, next to a small version.json
 * like {"versionCode": 57}. versionCode is the CI run number, so a bigger number is a newer build.
 */
object UpdateCheck {
    private const val BASE = "https://github.com/rupaai/smart-recommender-player/releases/download/tv-app"
    const val VERSION_URL = "$BASE/version.json"
    const val APK_URL = "$BASE/northern-lights-tv.apk"

    /** Ask GitHub at most this often when the app comes to the front (the Settings button always asks). */
    const val CHECK_EVERY_MS = 6L * 60 * 60 * 1000

    private val VERSION_CODE = Regex("\"versionCode\"\\s*:\\s*(\\d+)")

    /** The versionCode in a version.json body, or null when it can't be read. */
    fun versionCodeFrom(body: String): Long? = VERSION_CODE.find(body)?.groupValues?.get(1)?.toLongOrNull()

    /** The build to offer: the published one when it is newer than the installed one, else null. */
    fun newerBuild(published: Long?, installed: Long): Long? =
        if (published != null && published > installed) published else null

    /** True when the last automatic check is old enough (or the clock went backwards). */
    fun shouldCheck(lastCheckAt: Long, now: Long): Boolean =
        now < lastCheckAt || now - lastCheckAt >= CHECK_EVERY_MS
}
