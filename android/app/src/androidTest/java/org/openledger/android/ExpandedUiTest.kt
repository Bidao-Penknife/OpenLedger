package org.openledger.android

import android.content.Context
import android.graphics.Bitmap
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import androidx.test.uiautomator.By
import androidx.test.uiautomator.BySelector
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.Until
import java.io.File
import java.util.UUID
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

/** Exercise the real forms against a temporary restored dataset on an opt-in emulator. */
@RunWith(AndroidJUnit4::class)
class ExpandedUiTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context
        get() = AppPreferences.context(instrumentation.targetContext)

    private val application
        get() = instrumentation.targetContext.applicationContext as LedgerApplication

    private val device
        get() = UiDevice.getInstance(instrumentation)

    private fun click(title: Int) {
        val text = context.getString(title)
        device.wait(Until.hasObject(By.text(text)), 1500)
        for (attempt in 0..8) {
            val view = device.findObject(By.text(text))
            if (view != null) {
                view.click()
                return
            }
            device.swipe(
                device.displayWidth / 2,
                device.displayHeight * 4 / 5,
                device.displayWidth / 2,
                device.displayHeight / 3,
                20,
            )
            device.waitForIdle()
        }
        fail("Missing clickable control: $text")
    }

    private fun save() {
        assertTrue(device.wait(Until.hasObject(By.res(context.packageName, "save_button")), 10000))
        device.findObject(By.res(context.packageName, "save_button")).click()
        assertTrue(device.wait(Until.gone(By.res(context.packageName, "save_button")), 20000))
        application.worker.submit {}.get()
        instrumentation.waitForIdleSync()
    }

    private fun press(selector: BySelector) {
        val view = device.wait(Until.findObject(selector), 10000)
        if (view == null) {
            device.takeScreenshot(File(context.cacheDir, "phase11-ui-failure.png"))
            device.dumpWindowHierarchy(File(context.cacheDir, "phase11-ui-failure.xml"))
            fail("Missing control: $selector")
        }
        view!!.click()
    }

    private fun assets(amount: String) {
        assertTrue(
            "Expected assets $amount",
            device.wait(Until.hasObject(By.text("¥ $amount")), 20000),
        )
    }

    private fun choose(id: String, name: String) {
        assertTrue(device.wait(Until.hasObject(By.res(context.packageName, id)), 10000))
        device.findObject(By.res(context.packageName, id)).click()
        assertTrue(device.wait(Until.hasObject(By.text(name + " · CNY")), 5000))
        device.findObject(By.text(name + " · CNY")).click()
    }

    private fun seed(name: String, opening: String) {
        val today = application.call("snapshot").getJSONObject("data").getString("today")
        val result =
            application.confirmed(
                "create_account",
                JSONObject()
                    .put("request_id", UUID.randomUUID().toString())
                    .put("name", name)
                    .put("account_type", "cash")
                    .put("opening_amount", opening)
                    .put("balance_start_on", today),
            )
        assertTrue(result.toString(), result.getBoolean("ok"))
    }

    @Test
    fun editDeleteRestoreTransferRefundImageAndCharts() {
        SyntheticLedger().use {
            seed("合成现金", "1000")
            seed("合成银行卡", "0")
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                assets("1000.00")
                device.findObject(By.res(context.packageName, "quick_input")).text = "咖啡25元"
                device.findObject(By.res(context.packageName, "parse_button")).click()
                save()
                assets("975.00")
                val rows =
                    application
                        .call("snapshot")
                        .getJSONObject("data")
                        .getJSONObject("transactions")
                        .getJSONArray("rows")
                val id =
                    (0 until rows.length())
                        .map { rows.getJSONObject(it) }
                        .first { it.getString("kind") == "expense" }
                        .getString("id")
                scenario.onActivity { it.transactions.detail(id) }
                assertTrue(
                    device.wait(
                        Until.hasObject(By.text(context.getString(R.string.detail_title))),
                        10000,
                    )
                )
                click(R.string.edit_record)
                assertTrue(
                    device.wait(Until.hasObject(By.res(context.packageName, "amount_input")), 10000)
                )
                device.findObject(By.res(context.packageName, "amount_input")).text = "30.00"
                save()
                assets("970.00")
                scenario.onActivity { it.transactions.detail(id) }
                device.waitForIdle()
                click(R.string.delete_record)
                assertTrue(device.wait(Until.hasObject(By.res("android", "button1")), 10000))
                press(By.res("android", "button1"))
                assets("1000.00")
                scenario.onActivity { it.transactions.detail(id) }
                device.waitForIdle()
                click(R.string.restore_record)
                press(By.res("android", "button1"))
                assets("970.00")
                scenario.onActivity { it.transactions.transfer() }
                choose("from_account_input", "合成现金")
                choose("to_account_input", "合成银行卡")
                device.findObject(By.res(context.packageName, "amount_input")).text = "100.00"
                save()
                assets("970.00")
                scenario.onActivity { it.transactions.refund(id) }
                choose("to_account_input", "合成银行卡")
                device.findObject(By.res(context.packageName, "amount_input")).text = "5.00"
                save()
                assets("975.00")
                val image = File(application.staging, "ui-receipt.png")
                val bitmap =
                    Bitmap.createBitmap(80, 80, Bitmap.Config.ARGB_8888).apply {
                        eraseColor(0xff256f62.toInt())
                    }
                image.outputStream().use {
                    assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it))
                }
                bitmap.recycle()
                val version =
                    application
                        .call("transaction_detail", JSONObject().put("id", id))
                        .getJSONObject("data")
                        .getLong("version")
                scenario.onActivity { it.importedFile("attachment:$id:$version", image.name) }
                assertTrue(
                    device.wait(
                        Until.hasObject(By.text(context.getString(R.string.image_attachment))),
                        10000,
                    )
                )
                press(By.res("android", "button1"))
                assertTrue(
                    device.wait(
                        Until.hasObject(By.text(context.getString(R.string.image_number, 1))),
                        10000,
                    )
                )
                val detail =
                    application
                        .call("transaction_detail", JSONObject().put("id", id))
                        .getJSONObject("data")
                assertEquals(1, detail.getJSONArray("attachments").length())
                device.pressBack()
                click(R.string.transactions)
                assertTrue(
                    device.takeScreenshot(File(context.cacheDir, "phase11-transactions.png"))
                )
                click(R.string.nav_assets)
                assertTrue(device.takeScreenshot(File(context.cacheDir, "phase11-assets.png")))
                click(R.string.analysis)
                assertTrue(
                    device.wait(
                        Until.hasObject(
                            By.text(context.getString(R.string.net_expense) + ": ¥ 25.00")
                        ),
                        10000,
                    )
                )
                device.swipe(
                    device.displayWidth / 2,
                    device.displayHeight * 4 / 5,
                    device.displayWidth / 2,
                    device.displayHeight / 2,
                    18,
                )
                device.waitForIdle()
                assertTrue(device.takeScreenshot(File(context.cacheDir, "phase11-analysis.png")))
                val overview =
                    application.call("snapshot").getJSONObject("data").getJSONObject("overview")
                assertEquals(97500L, overview.getLong("total_assets_minor"))
                assertEquals(2500L, overview.getLong("expense_minor"))
                assertNull(application.pending())
                File(context.cacheDir, "phase11-ui-check.json").writeText(overview.toString(2))
            }
        }
    }

    @Test
    fun themesLanguageAndQuickIntentPersist() {
        SyntheticLedger().use {
            val preferences = application.getSharedPreferences("presentation", Context.MODE_PRIVATE)
            val originalLanguage = preferences.getString("language", "zh")!!
            val originalTheme = preferences.getString("theme", "system")!!
            try {
                ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                    assertTrue(
                        device.wait(
                            Until.hasObject(By.res(context.packageName, "quick_input")),
                            20000,
                        )
                    )
                    click(R.string.settings)
                    click(R.string.theme_title)
                    click(R.string.dark_theme)
                    assertTrue(
                        device.wait(
                            Until.hasObject(By.text(context.getString(R.string.settings))),
                            10000,
                        )
                    )
                    assertEquals("dark", preferences.getString("theme", ""))
                    assertTrue(device.takeScreenshot(File(context.cacheDir, "phase11-dark.png")))
                    click(R.string.language_title)
                    press(By.text("English"))
                    assertTrue(device.wait(Until.hasObject(By.text("Settings")), 10000))
                    assertEquals("en", preferences.getString("language", ""))
                    assertTrue(device.takeScreenshot(File(context.cacheDir, "phase11-english.png")))
                    scenario.recreate()
                    assertTrue(device.wait(Until.hasObject(By.text("Settings")), 10000))
                }
                instrumentation.targetContext.startActivity(
                    QuickEntry.intent(instrumentation.targetContext)
                )
                assertTrue(
                    device.wait(
                        Until.hasObject(By.res(context.packageName, "quick_input")),
                        10000,
                    )
                )
                assertNull(application.pending())
                assertEquals(
                    0,
                    application
                        .call("snapshot")
                        .getJSONObject("data")
                        .getJSONObject("transactions")
                        .getInt("total"),
                )
                instrumentation.runOnMainSync {
                    ActivityLifecycleMonitorRegistry.getInstance()
                        .getActivitiesInStage(Stage.RESUMED)
                        .filterIsInstance<MainActivity>()
                        .forEach { it.finish() }
                }
                instrumentation.waitForIdleSync()
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
    }
}
