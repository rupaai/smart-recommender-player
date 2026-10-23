package app.northernlights.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ServerConfigTest {
    @Test
    fun readsTheVersionFromHealthz() {
        val body = """{"ok": true, "git_sha": "abc1234", "version": "v0.4.0-dev.2", "model_bundle": null, "db_schema": 21}"""
        assertEquals("v0.4.0-dev.2", ServerConfig.versionFrom(body))
    }

    @Test
    fun aNullVersionGivesNoVersion() {
        assertNull(ServerConfig.versionFrom("""{"ok": true, "git_sha": "dev", "version": null}"""))
    }

    @Test
    fun aMissingVersionFieldGivesNoVersion() {
        assertNull(ServerConfig.versionFrom("""{"ok": true, "git_sha": "dev"}"""))
        assertNull(ServerConfig.versionFrom("not json at all"))
    }
}
