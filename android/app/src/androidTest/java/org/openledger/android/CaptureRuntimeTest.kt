package org.openledger.android

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions
import java.io.File
import java.util.UUID
import java.util.concurrent.TimeUnit
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

/** Exercise the actual bundled model and ledger inside the APK on an offline device. */
@RunWith(AndroidJUnit4::class)
class CaptureRuntimeTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    private fun service(): Pair<PyObject, File> {
        if (!Python.isStarted()) Python.start(AndroidPlatform(context))
        val folder = File(context.cacheDir, "phase12-core-${UUID.randomUUID()}")
        return Python.getInstance()
            .getModule("openledger.mobile.bridge")
            .callAttr("MobileLedger", folder.absolutePath) to folder
    }

    private fun call(
        service: PyObject,
        action: String,
        body: JSONObject = JSONObject(),
    ): JSONObject {
        val reply =
            JSONObject(
                service
                    .callAttr(
                        "call",
                        JSONObject()
                            .put("api_version", 1)
                            .put("action", action)
                            .put("body", body)
                            .toString(),
                    )
                    .toString()
            )
        assertTrue(reply.toString(), reply.getBoolean("ok"))
        return reply.getJSONObject("data")
    }

    private fun account(service: PyObject, code: String = "CNY", opening: String = "1000"): String {
        val today = call(service, "snapshot").getString("today")
        return call(
                service,
                "create_account",
                JSONObject()
                    .put("request_id", UUID.randomUUID().toString())
                    .put("name", "合成$code")
                    .put("account_type", "cash")
                    .put("currency_code", code)
                    .put("opening_amount", opening)
                    .put("balance_start_on", today),
            )
            .getJSONObject("data")
            .getString("id")
    }

    @Test
    fun bundledChineseOcrWorksFirstUseOfflineAndBacksUpItsOriginal() {
        val (service, folder) = service()
        val account = account(service)
        val bitmap = receipt()
        try {
            OcrRuntime.initialize(context)
            OcrRuntime.initialize(context) // Repeat use in the same process must remain safe.
            val recognizer =
                TextRecognition.getClient(ChineseTextRecognizerOptions.Builder().build())
            val text =
                try {
                    Tasks.await(
                            recognizer.process(InputImage.fromBitmap(bitmap, 0)),
                            45,
                            TimeUnit.SECONDS,
                        )
                        .text
                } finally {
                    recognizer.close()
                }
            assertTrue(text, text.contains("实付"))
            assertTrue(text, text.contains("128") && text.contains("28") && text.contains("100"))
            val file = File(folder, "staging/receipt.png")
            file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
            val captured =
                call(
                        service,
                        "capture_stage",
                        JSONObject()
                            .put("request_id", UUID.randomUUID().toString())
                            .put("source_kind", "ocr")
                            .put("source_label", "合成收据")
                            .put("event_key", "receipt")
                            .put("text", text)
                            .put("filename", file.name),
                    )
                    .getJSONObject("data")
            val capture =
                call(service, "capture_detail", JSONObject().put("id", captured.getString("id")))
            val values = capture.getJSONObject("suggested").getJSONArray("amount_candidates")
            val amounts =
                (0 until values.length()).map { values.getJSONObject(it).getLong("amount_minor") }
            assertTrue(amounts.toString(), amounts.containsAll(listOf(12800L, 2800L, 10000L)))
            val snapshot = call(service, "snapshot")
            assertEquals(100000L, snapshot.getJSONObject("overview").getLong("total_assets_minor"))
            val categories = snapshot.getJSONArray("categories")
            val category =
                (0 until categories.length())
                    .map { categories.getJSONObject(it) }
                    .first { it.getString("transaction_kind") == "expense" }
            val fields =
                JSONObject()
                    .put("kind", "expense")
                    .put("amount", "100.00")
                    .put("account_id", account)
                    .put("book_id", snapshot.getJSONArray("books").getJSONObject(0).getString("id"))
                    .put("category_id", category.getString("id"))
                    .put("occurred_on", snapshot.getString("today"))
            val body =
                JSONObject()
                    .put("request_id", UUID.randomUUID().toString())
                    .put("id", capture.getString("id"))
                    .put("expected_version", 1)
                    .put("fields", fields)
            val saved = call(service, "capture_record", body)
            assertTrue(call(service, "capture_record", body).getBoolean("replayed"))
            assertEquals(
                90000L,
                call(service, "snapshot").getJSONObject("overview").getLong("total_assets_minor"),
            )
            val transaction = saved.getJSONObject("data").getString("id")
            assertEquals(
                1,
                call(service, "transaction_detail", JSONObject().put("id", transaction))
                    .getJSONArray("attachments")
                    .length(),
            )
            val backup = "receipt-${UUID.randomUUID()}.olbackup"
            call(service, "backup_create", JSONObject().put("filename", backup))
            call(service, "backup_prepare", JSONObject().put("filename", backup))
            val restored =
                call(
                    service,
                    "backup_restore",
                    JSONObject()
                        .put("request_id", UUID.randomUUID().toString())
                        .put("filename", backup),
                )
            assertTrue(
                File(
                        folder.parentFile,
                        restored.getString("directory") +
                            "/attachments/${capture.getString("image_relative_path")}",
                    )
                    .isFile
            )
        } finally {
            bitmap.recycle()
        }
    }

    @Test
    fun nativeCurrencyPrecisionAndFxValuationAreExact() {
        val (service, _) = service()
        val yen = account(service, "JPY", "5000")
        val cash = account(service, "CNY", "0")
        assertEquals("5000", ReportRenderer.money("5000", "JPY"))
        assertEquals("1.234", ReportRenderer.money("1234", "KWD"))
        var snapshot = call(service, "snapshot")
        assertFalse(snapshot.getJSONObject("overview").getBoolean("assets_complete"))
        call(
            service,
            "mutate",
            JSONObject()
                .put("request_id", UUID.randomUUID().toString())
                .put("command", "rate.set.v1")
                .put(
                    "payload",
                    JSONObject()
                        .put("currency_code", "JPY")
                        .put("rate_text", "0.05")
                        .put("effective_on", snapshot.getString("today")),
                ),
        )
        call(
            service,
            "mutate",
            JSONObject()
                .put("request_id", UUID.randomUUID().toString())
                .put("command", "transfer.record.v1")
                .put(
                    "payload",
                    JSONObject()
                        .put("id", UUID.randomUUID().toString())
                        .put(
                            "fields",
                            JSONObject()
                                .put("from_account_id", yen)
                                .put("to_account_id", cash)
                                .put("amount", "1000")
                                .put("to_amount", "48.00")
                                .put("occurred_on", snapshot.getString("today")),
                        ),
                ),
        )
        snapshot = call(service, "snapshot")
        val overview = snapshot.getJSONObject("overview")
        assertEquals(4000L, overview.getJSONObject("balances").getLong(yen))
        assertEquals(4800L, overview.getJSONObject("balances").getLong(cash))
        assertEquals(24800L, overview.getLong("total_assets_minor"))
        assertEquals(0L, overview.getLong("expense_minor"))
    }

    @Test
    fun notificationsAndChineseVoiceRemainDraftsAcrossRestart() {
        val (service, folder) = service()
        account(service)
        val body =
            JSONObject()
                .put("request_id", UUID.randomUUID().toString())
                .put("source_kind", "notification")
                .put("source_label", "合成支付通知")
                .put("event_key", "event-1")
                .put("text", "支付成功，实付35元")
        val first = call(service, "capture_stage", body).getJSONObject("data").getString("id")
        body.put("request_id", UUID.randomUUID().toString()).put("text", "支付成功，商户：合成餐厅，实付35元")
        assertEquals(
            first,
            call(service, "capture_stage", body).getJSONObject("data").getString("id"),
        )
        body
            .put("request_id", UUID.randomUUID().toString())
            .put("source_kind", "voice")
            .put("event_key", UUID.randomUUID().toString())
            .put("text", "今天午饭花了三十五元，微信支付")
        val voice = call(service, "capture_stage", body).getJSONObject("data").getString("id")
        val reopened =
            Python.getInstance()
                .getModule("openledger.mobile.bridge")
                .callAttr("MobileLedger", folder.absolutePath)
        assertEquals(2, call(reopened, "capture_list").getInt("total"))
        assertEquals(
            3500L,
            call(reopened, "capture_detail", JSONObject().put("id", voice))
                .getJSONObject("suggested")
                .getJSONArray("amount_candidates")
                .getJSONObject(0)
                .getLong("amount_minor"),
        )
        assertEquals(
            100000L,
            call(reopened, "snapshot").getJSONObject("overview").getLong("total_assets_minor"),
        )
        assertTrue(PaymentNotificationListener.eligible("支付成功，实付35元"))
        assertFalse(PaymentNotificationListener.eligible("待付款35元"))
        assertFalse(PaymentNotificationListener.eligible("支付失败35元"))
    }

    companion object {
        fun receipt(): Bitmap {
            val bitmap = Bitmap.createBitmap(1000, 680, Bitmap.Config.ARGB_8888)
            val canvas = Canvas(bitmap)
            canvas.drawColor(Color.WHITE)
            val paint =
                Paint(Paint.ANTI_ALIAS_FLAG).apply {
                    color = Color.BLACK
                    textSize = 56f
                }
            listOf("午饭收据（合成）", "商户：合成餐厅", "原价 128元", "优惠 28元", "实付 100元").forEachIndexed {
                index,
                line ->
                canvas.drawText(line, 70f, 100f + index * 110f, paint)
            }
            return bitmap
        }
    }
}
