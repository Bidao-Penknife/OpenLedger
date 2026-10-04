package org.openledger.android

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.util.UUID
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

/** Tests execute inside the APK's actual Python/SQLite runtime on a device. */
@RunWith(AndroidJUnit4::class)
class SharedCoreTest {
    private fun service(directory: File? = null): Pair<PyObject, File> {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        if (!Python.isStarted()) Python.start(AndroidPlatform(context))
        val folder = directory ?: File(context.cacheDir, "core-test-${UUID.randomUUID()}")
        val value =
            Python.getInstance()
                .getModule("openledger.mobile.bridge")
                .callAttr("MobileLedger", folder.absolutePath)
        return value to folder
    }

    private fun call(
        service: PyObject,
        action: String,
        body: JSONObject = JSONObject(),
    ): JSONObject {
        val request = JSONObject().put("api_version", 1).put("action", action).put("body", body)
        return JSONObject(service.callAttr("call", request.toString()).toString())
    }

    private fun cash(service: PyObject): String {
        val today = call(service, "snapshot").getJSONObject("data").getString("today")
        val reply =
            call(
                service,
                "create_account",
                JSONObject()
                    .put("request_id", UUID.randomUUID().toString())
                    .put("name", "合成现金")
                    .put("account_type", "cash")
                    .put("opening_amount", "1000.00")
                    .put("balance_start_on", today),
            )
        assertTrue(reply.toString(), reply.getBoolean("ok"))
        return reply.getJSONObject("data").getJSONObject("data").getString("id")
    }

    private fun record(service: PyObject): JSONObject {
        val data = call(service, "snapshot").getJSONObject("data")
        val categories = data.getJSONArray("categories")
        val category =
            (0 until categories.length())
                .map { categories.getJSONObject(it) }
                .first { it.getString("transaction_kind") == "expense" }
                .getString("id")
        val fields =
            JSONObject()
                .put("kind", "expense")
                .put("amount", "25.00")
                .put("account_id", data.getJSONArray("accounts").getJSONObject(0).getString("id"))
                .put("book_id", data.getJSONArray("books").getJSONObject(0).getString("id"))
                .put("category_id", category)
                .put("occurred_on", data.getString("today"))
                .put("note", "合成咖啡")
        return JSONObject().put("request_id", UUID.randomUUID().toString()).put("fields", fields)
    }

    @Test
    fun parserIsReadOnlyAndSqlResourcesWork() {
        val (service, _) = service()
        cash(service)
        val before = call(service, "snapshot").getJSONObject("data").getJSONObject("overview")
        val result = call(service, "preview", JSONObject().put("text", "咖啡25元"))
        assertTrue(result.toString(), result.getBoolean("ok"))
        val draft = result.getJSONObject("data").getJSONArray("drafts").getJSONObject(0)
        assertEquals(2500L, draft.getJSONObject("amount_minor").getLong("value"))
        val after = call(service, "snapshot").getJSONObject("data").getJSONObject("overview")
        assertEquals(before.getLong("change_seq"), after.getLong("change_seq"))
        assertEquals(100000L, after.getLong("total_assets_minor"))
    }

    @Test
    fun confirmedRetryAndReopenDebitOnce() {
        val (service, folder) = service()
        cash(service)
        val request = record(service)
        assertTrue(call(service, "record", request).getBoolean("ok"))
        val (reopened, _) = this.service(folder)
        val replay = call(reopened, "record", request)
        assertTrue(replay.toString(), replay.getBoolean("ok"))
        assertTrue(replay.getJSONObject("data").getBoolean("replayed"))
        assertEquals(
            97500L,
            call(reopened, "snapshot")
                .getJSONObject("data")
                .getJSONObject("overview")
                .getLong("total_assets_minor"),
        )
    }

    @Test
    fun invalidAmountDoesNotChangeBalance() {
        val (service, _) = service()
        cash(service)
        val request = record(service)
        request.getJSONObject("fields").put("amount", "25.001")
        val reply = call(service, "record", request)
        assertFalse(reply.getBoolean("ok"))
        assertEquals("AMOUNT_PRECISION", reply.getJSONObject("error").getString("code"))
        assertEquals(
            100000L,
            call(service, "snapshot")
                .getJSONObject("data")
                .getJSONObject("overview")
                .getLong("total_assets_minor"),
        )
    }

    @Test
    fun firstStartCreatesNoFakeMoney() {
        val (service, _) = service()
        val data = call(service, "snapshot").getJSONObject("data")
        assertEquals(1, data.getInt("schema_version"))
        assertEquals(0, data.getJSONArray("accounts").length())
        assertEquals(0L, data.getJSONObject("overview").getLong("total_assets_minor"))
    }
}
