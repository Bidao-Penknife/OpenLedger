package org.openledger.android

import android.app.AlertDialog
import android.content.Intent
import android.graphics.BitmapFactory
import android.provider.Settings
import android.widget.CheckBox
import android.widget.ImageView
import android.widget.ScrollView
import android.widget.Toast
import java.io.File
import java.util.UUID
import org.json.JSONObject

/** All three capture sources share a durable inbox and the existing audited editors. */
class CaptureActions(private val a: MainActivity) {
    private val ledger
        get() = a.application as LedgerApplication

    val voice = VoiceActions(a)
    val ocr = OcrActions(a)

    fun stage(body: JSONObject, done: () -> Unit = {}) {
        body.put("request_id", UUID.randomUUID().toString())
        a.request("capture_stage", body, true) { result ->
            done()
            val value = result.getJSONObject("data")
            if (value.optBoolean("duplicate"))
                Toast.makeText(a, R.string.capture_duplicate, Toast.LENGTH_LONG).show()
            detail(value.getString("id"))
        }
    }

    fun stageVoice(text: String, done: () -> Unit) =
        stage(
            JSONObject()
                .put("source_kind", "voice")
                .put("source_label", a.getString(R.string.voice_title))
                .put("event_key", UUID.randomUUID().toString())
                .put("text", text.take(4000)),
            done,
        )

    fun inbox(state: String = "pending", page: Int = 0) {
        a.request("capture_list", JSONObject().put("state", state).put("page", page)) { result ->
            val form = a.column()
            form.addView(a.label(a.getString(R.string.capture_inbox_note), 14))
            val tabs = a.row()
            for ((key, title) in
                listOf(
                    "pending" to R.string.capture_pending,
                    "saved" to R.string.capture_saved,
                    "ignored" to R.string.capture_ignored,
                )) tabs.addView(a.button(a.getString(title)) { inbox(key) })
            form.addView(tabs)
            val rows = a.jsonRows(result.getJSONArray("rows"))
            if (rows.isEmpty()) form.addView(a.label(a.getString(R.string.capture_empty), 14))
            var dialog: AlertDialog? = null
            for (row in rows) form.addView(
                a.button(row.getString("source_label") + " · " + row.getString("text").take(80)) {
                    dialog?.dismiss()
                    detail(row.getString("id"))
                }
            )
            if (page > 0)
                form.addView(
                    a.button(a.getString(R.string.previous)) {
                        dialog?.dismiss()
                        inbox(state, page - 1)
                    }
                )
            if ((page + 1) * 25 < result.getInt("total"))
                form.addView(
                    a.button(a.getString(R.string.next)) {
                        dialog?.dismiss()
                        inbox(state, page + 1)
                    }
                )
            dialog =
                AlertDialog.Builder(a)
                    .setTitle(R.string.capture_inbox)
                    .setView(ScrollView(a).apply { addView(form) })
                    .setNegativeButton(R.string.cancel, null)
                    .show()
        }
    }

    fun detail(id: String) {
        a.request("capture_detail", JSONObject().put("id", id)) { capture ->
            val form = a.column()
            form.addView(a.label(capture.getString("source_label"), 16, true))
            form.addView(a.label(capture.getString("text"), 14))
            form.addView(a.label(a.getString(R.string.capture_review_note), 13, color = a.muted))
            if (!capture.isNull("image_relative_path"))
                form.addView(
                    a.button(a.getString(R.string.capture_original)) {
                        image(capture.getString("id"))
                    }
                )
            val state = capture.getString("state")
            var dialog: AlertDialog? = null
            if (state == "pending") {
                form.addView(
                    a.button(a.getString(R.string.capture_confirm)) {
                        dialog?.dismiss()
                        chooseAmount(capture)
                    }
                )
                form.addView(
                    a.button(a.getString(R.string.capture_ignore)) {
                        change("capture_ignore", capture) { dialog?.dismiss() }
                    }
                )
            } else if (state == "ignored")
                form.addView(
                    a.button(a.getString(R.string.restore_item)) {
                        change("capture_restore", capture) { dialog?.dismiss() }
                    }
                )
            else
                form.addView(
                    a.button(a.getString(R.string.capture_open_saved)) {
                        dialog?.dismiss()
                        a.transactions.detail(capture.getString("transaction_id"))
                    }
                )
            dialog =
                AlertDialog.Builder(a)
                    .setTitle(R.string.capture_inbox)
                    .setView(ScrollView(a).apply { addView(form) })
                    .setNegativeButton(R.string.cancel, null)
                    .show()
        }
    }

