package org.openledger.android

import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.text.InputType
import android.widget.CheckBox
import android.widget.Toast
import org.json.JSONObject

/** Nonfinancial settings, explicit network actions and native secure credentials. */
class SettingsActions(private val a: MainActivity) {
    private val ledger
        get() = a.application as LedgerApplication

    private val preferences
        get() = a.getSharedPreferences("presentation", Context.MODE_PRIVATE)

    private val aiPreferences
        get() = a.getSharedPreferences("ai-${ledger.directory}", Context.MODE_PRIVATE)

    fun config(): JSONObject =
        JSONObject()
            .put("enabled", aiPreferences.getBoolean("enabled", false))
            .put("base_url", aiPreferences.getString("base_url", ""))
            .put("model", aiPreferences.getString("model", ""))
            .put("allow_local_http", false)

    fun render() {
        val panel = a.card()
        panel.addView(a.label(a.getString(R.string.preferences_title), 20, true))
        panel.addView(
            a.button(a.getString(R.string.theme_title)) {
                val codes = listOf("system", "light", "dark")
                AlertDialog.Builder(a)
                    .setTitle(R.string.theme_title)
                    .setSingleChoiceItems(
                        listOf(R.string.follow_system, R.string.light_theme, R.string.dark_theme)
                            .map(a::getString)
                            .toTypedArray(),
                        codes.indexOf(preferences.getString("theme", "system")),
                    ) { dialog, index ->
                        check(preferences.edit().putString("theme", codes[index]).commit())
                        dialog.dismiss()
                        a.recreate()
                    }
                    .show()
            }
        )
        panel.addView(
            a.button(a.getString(R.string.language_title)) {
                AlertDialog.Builder(a)
                    .setTitle(R.string.language_title)
                    .setSingleChoiceItems(
                        arrayOf("简体中文", "English"),
                        if (preferences.getString("language", "zh") == "en") 1 else 0,
                    ) { dialog, index ->
                        check(
                            preferences
                                .edit()
                                .putString("language", if (index == 0) "zh" else "en")
                                .commit()
                        )
                        dialog.dismiss()
                        a.recreate()
                    }
                    .show()
            }
        )
        panel.addView(
            a.button(a.getString(R.string.time_zone_title, ledger.timeZone)) { timeZone() }
        )
        panel.addView(a.button(a.getString(R.string.ai_settings)) { aiSettings() })
        panel.addView(
            a.label(
                a.getString(
                    if (config().getBoolean("enabled")) R.string.ai_enabled
                    else R.string.ai_disabled
                ),
                13,
                color = a.muted,
            )
        )
        panel.addView(a.button(a.getString(R.string.check_update)) { updates() })
        panel.addView(
            a.button(a.getString(R.string.pin_shortcut)) {
                if (!QuickEntry.pin(a))
                    Toast.makeText(a, R.string.shortcut_unsupported, Toast.LENGTH_LONG).show()
            }
        )
        panel.addView(a.label(a.getString(R.string.tile_note), 13, color = a.muted))
        panel.addView(
            a.button(a.getString(R.string.open_source)) {
                browser("https://github.com/Bidao-Penknife/OpenLedger")
            }
        )
        val version = a.packageManager.getPackageInfo(a.packageName, 0).versionName ?: ""
        panel.addView(
            a.label(
                "OpenLedger $version · Schema ${a.snapshot.getInt("schema_version")}",
                13,
                color = a.muted,
            )
        )
        a.content.addView(panel)
    }

    private fun timeZone() {
        val form = a.column().apply { setPadding(a.dp(20), a.dp(8), a.dp(20), a.dp(12)) }
        form.addView(a.label(a.getString(R.string.time_zone_note), 14))
        val zone = a.edit(a.getString(R.string.time_zone_hint), ledger.timeZone)
        form.addView(zone)
        a.confirmation(R.string.time_zone_settings, form) { dialog ->
            a.request("validate_time_zone", JSONObject().put("time_zone", zone.text.toString())) {
                result ->
                a.fileBusy(true)
                ledger.worker.execute {
                    val changed = runCatching {
                        ledger.setTimeZone(result.getString("time_zone"))
                    }
                        .isSuccess
                    a.runOnUiThread {
                        a.fileBusy(false)
                        if (changed) {
                            dialog.dismiss()
                            a.analysis.invalidate(true)
                            a.refresh()
                        } else Toast.makeText(a, R.string.pending_error, Toast.LENGTH_LONG).show()
                    }
                }
            }
        }
    }

