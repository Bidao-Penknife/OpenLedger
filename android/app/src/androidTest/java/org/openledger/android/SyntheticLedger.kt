package org.openledger.android

import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.Kwarg
import com.chaquo.python.Python
import java.io.File
import java.util.UUID
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue

/** Opt-in fixture: restore an empty synthetic backup and always return to the original. */
class SyntheticLedger : AutoCloseable {
    val application =
        InstrumentationRegistry.getInstrumentation().targetContext.applicationContext
            as LedgerApplication
    private val original: String
    private val overview: String

    init {
        assumeTrue(
            "Use only an owned synthetic emulator",
            InstrumentationRegistry.getArguments().getString("synthetic_device") == "true",
        )
        assertNull(application.pending())
        original = application.directory
        overview =
            application.call("snapshot").getJSONObject("data").getJSONObject("overview").toString()
        val folder = File(application.cacheDir, "ui-fixture-${UUID.randomUUID()}")
        val mobile =
            Python.getInstance()
                .getModule("openledger.mobile.bridge")
                .callAttr(
                    "MobileLedger",
                    folder.absolutePath,
                    Kwarg("staging_directory", application.staging.absolutePath),
                )
        val filename = "ui-fixture-${UUID.randomUUID()}.olbackup"
        val request =
            JSONObject()
                .put("api_version", 1)
                .put("action", "backup_create")
                .put("body", JSONObject().put("filename", filename))
        val backup = JSONObject(mobile.callAttr("call", request.toString()).toString())
        assertTrue(backup.toString(), backup.getBoolean("ok"))
        val prepared = application.call("backup_prepare", JSONObject().put("filename", filename))
        assertTrue(prepared.toString(), prepared.getBoolean("ok"))
        val restored =
            application.confirmed(
                "backup_restore",
                JSONObject()
                    .put("filename", filename)
                    .put("request_id", UUID.randomUUID().toString()),
            )
        assertTrue(restored.toString(), restored.getBoolean("ok"))
        assertNotEquals(original, application.directory)
    }

    override fun close() {
        assertNull(application.pending())
        application.switchDirectory(original)
        assertEquals(
            overview,
            application.call("snapshot").getJSONObject("data").getJSONObject("overview").toString(),
        )
    }
}
