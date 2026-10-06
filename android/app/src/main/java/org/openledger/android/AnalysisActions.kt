package org.openledger.android

import android.app.AlertDialog
import android.os.Bundle
import android.widget.LinearLayout
import org.json.JSONArray
import org.json.JSONObject

/** Reuse one exact Python report snapshot for charts, labels and exports. */
class AnalysisActions(private val a: MainActivity) {
    private var selection = JSONObject()
    private var current: JSONObject? = null

    fun save(state: Bundle) {
        state.putString("analysis_filters", selection.toString())
    }

    fun restore(state: Bundle?) {
        selection = JSONObject(state?.getString("analysis_filters") ?: "{}")
    }

    fun render() {
        if (selection.length() == 0)
            selection
                .put("start_on", a.snapshot.getString("today").take(4) + "-01-01")
                .put("end_on", a.snapshot.getString("today"))
        val panel = a.card()
        panel.addView(a.label(a.getString(R.string.analysis), 20, true))
        panel.addView(
            a.label(selection.getString("start_on") + " — " + selection.getString("end_on"), 14)
        )
        panel.addView(a.button(a.getString(R.string.analysis_filter)) { filters() })
        panel.addView(a.button(a.getString(R.string.refresh)) { load() })
        a.content.addView(panel)
        val report = current
        if (report == null) {
            load()
            return
        }
        val code = report.getJSONObject("filters").getString("currency_code")
        val totals = report.getJSONObject("totals")
        val summary = a.card()
        for ((key, title) in
            listOf(
                "income_minor" to R.string.income,
                "gross_expense_minor" to R.string.gross_expense,
                "refund_minor" to R.string.refund,
                "net_expense_minor" to R.string.net_expense,
                "surplus_minor" to R.string.surplus,
            )) summary.addView(
            a.label(
                a.getString(title) +
                    ": " +
                    CurrencyCatalog.label(code) +
                    " " +
                    ReportRenderer.money(totals.getString(key), code),
                17,
                true,
            )
        )
        if (!totals.isNull("savings_rate"))
            summary.addView(
                a.label(
                    a.getString(R.string.savings_rate) +
                        ": " +
                        ReportRenderer.percent(totals.getString("savings_rate")),
                    14,
                )
            )
        a.content.addView(summary)
        val charts = a.card()
        charts.addView(a.label(a.getString(R.string.month_trend), 18, true))
        charts.addView(FinanceChart(a, report, "months"), LinearLayout.LayoutParams(-1, a.dp(230)))
        charts.addView(a.label(a.getString(R.string.chart_legend), 13, color = a.muted))
        charts.addView(a.label(a.getString(R.string.category_share), 18, true))
        charts.addView(
            FinanceChart(a, report, "categories"),
            LinearLayout.LayoutParams(-1, a.dp(230)),
        )
        charts.addView(a.label(a.getString(R.string.category_basis), 13, color = a.muted))
        a.content.addView(charts)
        val details = a.card()
        for (month in a.jsonRows(report.getJSONArray("months"))) {
            val money = month.getJSONObject("totals")
            details.addView(
                a.label(
                    month.getString("month") +
                        "  " +
                        a.getString(R.string.income) +
                        " " +
                        CurrencyCatalog.label(code) +
                        ReportRenderer.money(money.getString("income_minor"), code) +
                        " · " +
                        a.getString(R.string.net_expense) +
                        " " +
                        CurrencyCatalog.label(code) +
                        ReportRenderer.money(money.getString("net_expense_minor"), code),
                    13,
                )
            )
        }
        details.addView(a.label(a.getString(R.string.category_share), 18, true))
        for (category in a.jsonRows(report.getJSONArray("categories"))) details.addView(
            a.label(
                category.getString("name") +
                    " · " +
                    CurrencyCatalog.label(code) +
                    ReportRenderer.money(category.getString("net_expense_minor"), code) +
                    " · " +
                    ReportRenderer.share(category),
                14,
            )
        )
        details.addView(a.label(a.getString(R.string.expense_rank), 18, true))
        for ((index, item) in a.jsonRows(report.getJSONArray("ranking")).withIndex()) details
            .addView(
                a.label(
                    "${index + 1}. ${item.getString("label")} · ${CurrencyCatalog.label(code)}${ReportRenderer.money(item.getString("amount_minor"), code)} · ${item.getInt("count")}",
                    14,
                )
            )
        val comparison = report.optJSONObject("comparison_totals")
        if (comparison != null)
            details.addView(
                a.label(
                    a.getString(
                        R.string.comparison_note,
                        report.getString("comparison_start"),
                        report.getString("comparison_end"),
                        ReportRenderer.money(comparison.getString("income_minor"), code),
                        ReportRenderer.money(comparison.getString("net_expense_minor"), code),
                    ),
                    14,
                )
            )
        val notes = report.getJSONArray("notes")
        for (i in 0 until notes.length()) details.addView(
            a.label(notes.getString(i), 13, color = a.muted)
        )
        details.addView(a.button(a.getString(R.string.export_pdf)) { export("pdf", report) })
        details.addView(a.button(a.getString(R.string.export_png)) { export("png", report) })
        a.content.addView(details)
    }

