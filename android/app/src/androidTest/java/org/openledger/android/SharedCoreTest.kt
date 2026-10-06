package org.openledger.android

import android.graphics.Bitmap
import android.graphics.pdf.PdfRenderer
import android.os.ParcelFileDescriptor
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
    fun portableBackupRestoresWithoutReplacingSource() {
        val (service, folder) = service()
        cash(service)
        assertTrue(call(service, "record", record(service)).getBoolean("ok"))
        val backup = call(service, "backup_create", JSONObject().put("filename", "test.olbackup"))
        if (!backup.getBoolean("ok")) {
            val builtins = Python.getInstance().getModule("builtins")
            val scope = builtins.callAttr("dict")
            scope.callAttr("__setitem__", "files", service.get("files"))
            builtins.callAttr(
                "exec",
                "try:\n    files.backup('diagnostic.olbackup')\nexcept Exception as error:\n    raise error.__cause__ or error",
                scope,
            )
        }
        assertTrue(backup.toString(), backup.getBoolean("ok"))
        val preserved =
            call(service, "backup_prepare", JSONObject().put("filename", "test.olbackup"))
        assertTrue(preserved.toString(), preserved.getBoolean("ok"))
        assertTrue(File(folder, "staging/test.olbackup").delete())
        val body =
            JSONObject()
                .put("filename", "test.olbackup")
                .put("request_id", UUID.randomUUID().toString())
        val restored = call(service, "backup_restore", body)
        assertTrue(restored.toString(), restored.getBoolean("ok"))
        assertEquals(restored.toString(), call(service, "backup_restore", body).toString())
        val (other, _) =
            this.service(
                File(folder.parentFile, restored.getJSONObject("data").getString("directory"))
            )
        assertEquals(
            97500L,
            call(other, "snapshot")
                .getJSONObject("data")
                .getJSONObject("overview")
                .getLong("total_assets_minor"),
        )
        assertTrue(File(folder, "database/openledger.sqlite3").isFile)
    }

    @Test
    fun transferRefundAndDeletedRecoveryKeepExactBalances() {
        val (service, _) = service()
        val first = cash(service)
        val today = call(service, "snapshot").getJSONObject("data").getString("today")
        val second =
            call(
                    service,
                    "create_account",
                    JSONObject()
                        .put("request_id", UUID.randomUUID().toString())
                        .put("name", "合成银行")
                        .put("account_type", "bank")
                        .put("opening_amount", "0")
                        .put("balance_start_on", today),
                )
                .getJSONObject("data")
                .getJSONObject("data")
                .getString("id")
        fun mutate(command: String, payload: JSONObject): JSONObject =
            call(
                service,
                "mutate",
                JSONObject()
                    .put("request_id", UUID.randomUUID().toString())
                    .put("command", command)
                    .put("payload", payload),
            )
        val transfer =
            mutate(
                "transfer.record.v1",
                JSONObject()
                    .put("id", UUID.randomUUID().toString())
                    .put(
                        "fields",
                        JSONObject()
                            .put("from_account_id", first)
                            .put("to_account_id", second)
                            .put("amount", "100.00")
                            .put("occurred_on", today)
                            .put("time_zone", "Asia/Shanghai"),
                    ),
            )
        assertTrue(transfer.toString(), transfer.getBoolean("ok"))
        val recorded = call(service, "record", record(service))
        val id = recorded.getJSONObject("data").getJSONObject("data").getString("id")
        val refund =
            mutate(
                "refund.record.v1",
                JSONObject()
                    .put("id", UUID.randomUUID().toString())
                    .put(
                        "fields",
                        JSONObject()
                            .put("original_transaction_id", id)
                            .put("account_id", second)
                            .put("amount", "10.00")
                            .put("occurred_on", today)
                            .put("time_zone", "Asia/Shanghai"),
                    ),
            )
        assertTrue(refund.toString(), refund.getBoolean("ok"))
        val overview = call(service, "snapshot").getJSONObject("data").getJSONObject("overview")
        assertEquals(98500L, overview.getLong("total_assets_minor"))
        assertEquals(1500L, overview.getLong("expense_minor"))
        val detail =
            call(service, "transaction_detail", JSONObject().put("id", id)).getJSONObject("data")
        val blocked =
            mutate(
                "transaction.delete.v1",
                JSONObject().put("id", id).put("expected_version", detail.getInt("version")),
            )
        assertEquals(
            "ACTIVE_REFUNDS_BLOCK_OPERATION",
            blocked.getJSONObject("error").getString("code"),
        )
    }

    @Test
    fun reportsAndSpreadsheetsRunInThePackagedRuntime() {
        val (service, folder) = service()
        cash(service)
        assertTrue(call(service, "record", record(service)).getBoolean("ok"))
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val today = call(service, "snapshot").getJSONObject("data").getString("today")
        val data =
            call(
                service,
                "analytics",
                JSONObject().put("start_on", today.take(7) + "-01").put("end_on", today),
            )
        assertTrue(data.toString(), data.getBoolean("ok"))
        val report = data.getJSONObject("data")
        assertEquals("2500", report.getJSONObject("totals").getString("net_expense_minor"))
        assertEquals("9223372036854775807.01", ReportRenderer.money("922337203685477580701"))
        for (format in listOf("csv", "xlsx")) {
            val exported =
                call(
                    service,
                    "export_transactions",
                    JSONObject().put("filename", "runtime.$format").put("format", format),
                )
            assertTrue(exported.toString(), exported.getBoolean("ok"))
            assertEquals(2, exported.getJSONObject("data").getInt("row_count"))
            val headers =
                call(service, "import_headers", JSONObject().put("filename", "runtime.$format"))
            assertTrue(headers.toString(), headers.getBoolean("ok"))
            assertEquals(2, headers.getJSONObject("data").getInt("row_count"))
        }
        val renderer = ReportRenderer(context)
        val pdf = renderer.export(report, "pdf", folder)
        assertTrue(
            pdf.readBytes().take(5).toByteArray().toString(Charsets.US_ASCII).startsWith("%PDF-")
        )
        val descriptor = ParcelFileDescriptor.open(pdf, ParcelFileDescriptor.MODE_READ_ONLY)
        PdfRenderer(descriptor).use { reader ->
            assertTrue(reader.pageCount > 0)
            reader.openPage(0).use { page ->
                val bitmap = Bitmap.createBitmap(595, 842, Bitmap.Config.ARGB_8888)
                page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                File(context.cacheDir, "phase11-report-page.png").outputStream().use {
                    bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)
                }
                bitmap.recycle()
            }
        }
        val png = renderer.export(report, "png", folder)
        assertTrue(png.length() > 1000)
        File(context.cacheDir, "phase11-report.pdf").writeBytes(pdf.readBytes())
        File(context.cacheDir, "phase11-report.png").writeBytes(png.readBytes())
    }

    @Test
    fun firstStartCreatesNoFakeMoney() {
        val (service, _) = service()
        val data = call(service, "snapshot").getJSONObject("data")
        assertEquals(1, data.getInt("schema_version"))
        assertEquals(0, data.getJSONArray("accounts").length())
        assertEquals(0L, data.getJSONObject("overview").getLong("total_assets_minor"))
    }

    @Test
    fun refundOnlyPeriodHasNoShareAndExportsWithoutFloatingPoint() {
        val (service, folder) = service()
        val account = cash(service)
        val today = call(service, "snapshot").getJSONObject("data").getString("today")
        val datetime = Python.getInstance().getModule("datetime")
        val yesterday =
            datetime
                .get("date")!!
                .callAttr("fromisoformat", today)
                .callAttr("__sub__", datetime.get("timedelta")!!.call(1))
                .callAttr("isoformat")
                .toString()
        val opening =
            call(
                service,
                "mutate",
                JSONObject()
                    .put("request_id", UUID.randomUUID().toString())
                    .put("command", "account.opening.set.v1")
                    .put(
                        "payload",
                        JSONObject()
                            .put("account_id", account)
                            .put("expected_account_version", 1)
                            .put("opening_amount", "1000")
                            .put("balance_start_on", yesterday),
                    ),
            )
        assertTrue(opening.toString(), opening.getBoolean("ok"))
        val expense = record(service)
        expense.getJSONObject("fields").put("occurred_on", yesterday)
        val saved = call(service, "record", expense)
        assertTrue(saved.toString(), saved.getBoolean("ok"))
        val original = saved.getJSONObject("data").getJSONObject("data").getString("id")
        val refund =
            call(
                service,
                "mutate",
                JSONObject()
                    .put("request_id", UUID.randomUUID().toString())
                    .put("command", "refund.record.v1")
                    .put(
                        "payload",
                        JSONObject()
                            .put("id", UUID.randomUUID().toString())
                            .put(
                                "fields",
                                JSONObject()
                                    .put("original_transaction_id", original)
                                    .put("account_id", account)
                                    .put("amount", "5.00")
                                    .put("occurred_on", today)
                                    .put("time_zone", "Asia/Shanghai"),
                            ),
                    ),
            )
        assertTrue(refund.toString(), refund.getBoolean("ok"))
        val report =
            call(service, "analytics", JSONObject().put("start_on", today).put("end_on", today))
                .getJSONObject("data")
        assertEquals("-500", report.getJSONObject("totals").getString("net_expense_minor"))
        val category = report.getJSONArray("categories").getJSONObject(0)
        assertTrue(category.isNull("share"))
        assertEquals("—", ReportRenderer.share(category))
        val context =
            AppPreferences.context(InstrumentationRegistry.getInstrumentation().targetContext)
        for (format in listOf("pdf", "png")) assertTrue(
            ReportRenderer(context).export(report, format, folder).length() > 1000
        )
    }
}
