package org.openledger.android

import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.Until
import java.io.File
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Run only on a fresh preview installation: never alter an existing user's ledger. */
@RunWith(AndroidJUnit4::class)
class UiFlowTest {
    @Test
    fun createAccountParseConfirmAndDisplayExactAssets() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = AppPreferences.context(instrumentation.targetContext)
        val application = context.applicationContext as LedgerApplication
        SyntheticLedger().use {
            val initial = application.call("snapshot").getJSONObject("data")
            assumeTrue(
                "UI fixture requires an empty preview installation",
                initial.getJSONArray("accounts").length() == 0,
            )
            assumeTrue(initial.getJSONObject("transactions").getInt("total") == 0)
            val device = UiDevice.getInstance(instrumentation)
            val packageName = context.packageName
            ActivityScenario.launch(MainActivity::class.java).use {
                assertTrue(
                    device.wait(
                        Until.hasObject(By.text(context.getString(R.string.new_account))),
                        20000,
                    )
                )
                device.findObject(By.text(context.getString(R.string.new_account))).click()
                assertTrue(device.wait(Until.hasObject(By.res(packageName, "save_button")), 10000))
                val editors = device.findObjects(By.clazz("android.widget.EditText"))
                assertEquals(2, editors.size)
                editors[0].text = "合成现金"
                editors[1].text = "1000.00"
                device.findObject(By.res(packageName, "save_button")).click()
                assertTrue(device.wait(Until.hasObject(By.text("¥ 1000.00")), 20000))
                device.findObject(By.res(packageName, "quick_input")).text = "咖啡25元"
                device.findObject(By.res(packageName, "parse_button")).click()
                assertTrue(
                    device.wait(
                        Until.hasObject(By.text(context.getString(R.string.confirm_record))),
                        10000,
                    )
                )
                device.findObject(By.res(packageName, "save_button")).click()
                assertTrue(device.wait(Until.hasObject(By.text("¥ 975.00")), 20000))
                val snapshot = application.call("snapshot").getJSONObject("data")
                assertEquals(
                    97500L,
                    snapshot.getJSONObject("overview").getLong("total_assets_minor"),
                )
                assertEquals(2500L, snapshot.getJSONObject("overview").getLong("expense_minor"))
                assertNull(application.pending())
                val screenshot = File(context.cacheDir, "ui-confirmed.png")
                assertTrue(device.takeScreenshot(screenshot))
                val evidence = File(context.cacheDir, "ui-flow-check.json")
                evidence.writeText(snapshot.toString(2), Charsets.UTF_8)
                device.findObject(By.text(context.getString(R.string.transactions))).click()
                assertTrue(device.wait(Until.hasObject(By.textContains("25.00")), 10000))
                assertTrue(device.takeScreenshot(File(context.cacheDir, "ui-transactions.png")))
            }
        }
    }
}
