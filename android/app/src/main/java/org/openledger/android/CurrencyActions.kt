package org.openledger.android

import android.app.AlertDialog
import org.json.JSONObject

/** Dated manual quotations never rewrite a native balance or the two sides of an FX transfer. */
class CurrencyActions(private val a: MainActivity) {
    fun render(panel: android.widget.LinearLayout) {
        panel.addView(a.label(a.getString(R.string.currency_title), 20, true))
        panel.addView(a.label(a.getString(R.string.currency_policy), 13, color = a.muted))
        panel.addView(a.button(a.getString(R.string.currency_configure)) { configure() })
    }

    private fun configure() {
        a.request("currency_state") { state ->
            val form = a.column()
            val settings = state.getJSONObject("settings")
            val display =
                a.spinner(CurrencyCatalog.codes).apply {
                    setSelection(
                        CurrencyCatalog.codes.indexOf(settings.getString("display_currency"))
                    )
                }
            form.addView(a.label(a.getString(R.string.display_currency), 14))
            form.addView(display)
            form.addView(a.button(a.getString(R.string.rate_new)) { rate(state) })
            for (row in a.jsonRows(state.getJSONArray("rates"))) {
                form.addView(
                    a.button(
                        "${row.getString("currency_code")} · ${row.getString("effective_on")} · ${row.getString("rate_text")} CNY"
                    ) {
                        rate(state, row)
                    }
                )
            }
            a.confirmation(R.string.currency_title, form) { dialog ->
                a.request(
                    "mutate",
                    a.transactions.command(
                        "currency.set.v1",
                        JSONObject()
                            .put(
                                "display_currency",
                                CurrencyCatalog.codes[display.selectedItemPosition],
                            )
                            .put("expected_version", settings.getInt("version")),
                    ),
                    true,
                ) {
                    dialog.dismiss()
                    a.analysis.invalidate(true)
                    a.refresh()
                }
            }
        }
    }

    private fun rate(state: JSONObject, existing: JSONObject? = null) {
        val form = a.column()
        val codes = CurrencyCatalog.codes.filter { it != "CNY" }
        val code =
            a.spinner(codes).apply {
                setSelection(
                    codes.indexOf(existing?.optString("currency_code") ?: "USD").coerceAtLeast(0)
                )
                isEnabled = existing == null
            }
        val date =
            a.edit(
                a.getString(R.string.date),
                existing?.optString("effective_on") ?: a.snapshot.getString("today"),
            )
        date.isEnabled = existing == null
        val rate =
            a.edit(
                a.getString(R.string.rate_value),
                existing?.optString("rate_text") ?: "",
                amount = true,
            )
        val note = a.edit(a.getString(R.string.note), existing?.optString("note") ?: "")
        form.addView(a.label(a.getString(R.string.rate_note), 14))
        listOf(code, date, rate, note).forEach { form.addView(it) }
        a.confirmation(R.string.rate_new, form) { dialog ->
            val chosen = codes[code.selectedItemPosition]
            val payload =
                JSONObject()
                    .put("currency_code", chosen)
                    .put("effective_on", date.text.toString())
                    .put("rate_text", rate.text.toString())
                    .put("note", note.text.toString())
            val prior =
                existing
                    ?: a.jsonRows(state.getJSONArray("rates")).firstOrNull {
                        it.getString("currency_code") == chosen &&
                            it.getString("effective_on") == date.text.toString()
                    }
            if (prior != null) {
                if (existing == null) {
                    AlertDialog.Builder(a)
                        .setMessage(R.string.rate_exists)
                        .setPositiveButton(R.string.ok, null)
                        .show()
                    return@confirmation
                }
                payload.put("expected_version", prior.getInt("version"))
            }
            a.request("mutate", a.transactions.command("rate.set.v1", payload), true) {
                dialog.dismiss()
                a.analysis.invalidate(true)
                a.refresh()
            }
        }
    }
}