    private fun change(action: String, capture: JSONObject, done: () -> Unit) {
        a.request(
            action,
            JSONObject()
                .put("request_id", UUID.randomUUID().toString())
                .put("id", capture.getString("id"))
                .put("expected_version", capture.getInt("version")),
            true,
        ) {
            done()
            a.refresh()
        }
    }

    private fun chooseAmount(capture: JSONObject) {
        val candidates =
            a.jsonRows(capture.getJSONObject("suggested").getJSONArray("amount_candidates"))
        if (candidates.size <= 1) {
            capture.put("selected_amount", candidates.firstOrNull()?.optString("amount") ?: "")
            edit(capture)
        } else
            AlertDialog.Builder(a)
                .setTitle(R.string.capture_choose_amount)
                .setItems(
                    (candidates.map { it.getString("amount") + " · " + it.getString("context") } +
                            a.getString(R.string.manual))
                        .toTypedArray()
                ) { _, index ->
                    capture.put(
                        "selected_amount",
                        candidates.getOrNull(index)?.optString("amount") ?: "",
                    )
                    edit(capture)
                }
                .setNegativeButton(R.string.cancel, null)
                .show()
    }

    private fun edit(capture: JSONObject) {
        if (a.activeAccounts().isEmpty()) {
            Toast.makeText(a, R.string.no_accounts, Toast.LENGTH_LONG).show()
            return
        }
        val suggested = capture.getJSONObject("suggested")
        val kinds = listOf("expense", "income", "transfer", "expense_refund")
        val labels = kinds.map(a::kindLabel).toTypedArray()
        AlertDialog.Builder(a)
            .setTitle(R.string.transaction_type)
            .setSingleChoiceItems(
                labels,
                kinds.indexOf(suggested.getString("suggested_kind")).coerceAtLeast(0),
            ) { dialog, position ->
                dialog.dismiss()
                when (val kind = kinds[position]) {
                    "transfer" -> a.transactions.transfer(capture = capture)
                    "expense_refund" -> original(capture)
                    else -> {
                        val draft =
                            suggested.getJSONArray("drafts").optJSONObject(0) ?: JSONObject()
                        draft.put("kind", JSONObject().put("value", kind))
                        draft.put(
                            "merchant",
                            JSONObject().put("value", suggested.getString("suggested_merchant")),
                        )
                        draft.put("currency_code", suggested.getString("currency_code"))
                        a.editTransaction(draft, capture = capture)
                    }
                }
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun original(capture: JSONObject, page: Int = 0, search: String = "") {
        a.request(
            "snapshot",
            JSONObject().put("kind", "expense").put("page", page).put("search", search),
        ) { data ->
            val result = data.getJSONObject("transactions")
            val rows = a.jsonRows(result.getJSONArray("rows"))
            val form = a.column()
            val input = a.edit(a.getString(R.string.search_hint), search)
            form.addView(input)
            var dialog: AlertDialog? = null
            form.addView(
                a.button(a.getString(R.string.search)) {
                    dialog?.dismiss()
                    original(capture, 0, input.text.toString())
                }
            )
            for (row in rows) form.addView(
                a.button(
                    row.getString("occurred_on") +
                        " · " +
                        row.getString("currency_code") +
                        " " +
                        a.money(row.getLong("amount_minor"), row.getString("currency_code")) +
                        " · " +
                        row.optString("note")
                ) {
                    dialog?.dismiss()
                    a.transactions.refund(row.getString("id"), capture = capture)
                }
            )
            if (rows.isEmpty()) form.addView(a.label(a.getString(R.string.empty_transactions), 14))
            if ((page + 1) * 25 < result.getInt("total"))
                form.addView(
                    a.button(a.getString(R.string.next)) {
                        dialog?.dismiss()
                        original(capture, page + 1, search)
                    }
                )
            dialog =
                AlertDialog.Builder(a)
                    .setTitle(R.string.capture_original_expense)
                    .setView(ScrollView(a).apply { addView(form) })
                    .setNegativeButton(R.string.cancel, null)
                    .show()
        }
    }

    private fun image(id: String) {
        a.request("capture_view", JSONObject().put("id", id)) { data ->
            val file = File(ledger.staging, data.getString("filename"))
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(file.absolutePath, bounds)
            var sample = 1
            while (
                bounds.outWidth.toLong() * bounds.outHeight / sample / sample > 4_000_000
            ) sample *= 2
            val bitmap =
                BitmapFactory.decodeFile(
                    file.absolutePath,
                    BitmapFactory.Options().apply { inSampleSize = sample },
                )
            val view =
                ImageView(a).apply {
                    adjustViewBounds = true
                    setImageBitmap(bitmap)
                    contentDescription = a.getString(R.string.capture_original)
                }
            AlertDialog.Builder(a)
                .setTitle(R.string.capture_original)
                .setView(ScrollView(a).apply { addView(view) })
                .setPositiveButton(R.string.ok, null)
                .show()
                .setOnDismissListener {
                    view.setImageDrawable(null)
                    bitmap?.recycle()
                    file.delete()
                }
        }
    }

    fun settings(panel: android.widget.LinearLayout) {
        val prefs =
            a.getSharedPreferences(
                PaymentNotificationListener.PREFERENCES,
                android.content.Context.MODE_PRIVATE,
            )
        panel.addView(a.label(a.getString(R.string.notification_title), 20, true))
        panel.addView(a.label(a.getString(R.string.notification_note), 13, color = a.muted))
        val enabled =
            CheckBox(a).apply {
                text = a.getString(R.string.notification_enabled)
                isChecked = prefs.getBoolean("enabled", false)
            }
        enabled.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean("enabled", checked).apply()
        }
        panel.addView(enabled)
        panel.addView(
            a.label(
                a.getString(
                    if (PaymentNotificationListener.hasAccess(a)) R.string.notification_granted
                    else R.string.notification_missing
                ),
                14,
            )
        )
        panel.addView(
            a.button(a.getString(R.string.notification_access)) {
                runCatching {
                    a.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                }
                    .onFailure {
                        Toast.makeText(a, R.string.notification_missing, Toast.LENGTH_LONG).show()
                    }
            }
        )
        panel.addView(
            a.button(a.getString(R.string.notification_apps)) {
                val form = a.column()
                val selected =
                    prefs
                        .getStringSet("packages", PaymentNotificationListener.DEFAULT_PACKAGES)
                        .orEmpty()
                val checks =
                    PaymentNotificationListener.DEFAULT_PACKAGES.sorted().map { packageName ->
                        packageName to
                            CheckBox(a).apply {
                                text = packageName
                                isChecked = packageName in selected
                                form.addView(this)
                            }
                    }
                val custom =
                    a.edit(
                        a.getString(R.string.notification_custom),
                        selected
                            .filter { it !in PaymentNotificationListener.DEFAULT_PACKAGES }
                            .joinToString("\n"),
                        multiline = true,
                    )
                form.addView(custom)
                a.confirmation(R.string.notification_apps, form) { dialog ->
                    val additional =
                        custom.text.toString().split(Regex("[\\s,;]+")).filter { it.isNotBlank() }
                    if (
                        additional.any {
                            !Regex("[A-Za-z][A-Za-z0-9_]*(?:\\.[A-Za-z][A-Za-z0-9_]*)+").matches(it)
                        } || additional.size > 20
                    ) {
                        custom.error = a.getString(R.string.invalid_fields)
                        return@confirmation
                    }
                    val values =
                        checks.filter { it.second.isChecked }.map { it.first }.toSet() + additional
                    prefs.edit().putStringSet("packages", values).apply()
                    dialog.dismiss()
                }
            }
        )
        val error = prefs.getString("last_error", "").orEmpty()
        if (error.isNotBlank()) panel.addView(a.label(a.errorText(error), 13))
        panel.addView(a.button(a.getString(R.string.capture_inbox)) { inbox() })
    }
}