    private fun aiSettings() {
        val directory = ledger.directory
        val destination = aiPreferences
        val previous = config()
        val form = a.column().apply { setPadding(a.dp(20), a.dp(8), a.dp(20), a.dp(12)) }
        form.addView(a.label(a.getString(R.string.ai_privacy), 14))
        val enabled =
            CheckBox(a).apply {
                text = a.getString(R.string.enable_ai)
                isChecked = previous.getBoolean("enabled")
            }
        val endpoint = a.edit(a.getString(R.string.api_endpoint), previous.getString("base_url"))
        val model = a.edit(a.getString(R.string.api_model), previous.getString("model"))
        val key =
            a.edit(a.getString(R.string.api_key_hint)).apply {
                inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
                isSaveEnabled = false
            }
        form.addView(enabled)
        form.addView(endpoint)
        form.addView(model)
        form.addView(key)
        form.addView(
            a.button(a.getString(R.string.delete_key)) {
                AlertDialog.Builder(a)
                    .setMessage(R.string.delete_key_note)
                    .setNegativeButton(R.string.cancel, null)
                    .setPositiveButton(R.string.confirm_save) { _, _ ->
                        a.fileBusy(true)
                        ledger.worker.execute {
                            val cleared = runCatching {
                                check(ledger.directory == directory)
                                ledger.secrets.delete(
                                    directory,
                                    previous.getString("base_url"),
                                )
                            }
                                .isSuccess
                            a.runOnUiThread {
                                a.fileBusy(false)
                                Toast.makeText(
                                        a,
                                        if (cleared) R.string.key_deleted
                                        else R.string.settings_failed,
                                        Toast.LENGTH_LONG,
                                    )
                                    .show()
                            }
                        }
                    }
                    .show()
            }
        )
        a.confirmation(R.string.ai_settings, form) { dialog ->
            val configured =
                JSONObject()
                    .put("enabled", enabled.isChecked)
                    .put("base_url", endpoint.text.toString().trim())
                    .put("model", model.text.toString().trim())
                    .put("allow_local_http", false)
            fun save() {
                val secret = key.text.toString()
                a.fileBusy(true)
                ledger.worker.execute {
                    val saved = runCatching {
                        check(ledger.directory == directory)
                        if (secret.isNotEmpty())
                            ledger.secrets.set(
                                directory,
                                configured.getString("base_url"),
                                secret,
                            )
                        check(
                            destination
                                .edit()
                                .putBoolean("enabled", configured.getBoolean("enabled"))
                                .putString("base_url", configured.getString("base_url"))
                                .putString("model", configured.getString("model"))
                                .commit()
                        )
                    }
                        .isSuccess
                    a.runOnUiThread {
                        a.fileBusy(false)
                        if (saved) {
                            key.setText("")
                            dialog.dismiss()
                            a.renderCurrent()
                        } else Toast.makeText(a, R.string.settings_failed, Toast.LENGTH_LONG).show()
                    }
                }
            }
            if (
                !enabled.isChecked &&
                    configured.getString("base_url").isEmpty() &&
                    key.text.isEmpty()
            )
                save()
            else a.request("validate_ai_config", JSONObject().put("config", configured)) { save() }
        }
    }

    fun parse(text: String) {
        if (!config().getBoolean("enabled")) {
            aiSettings()
            return
        }
        AlertDialog.Builder(a)
            .setTitle(R.string.ai_parse)
            .setMessage(R.string.ai_request_note)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.ai_parse) { _, _ ->
                a.request("ai_preview", JSONObject().put("text", text).put("config", config())) {
                    a.reviewDrafts(it, true)
                }
            }
            .show()
    }

    fun updates() {
        val version = a.packageManager.getPackageInfo(a.packageName, 0).versionName ?: "0.2.0-beta1"
        a.request(
            "check_updates",
            JSONObject().put("current_version", version).put("include_prerelease", true),
        ) { result ->
            if (result.getString("status") == "available")
                AlertDialog.Builder(a)
                    .setTitle(R.string.update_available)
                    .setMessage(a.getString(R.string.update_note, result.getString("version")))
                    .setNegativeButton(R.string.close, null)
                    .setPositiveButton(R.string.view_release) { _, _ ->
                        browser(result.getString("release_url"))
                    }
                    .show()
            else
                Toast.makeText(
                        a,
                        if (result.getString("status") == "no_release") R.string.no_mobile_release
                        else R.string.up_to_date,
                        Toast.LENGTH_LONG,
                    )
                    .show()
        }
    }

    private fun browser(url: String) {
        runCatching { a.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
            .onFailure { Toast.makeText(a, R.string.browser_missing, Toast.LENGTH_SHORT).show() }
    }
}
