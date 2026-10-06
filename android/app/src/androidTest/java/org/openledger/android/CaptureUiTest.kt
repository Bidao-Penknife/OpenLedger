package org.openledger.android

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.speech.SpeechRecognizer
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.Until
import java.io.File
import java.util.UUID
import java.util.regex.Pattern
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

/** Real mobile screens, amount selection, confirmation, offline help and capture permissions. */
@RunWith(AndroidJUnit4::class)
class CaptureUiTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val originalContext = instrumentation.targetContext
    private val app
        get() = originalContext.applicationContext as LedgerApplication

    private val device
        get() = UiDevice.getInstance(instrumentation)

    private val context
        get() = AppPreferences.context(originalContext)

    private fun click(title: Int) {
        val text = context.getString(title)
        for (attempt in 0..28) {
            val view = device.findObject(By.text(text))
            if (view != null) {
                view.click()
                return
            }
            device.swipe(
                device.displayWidth / 2,
                if (attempt < 14) device.displayHeight * 4 / 5 else device.displayHeight / 4,
                device.displayWidth / 2,
                if (attempt < 14) device.displayHeight / 4 else device.displayHeight * 4 / 5,
                20,
            )
            device.waitForIdle()
        }
        device.dumpWindowHierarchy(File(context.cacheDir, "phase12-ui-failure.xml"))
        image("ui-failure")
        fail("Missing control $text")
    }

    private fun image(name: String) {
        instrumentation.waitForIdleSync()
        assertTrue(device.takeScreenshot(File(context.cacheDir, "phase12-$name.png")))
    }

    private fun ready() {
        app.worker.submit {}.get(30, java.util.concurrent.TimeUnit.SECONDS)
        instrumentation.waitForIdleSync()
    }

    @Test
    fun ocrAmountPickerSavesOneExpenseAndManualIsOffline() {
        val preferences = originalContext.getSharedPreferences("presentation", Context.MODE_PRIVATE)
        val originalLanguage = preferences.getString("language", "system")
        val originalTheme = preferences.getString("theme", "system")
        assertTrue(
            preferences.edit().putString("language", "zh").putString("theme", "light").commit()
        )
        try {
            SyntheticLedger().use {
                val today = app.call("snapshot").getJSONObject("data").getString("today")
                val account =
                    app.confirmed(
                        "create_account",
                        JSONObject()
                            .put("request_id", UUID.randomUUID().toString())
                            .put("name", "现金（合成）")
                            .put("account_type", "cash")
                            .put("opening_amount", "1000")
                            .put("balance_start_on", today),
                    )
                assertTrue(account.toString(), account.getBoolean("ok"))
                val file = File(app.staging, "ui-receipt-${UUID.randomUUID()}.png")
                val bitmap = CaptureRuntimeTest.receipt()
                file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
                bitmap.recycle()
                ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                    assertTrue(
                        device.wait(
                            Until.hasObject(By.res(context.packageName, "quick_input")),
                            15000,
                        )
                    )
                    ready()
                    image("quick")
                    scenario.onActivity { it.captures.ocr.imported(file.name) }
                    assertTrue(
                        device.wait(
                            Until.hasObject(By.text(context.getString(R.string.capture_confirm))),
                            45000,
                        )
                    )
                    image("capture")
                    assertEquals(
                        100000L,
                        app.call("snapshot")
                            .getJSONObject("data")
                            .getJSONObject("overview")
                            .getLong("total_assets_minor"),
                    )
                    click(R.string.capture_confirm)
                    assertTrue(
                        device.wait(
                            Until.hasObject(
                                By.text(context.getString(R.string.capture_choose_amount))
                            ),
                            10000,
                        )
                    )
                    image("amounts")
                    val actual = device.wait(Until.findObject(By.textContains("100.00")), 10000)
                    assertNotNull(actual)
                    actual!!.click()
                    assertTrue(
                        device.wait(
                            Until.hasObject(By.text(context.getString(R.string.transaction_type))),
                            10000,
                        )
                    )
                    click(R.string.expense)
                    assertTrue(
                        device.wait(
                            Until.hasObject(By.res(context.packageName, "save_button")),
                            10000,
                        )
                    )
                    device.findObject(By.res(context.packageName, "save_button")).click()
                    assertTrue(
                        device.wait(Until.gone(By.res(context.packageName, "save_button")), 15000)
                    )
                    ready()
                    assertEquals(
                        90000L,
                        app.call("snapshot")
                            .getJSONObject("data")
                            .getJSONObject("overview")
                            .getLong("total_assets_minor"),
                    )
                    assertEquals(
                        1,
                        app.call("capture_list", JSONObject().put("state", "saved"))
                            .getJSONObject("data")
                            .getInt("total"),
                    )
                    scenario.onActivity { it.captures.voice.start() }
                    if (SpeechRecognizer.isRecognitionAvailable(context)) {
                        val deny =
                            device.wait(
                                Until.findObject(
                                    By.res(Pattern.compile(".*:id/permission_deny.*"))
                                ),
                                10000,
                            )
                        deny?.click()
                    }
                    device.findObject(By.text(context.getString(R.string.voice_text)))?.click()
                    if (
                        !device.wait(
                            Until.hasObject(By.text(context.getString(R.string.voice_review))),
                            10000,
                        )
                    ) {
                        device.dumpWindowHierarchy(File(context.cacheDir, "phase12-ui-failure.xml"))
                        image("ui-failure")
                    }
                    assertTrue(
                        device.wait(
                            Until.hasObject(By.text(context.getString(R.string.voice_review))),
                            10000,
                        )
                    )
                    image("voice")
                    click(R.string.cancel)
                    click(R.string.settings)
                    click(R.string.notification_apps)
                    image("notifications")
                    click(R.string.cancel)
                    click(R.string.currency_configure)
                    assertTrue(
                        device.wait(
                            Until.hasObject(By.text(context.getString(R.string.display_currency))),
                            10000,
                        )
                    )
                    image("currencies")
                    click(R.string.cancel)
                    scenario.onActivity { it.startActivity(Intent(it, HelpActivity::class.java)) }
                    assertTrue(
                        device.wait(Until.hasObject(By.textContains("OpenLedger 完整使用手册")), 20000)
                    )
                    image("help")
                    device.pressBack()
                }
                assertNull(app.pending())
            }
        } finally {
            assertTrue(
                preferences
                    .edit()
                    .putString("language", originalLanguage)
                    .putString("theme", originalTheme)
                    .commit()
            )
        }
    }

    @Test
    fun authorizedNotificationListenerCapturesActualSystemEvent() {
        SyntheticLedger().use {
            val prefs =
                originalContext.getSharedPreferences(
                    PaymentNotificationListener.PREFERENCES,
                    Context.MODE_PRIVATE,
                )
            val originalEnabled = prefs.getBoolean("enabled", false)
            val originalPackages =
                prefs.getStringSet("packages", PaymentNotificationListener.DEFAULT_PACKAGES)
            val originalAccess = PaymentNotificationListener.hasAccess(originalContext)
            val component =
                "${originalContext.packageName}/org.openledger.android.PaymentNotificationListener"
            fun shell(command: String): String =
                instrumentation.uiAutomation.executeShellCommand(command).use { descriptor ->
                    android.os.ParcelFileDescriptor.AutoCloseInputStream(descriptor)
                        .bufferedReader()
                        .use { it.readText() }
                }
            try {
                assertTrue(
                    prefs
                        .edit()
                        .putBoolean("enabled", true)
                        .putStringSet("packages", setOf("com.android.shell"))
                        .commit()
                )
                shell("cmd notification allow_listener $component")
                assertTrue(PaymentNotificationListener.hasAccess(originalContext))
                val tag = "phase12-${UUID.randomUUID()}"
                shell("cmd notification post -t '支付成功' $tag '商户：合成咖啡，实付12元'")
                val deadline = System.nanoTime() + java.util.concurrent.TimeUnit.SECONDS.toNanos(15)
                var rows = app.call("capture_list").getJSONObject("data").getJSONArray("rows")
                while (rows.length() == 0 && System.nanoTime() < deadline) {
                    Thread.sleep(150)
                    rows = app.call("capture_list").getJSONObject("data").getJSONArray("rows")
                }
                assertEquals(1, rows.length())
                assertEquals("com.android.shell", rows.getJSONObject(0).getString("source_label"))
                assertTrue(rows.getJSONObject(0).getString("text").contains("12元"))
                assertEquals(
                    0,
                    app.call("snapshot")
                        .getJSONObject("data")
                        .getJSONObject("transactions")
                        .getInt("total"),
                )
            } finally {
                if (!originalAccess) shell("cmd notification disallow_listener $component")
                assertTrue(
                    prefs
                        .edit()
                        .putBoolean("enabled", originalEnabled)
                        .putStringSet("packages", originalPackages)
                        .commit()
                )
            }
        }
    }
}