    private fun load() {
        a.request("analytics", selection) {
            current = it
            a.renderCurrent()
        }
    }

    private fun filters() {
        val form = a.column().apply { setPadding(a.dp(20), a.dp(8), a.dp(20), a.dp(12)) }
        val start = a.edit(a.getString(R.string.start_on), selection.getString("start_on"))
        val end = a.edit(a.getString(R.string.end_on), selection.getString("end_on"))
        form.addView(start)
        form.addView(end)
        val selectors = mutableListOf<Triple<String, android.widget.Spinner, List<JSONObject>>>()
        for ((entity, title) in
            listOf(
                "book" to R.string.book,
                "account" to R.string.account,
                "category" to R.string.category,
                "tag" to R.string.tags,
            )) {
            val rows = a.jsonRows(a.snapshot.getJSONObject("catalogs").getJSONArray(entity))
            val selected = selection.optJSONArray("${entity}_ids")?.optString(0)
            val view = a.choices(rows, selected, a.getString(R.string.all_items))
            selectors.add(Triple(entity, view, rows))
            form.addView(a.label(a.getString(title), 13))
            form.addView(view)
        }
        val rankCodes = listOf("merchant", "category", "counterparty")
        val rank =
            a.spinner(
                listOf(R.string.merchant, R.string.category, R.string.counterparty)
                    .map(a::getString)
            )
        rank.setSelection(
            rankCodes.indexOf(selection.optString("ranking_dimension", "merchant")).coerceAtLeast(0)
        )
        form.addView(a.label(a.getString(R.string.ranking_dimension), 13))
        form.addView(rank)
        a.confirmation(R.string.analysis_filter, form) { dialog ->
            val value =
                JSONObject()
                    .put("start_on", start.text.toString())
                    .put("end_on", end.text.toString())
                    .put("ranking_dimension", rankCodes[rank.selectedItemPosition])
            for ((entity, view, rows) in selectors) {
                val id = a.selectedId(view, rows)
                value.put(
                    "${entity}_ids",
                    if (id == JSONObject.NULL) JSONArray() else JSONArray().put(id),
                )
            }
            a.request("analytics", value) {
                selection = value
                current = it
                dialog.dismiss()
                a.renderCurrent()
            }
        }
    }

    private fun export(format: String, report: JSONObject) {
        a.fileBusy(true)
        val ledger = a.application as LedgerApplication
        ledger.worker.execute {
            val file = runCatching {
                ReportRenderer(a).export(report, format, ledger.staging)
            }
                .getOrNull()
            a.runOnUiThread {
                a.fileBusy(false)
                if (file != null)
                    a.files.export(
                        file.name,
                        if (format == "pdf") "application/pdf" else "image/png",
                    )
                else
                    AlertDialog.Builder(a)
                        .setMessage(R.string.report_failed)
                        .setPositiveButton(R.string.close, null)
                        .show()
            }
        }
    }

    fun invalidate(reset: Boolean = false) {
        current = null
        if (reset) selection = JSONObject()
    }
}
