package org.openledger.android

import android.content.Context
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.util.UUID
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

/** Exercise Android Keystore and bundled TLS in the actual APK runtime. */
@RunWith(AndroidJUnit4::class)
class SecureSettingsTest {
    @Test
    fun secretsAreEncryptedScopedAndRemovable() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = SecureSecrets(context)
        val directory = "synthetic-${UUID.randomUUID()}"
        val endpoint = "https://example.test/v1"
        val key = "synthetic-secret-${UUID.randomUUID()}"
        try {
            store.set(directory, endpoint, key)
            assertEquals(key, store.get(directory, endpoint))
            assertNull(store.get(directory + "-other", endpoint))
            assertNull(store.get(directory, "https://other.test/v1"))
            val saved =
                context.getSharedPreferences("encrypted-ai-credentials", Context.MODE_PRIVATE).all
            assertTrue(saved.values.none { it.toString().contains(key) })
            store.set(directory, endpoint, key)
            assertNotEquals(
                saved,
                context.getSharedPreferences("encrypted-ai-credentials", Context.MODE_PRIVATE).all,
            )
        } finally {
            store.delete(directory, endpoint)
        }
        assertNull(store.get(directory, endpoint))
    }

    @Test
    fun tlsHasTrustedRootsAndAiConfigImportsWithoutWindowsCode() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        if (!Python.isStarted()) Python.start(AndroidPlatform(context))
        val python = Python.getInstance()
        assertTrue(
            python
                .getModule("ssl")
                .callAttr("create_default_context")
                .callAttr("get_ca_certs")
                .asList()
                .size > 100
        )
        val directory = java.io.File(context.cacheDir, "config-test-${UUID.randomUUID()}")
        val service =
            python
                .getModule("openledger.mobile.bridge")
                .callAttr("MobileLedger", directory.absolutePath)
        val request =
            JSONObject()
                .put("api_version", 1)
                .put("action", "validate_ai_config")
                .put(
                    "body",
                    JSONObject()
                        .put(
                            "config",
                            JSONObject()
                                .put("enabled", true)
                                .put("base_url", "https://example.test/v1")
                                .put("model", "synthetic"),
                        ),
                )
        val reply = JSONObject(service.callAttr("call", request.toString()).toString())
        assertTrue(reply.toString(), reply.getBoolean("ok"))
        assertEquals(
            "False",
            python
                .getModule("sys")
                .get("modules")!!
                .callAttr("__contains__", "openledger.infrastructure.credentials")
                .toString(),
        )
    }
}
