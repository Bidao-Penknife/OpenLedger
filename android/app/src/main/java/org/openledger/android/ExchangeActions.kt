package org.openledger.android

import android.app.AlertDialog
import android.widget.Toast
import java.util.UUID
import org.json.JSONArray
import org.json.JSONObject

/** SAF file exchange: map, inspect duplicates, select and explicitly confirm. */
class ExchangeActions(private val a: MainActivity) {
    fun render() {
        val panel = a.card()
        panel.addView(a.label(a.getString(R.string.file_exchange), 20, true))
        panel.addView(a.label(a.getString(R.string.import_note), 14, color = a.muted))
        panel.addView(
            a.button(a.getString(R.string.import_csv)) {
                AlertDialog.Builder(a)
                    .setTitle(R.string.csv_encoding)
                    .setItems(arrayOf("UTF-8", "GB18030")) { _, index ->
                        a.files.pick(if (index == 0) "import" else "import_gb", "csv", "*/*")
                    }
                    .show()
            }
        )
        panel.addView(
            a.button(a.getString(R.string.import_excel)) { a.files.pick("import", "xlsx", "*/*") }
        )
        for (format in listOf("csv", "xlsx")) panel.addView(
            a.button(
                a.getString(if (format == "csv") R.string.export_csv else R.string.export_excel)
            ) {
                val filename = "OpenLedger-transactions-${UUID.randomUUID()}.$format"
                a.request(
                    "export_transactions",
                    JSONObject().put("filename", filename).put("format", format),
                ) {
                    a.files.export(
                        it.getString("filename"),
                        if (format == "csv") "text/csv"
                        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                }
            }
        )
        panel.addView(a.button(a.getString(R.string.import_history)) { history() })
        a.content.addView(panel)
    }

    fun mapping(filename: String, encoding: String = "utf-8-sig") {
        a.request(
            "import_headers",
            JSONObject().put("filename", filename).put("encoding", encoding),
        ) { headers ->
            val form = a.column().apply { setPadding(a.dp(20), a.dp(8), a.dp(20), a.dp(12)) }
            form.addView(
                a.label(a.getString(R.string.import_rows, headers.getInt("row_count")), 14)
            )
            val defaults = mutableListOf<Triple<String, android.widget.Spinner, List<JSONObject>>>()
            for ((key, entity, title) in
                listOf(
                    Triple("book_id", "book", R.string.book),
                    Triple("account_id", "account", R.string.account),
                    Triple("income_category_id", "category", R.string.default_income_category),
                    Triple("expense_category_id", "category", R.string.default_expense_category),
                    Triple("from_account_id", "account", R.string.from_account),
                    Triple("to_account_id", "account", R.string.to_account),
                )) {
                val rows =
                    a.selectable(entity).filter {
                        key !in listOf("income_category_id", "expense_category_id") ||
                            it.getString("transaction_kind") ==
                                if (key == "income_category_id") "income" else "expense"
                    }
                val initial =
                    if (entity == "book" || key == "account_id")
                        a.nullable(a.snapshot.getJSONObject("preferences"), "default_${entity}_id")
                    else null
                val view = a.choices(rows, initial, a.getString(R.string.from_file))
                defaults.add(Triple(key, view, rows))
                form.addView(a.label(a.getString(title), 13))
                form.addView(view)
            }
            val kind =
                a.spinner(listOf(a.getString(R.string.expense), a.getString(R.string.income)))
            form.addView(a.label(a.getString(R.string.default_kind), 13))
            form.addView(kind)
            val sheets = headers.getJSONArray("sheets")
            val sheetNames = (0 until sheets.length()).map { sheets.getString(it) }
            val sheet = a.spinner(sheetNames)
            if (sheetNames.isNotEmpty()) {
                form.addView(a.label(a.getString(R.string.excel_sheet), 13))
                form.addView(sheet)
            }
            var columns = headers.getJSONArray("columns")
            var currentHeaders = headers.getJSONArray("headers")
            sheet.onItemSelectedListener =
                object : android.widget.AdapterView.OnItemSelectedListener {
                    override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}

                    override fun onItemSelected(
                        parent: android.widget.AdapterView<*>?,
                        view: android.view.View?,
                        position: Int,
                        id: Long,
                    ) {
                        if (position > 0 || currentHeaders != headers.getJSONArray("headers")) {
                            a.request(
                                "import_headers",
                                JSONObject()
                                    .put("filename", filename)
                                    .put("encoding", encoding)
                                    .put("sheet", sheetNames[position]),
                            ) { result ->
                                currentHeaders = result.getJSONArray("headers")
                                columns = result.getJSONArray("columns")
                            }
                        }
                    }
                }
            form.addView(
                a.button(a.getString(R.string.column_mapping)) {
                    columnMapping(currentHeaders, columns) { columns = it }
                }
            )
            a.confirmation(R.string.import_preview, form) { dialog ->
                val mapping =
                    JSONObject()
                        .put("columns", columns)
                        .put("encoding", encoding)
                        .put(
                            "default_kind",
                            if (kind.selectedItemPosition == 0) "expense" else "income",
                        )
                if (sheetNames.isNotEmpty())
                    mapping.put("sheet", sheetNames[sheet.selectedItemPosition])
                for ((key, view, rows) in defaults) mapping.put(key, a.selectedId(view, rows))
                a.request(
                    "import_preview",
                    JSONObject().put("filename", filename).put("mapping", mapping),
                ) { result ->
                    dialog.dismiss()
                    preview(result)
                }
            }
        }
    }

    private fun columnMapping(headers: JSONArray, current: JSONArray, done: (JSONArray) -> Unit) {
        val names = (0 until headers.length()).map { headers.getString(it) }
        val mapped = mutableMapOf<String, String>()
        for (i in 0 until current.length()) mapped[current.getJSONArray(i).getString(0)] =
            current.getJSONArray(i).getString(1)
        val fields =
            listOf(
                "kind" to R.string.transaction_type,
                "amount" to R.string.amount,
                "amount_minor" to R.string.amount_fen,
                "occurred_on" to R.string.date,
                "book_name" to R.string.book,
                "account_name" to R.string.account,
                "category_name" to R.string.category,
                "payment_method_name" to R.string.payment,
                "from_account_name" to R.string.from_account,
                "to_account_name" to R.string.to_account,
                "note" to R.string.note,
                "merchant" to R.string.merchant,
                "counterparty" to R.string.counterparty,
                "location" to R.string.location,
                "external_transaction_id" to R.string.external_id,
                "external_source" to R.string.external_source,
            )
        val form = a.column().apply { setPadding(a.dp(20), a.dp(8), a.dp(20), a.dp(12)) }
        val views = fields.map { (key, title) ->
            val view = a.spinner(listOf(a.getString(R.string.unmapped)) + names)
            view.setSelection(names.indexOf(mapped[key]) + 1)
            form.addView(a.label(a.getString(title), 13))
            form.addView(view)
            key to view
        }
        a.confirmation(R.string.column_mapping, form) { dialog ->
            for ((key, view) in views) {
                val index = view.selectedItemPosition - 1
                if (index < 0) mapped.remove(key) else mapped[key] = names[index]
            }
            done(JSONArray(mapped.toSortedMap().map { JSONArray().put(it.key).put(it.value) }))
            dialog.dismiss()
        }
    }

    private fun preview(result: JSONObject) {
        val rows = a.jsonRows(result.getJSONArray("rows"))
        val valid = rows.map { it.getJSONArray("issues").length() == 0 && !it.isNull("fields") }
        val selected =
            BooleanArray(rows.size) {
                valid[it] && rows[it].getJSONArray("possible_duplicates").length() == 0
            }
        val labels = rows.map { row ->
            val fields = row.optJSONObject("fields")
            val issues = row.getJSONArray("issues")
            val duplicate = row.getJSONArray("possible_duplicates").length() > 0
            val description =
                if (fields == null) ""
                else
                    a.kindLabel(fields.getString("kind")) +
                        " ¥" +
                        ReportRenderer.money(fields.getString("amount_minor")) +
                        " · " +
                        fields.getString("occurred_on") +
                        " · " +
                        fields.optString("note").take(30)
            "${row.getInt("number")}: $description" +
                if (issues.length() > 0) "\n" + issues.toString()
                else if (duplicate) "\n" + a.getString(R.string.possible_duplicate) else ""
        }
        val dialog =
            AlertDialog.Builder(a)
                .setTitle(R.string.import_preview)
                .setMultiChoiceItems(labels.toTypedArray(), selected) { dialog, index, checked ->
                    if (!valid[index]) {
                        (dialog as AlertDialog).listView.setItemChecked(index, false)
                        selected[index] = false
                        Toast.makeText(a, R.string.invalid_import_row, Toast.LENGTH_SHORT).show()
                    } else selected[index] = checked
                }
                .setPositiveButton(R.string.confirm_import, null)
                .setNegativeButton(R.string.cancel, null)
                .create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val indices =
                    JSONArray(
                        rows.indices.filter { selected[it] }.map { rows[it].getInt("number") }
                    )
                AlertDialog.Builder(a)
                    .setTitle(R.string.confirm_import)
                    .setMessage(a.getString(R.string.import_confirmation, indices.length()))
                    .setNegativeButton(R.string.cancel, null)
                    .setPositiveButton(R.string.confirm_save) { _, _ ->
                        val requestId = UUID.randomUUID().toString()
                        a.request(
                            "import_prepare",
                            JSONObject()
                                .put("preview_id", result.getString("preview_id"))
                                .put("selected_rows", indices)
                                .put("request_id", requestId),
                        ) { decision ->
                            a.request("import_commit", decision, true) {
                                dialog.dismiss()
                                a.refresh()
                            }
                        }
                    }
                    .show()
            }
        }
        dialog.show()
    }

    private fun history() {
        val batches = a.jsonRows(a.snapshot.getJSONArray("import_batches"))
        AlertDialog.Builder(a)
            .setTitle(R.string.import_history)
            .setItems(
                batches
                    .map {
                        it.getString("source_file_name") +
                            " · " +
                            it.getInt("accepted_row_count") +
                            " · " +
                            a.getString(
                                if (it.getString("status") == "committed") R.string.committed
                                else R.string.reverted
                            )
                    }
                    .toTypedArray()
            ) { _, index ->
                val batch = batches[index]
                if (batch.getString("status") != "committed") return@setItems
                AlertDialog.Builder(a)
                    .setTitle(R.string.revert_import)
                    .setMessage(R.string.revert_note)
                    .setNegativeButton(R.string.cancel, null)
                    .setPositiveButton(R.string.confirm_save) { _, _ ->
                        a.transactions.mutate(
                            "import.revert.v1",
                            JSONObject()
                                .put("id", batch.getString("id"))
                                .put("expected_version", batch.getInt("version")),
                        )
                    }
                    .show()
            }
            .setNegativeButton(R.string.close, null)
            .show()
    }
}
