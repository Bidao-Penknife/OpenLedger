package org.openledger.android

import android.app.AlertDialog
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.ScrollView
import java.util.UUID
import org.json.JSONArray
import org.json.JSONObject

/** Transaction details, versioned edits and conserved transfer/refund forms. */
class TransactionActions(private val a: MainActivity) {
    fun command(type: String, payload: JSONObject): JSONObject =
        JSONObject()
            .put("request_id", UUID.randomUUID().toString())
            .put("command", type)
            .put("payload", payload)

    fun mutate(type: String, payload: JSONObject, done: () -> Unit = { a.refresh() }) {
        a.request("mutate", command(type, payload), true) { done() }
    }

    fun tags(form: LinearLayout, selected: JSONArray? = null): () -> JSONArray {
        val ids =
            selected?.let { (0 until it.length()).map { i -> it.getString(i) }.toMutableSet() }
                ?: mutableSetOf()
        val rows =
            a.jsonRows(a.snapshot.getJSONObject("catalogs").getJSONArray("tag")).filter {
                it.optInt("is_archived") == 0 || it.getString("id") in ids
            }
        val button = a.button(a.getString(R.string.tags)) {}
        fun update() {
            button.text =
                a.getString(R.string.tags) +
                    ": " +
                    rows.filter { it.getString("id") in ids }.joinToString { it.getString("name") }
        }
        update()
        button.setOnClickListener {
            val edited = ids.toMutableSet()
            AlertDialog.Builder(a)
                .setTitle(R.string.tags)
                .setMultiChoiceItems(
                    rows.map { it.getString("name") }.toTypedArray(),
                    BooleanArray(rows.size) { rows[it].getString("id") in ids },
                ) { _, index, checked ->
                    if (checked) edited.add(rows[index].getString("id"))
                    else edited.remove(rows[index].getString("id"))
                }
                .setPositiveButton(R.string.confirm_save) { _, _ ->
                    ids.clear()
                    ids.addAll(edited)
                    update()
                }
                .setNegativeButton(R.string.cancel, null)
                .show()
        }
        form.addView(button)
        return { JSONArray(ids.sorted()) }
    }

    fun detail(id: String) {
        a.request("transaction_detail", JSONObject().put("id", id)) { value ->
            val form = a.column().apply { setPadding(a.dp(20), a.dp(8), a.dp(20), a.dp(12)) }
            val kind = value.getString("kind")
            form.addView(
                a.label(
                    "${a.kindLabel(kind)} ${CurrencyCatalog.label(value.getString("currency_code"))} ${a.money(value.getLong("amount_minor"), value.getString("currency_code"))}",
                    24,
                    true,
                )
            )
            form.addView(
                a.label(value.getString("occurred_on") + " · " + value.getString("time_zone"), 14)
            )
            for ((key, title) in
                listOf(
                    "book_id" to R.string.book,
                    "category_id" to R.string.category,
                    "payment_method_id" to R.string.payment,
                )) {
                val entity =
                    if (key == "payment_method_id") "payment_method" else key.removeSuffix("_id")
                val name =
                    a.jsonRows(a.snapshot.getJSONObject("catalogs").getJSONArray(entity))
                        .firstOrNull { it.getString("id") == a.nullable(value, key) }
                        ?.getString("name")
                if (name != null) form.addView(a.label(a.getString(title) + ": " + name, 14))
            }
            for (entry in a.jsonRows(value.getJSONArray("entries"))) {
                val account =
                    a.jsonRows(a.snapshot.getJSONArray("accounts")).first {
                        it.getString("id") == entry.getString("account_id")
                    }
                val name = account.getString("name")
                val code = account.getString("currency_code")
                form.addView(
                    a.label(
                        "$name: ${CurrencyCatalog.label(code)} ${a.money(entry.getLong("delta_minor"), code)}",
                        14,
                    )
                )
            }
            for ((key, title) in
                listOf(
                    "note" to R.string.note,
                    "counterparty" to R.string.counterparty,
                    "merchant" to R.string.merchant,
                    "location" to R.string.location,
                    "source_text" to R.string.original_text,
                )) {
                a.nullable(value, key)
                    ?.takeIf { it.isNotBlank() }
                    ?.let { form.addView(a.label(a.getString(title) + ": " + it, 14)) }
            }
            val tagIds = value.getJSONArray("tag_ids")
            val tagNames =
                a.jsonRows(a.snapshot.getJSONObject("catalogs").getJSONArray("tag"))
                    .filter { row ->
                        (0 until tagIds.length()).any {
                            tagIds.getString(it) == row.getString("id")
                        }
                    }
                    .joinToString { it.getString("name") }
            form.addView(a.label(a.getString(R.string.tags) + ": " + tagNames, 14))
            val dialog =
                AlertDialog.Builder(a)
                    .setTitle(R.string.detail_title)
                    .setView(ScrollView(a).apply { addView(form) })
                    .setNegativeButton(R.string.close, null)
                    .create()
            val deleted = !value.isNull("deleted_at_utc")
            a.attachments.render(form, value) { dialog.dismiss() }
            if (!deleted && kind in listOf("income", "expense", "transfer", "expense_refund")) {
                form.addView(
                    a.button(a.getString(R.string.edit_record)) {
                        dialog.dismiss()
                        when (kind) {
                            "transfer" -> transfer(value)
                            "expense_refund" ->
                                refund(value.getString("original_transaction_id"), value)
                            else -> {
                                val draft = JSONObject()
                                draft.put("currency_code", value.getString("currency_code"))
                                for (key in
                                    listOf(
                                        "kind",
                                        "amount_minor",
                                        "book_id",
                                        "category_id",
                                        "payment_method_id",
                                        "occurred_on",
                                        "occurred_at_utc",
                                        "time_period",
                                        "note",
                                        "counterparty",
                                        "merchant",
                                        "location",
                                    )) draft.put(key, JSONObject().put("value", value.opt(key)))
                                draft.put(
                                    "account_id",
                                    JSONObject()
                                        .put(
                                            "value",
                                            value
                                                .getJSONArray("entries")
                                                .getJSONObject(0)
                                                .getString("account_id"),
                                        ),
                                )
                                a.editTransaction(draft, value)
                            }
                        }
                    }
                )
            }
            if (!deleted && kind == "expense")
                form.addView(
                    a.button(a.getString(R.string.refund)) {
                        dialog.dismiss()
                        refund(id)
                    }
                )
            if (kind != "opening")
                form.addView(
                    a.button(
                        a.getString(
                            if (deleted) R.string.restore_record else R.string.delete_record
                        )
                    ) {
                        AlertDialog.Builder(a)
                            .setTitle(
                                if (deleted) R.string.restore_record else R.string.delete_record
                            )
                            .setMessage(R.string.delete_note)
                            .setNegativeButton(R.string.cancel, null)
                            .setPositiveButton(R.string.confirm_save) { _, _ ->
                                mutate(
                                    "transaction.${if (deleted) "restore" else "delete"}.v1",
                                    JSONObject()
                                        .put("id", id)
                                        .put("expected_version", value.getInt("version")),
                                ) {
                                    dialog.dismiss()
                                    a.refresh()
                                }
                            }
                            .show()
                    }
                )
            dialog.show()
        }
    }

    fun transfer(existing: JSONObject? = null, capture: JSONObject? = null) =
        financial("transfer", null, existing, capture)

    fun refund(originalId: String, existing: JSONObject? = null, capture: JSONObject? = null) =
        financial("refund", originalId, existing, capture)

    private fun financial(
        type: String,
        originalId: String?,
        existing: JSONObject?,
        capture: JSONObject?,
    ) {
        val form = a.column().apply { setPadding(a.dp(20), a.dp(8), a.dp(20), a.dp(12)) }
        val entries = existing?.getJSONArray("entries")?.let(a::jsonRows).orEmpty()
        val fromId = entries.firstOrNull { it.getLong("delta_minor") < 0 }?.getString("account_id")
        val toId = entries.firstOrNull { it.getLong("delta_minor") > 0 }?.getString("account_id")
        val accounts =
            a.jsonRows(a.snapshot.getJSONArray("accounts")).filter {
                it.optInt("is_archived") == 0 || it.getString("id") in listOf(fromId, toId)
            }
        val from = a.choices(accounts, fromId).apply { id = R.id.from_account_input }
        val to = a.choices(accounts, toId).apply { id = R.id.to_account_input }
        val amount =
            a.edit(
                a.getString(R.string.amount),
                capture?.optString("selected_amount")
                    ?: existing?.getLong("amount_minor")?.let {
                        a.money(it, existing.getString("currency_code"))
                    }
                    ?: "",
                amount = true,
            )
        var day = existing?.getString("occurred_on") ?: a.snapshot.getString("today")
        val date = a.button(day) {}
        date.setOnClickListener {
            a.datePicker(day) {
                day = it
                date.text = it
            }
        }
        val note =
            a.edit(a.getString(R.string.note), existing?.optString("note") ?: "", multiline = true)
        form.addView(
            a.label(
                a.getString(
                    if (type == "transfer") R.string.transfer_note else R.string.refund_note
                ),
                14,
            )
        )
        if (type == "transfer") {
            form.addView(a.label(a.getString(R.string.from_account), 13))
            form.addView(from)
        }
        form.addView(a.label(a.getString(R.string.to_account), 13))
        form.addView(to)
        form.addView(amount)
        val incoming =
            a.edit(
                a.getString(R.string.fx_incoming),
                existing
                    ?.takeIf { it.optString("currency_code") != it.optString("to_currency_code") }
                    ?.optString("to_amount_minor")
                    ?.takeIf { it != "null" }
                    ?.let { ReportRenderer.money(it, existing.getString("to_currency_code")) }
                    ?: "",
                amount = true,
            )
        if (type == "transfer") {
            form.addView(a.label(a.getString(R.string.fx_note), 13))
            form.addView(incoming)
        }
        form.addView(date)
        form.addView(note)
        val payments =
            a.selectable("payment_method", existing?.let { a.nullable(it, "payment_method_id") })
        val payment =
            a.choices(
                payments,
                existing?.let { a.nullable(it, "payment_method_id") },
                a.getString(R.string.no_payment),
            )
        if (type == "refund") form.addView(payment)
        val selectedTags = tags(form, existing?.optJSONArray("tag_ids"))
        a.confirmation(if (type == "transfer") R.string.transfer else R.string.refund, form) {
            dialog ->
            val fields =
                JSONObject()
                    .put("amount", amount.text.toString())
                    .put("occurred_on", day)
                    .put(
                        "time_zone",
                        existing?.getString("time_zone") ?: a.snapshot.getString("time_zone"),
                    )
                    .put("note", note.text.toString())
                    .put("tag_ids", selectedTags())
            if (existing != null) {
                fields
                    .put("source", existing.getString("source"))
                    .put("source_text", existing.opt("source_text"))
                if (day == existing.getString("occurred_on")) {
                    fields
                        .put("occurrence_precision", existing.getString("occurrence_precision"))
                        .put("time_period", existing.opt("time_period"))
                        .put("occurred_at_utc", existing.opt("occurred_at_utc"))
                }
            }
            if (type == "transfer")
                fields
                    .put("from_account_id", a.selectedId(from, accounts))
                    .put("to_account_id", a.selectedId(to, accounts))
            else
                fields
                    .put("original_transaction_id", originalId)
                    .put("account_id", a.selectedId(to, accounts))
                    .put("payment_method_id", a.selectedId(payment, payments))
            if (type == "transfer" && incoming.text.isNotBlank())
                fields.put("to_amount", incoming.text.toString())
            if (capture != null) {
                fields.put("kind", if (type == "transfer") "transfer" else "expense_refund")
                a.request(
                    "capture_record",
                    JSONObject()
                        .put("request_id", UUID.randomUUID().toString())
                        .put("id", capture.getString("id"))
                        .put("expected_version", capture.getInt("version"))
                        .put("fields", fields),
                    true,
                ) {
                    dialog.dismiss()
                    a.refresh()
                }
                return@confirmation
            }
            val payload =
                JSONObject()
                    .put("id", existing?.getString("id") ?: UUID.randomUUID().toString())
                    .put("fields", fields)
            if (existing != null) payload.put("expected_version", existing.getInt("version"))
            mutate("$type.${if (existing == null) "record" else "update"}.v1", payload) {
                dialog.dismiss()
                a.refresh()
            }
        }
    }

    fun filters(current: JSONObject, done: (JSONObject) -> Unit) {
        val form = a.column().apply { setPadding(a.dp(20), a.dp(8), a.dp(20), a.dp(12)) }
        val selection = mutableListOf<Triple<String, android.widget.Spinner, List<JSONObject>>>()
        for ((entity, title) in
            listOf(
                "book" to R.string.book,
                "account" to R.string.account,
                "category" to R.string.category,
                "tag" to R.string.tags,
            )) {
            val rows = a.jsonRows(a.snapshot.getJSONObject("catalogs").getJSONArray(entity))
            val choice =
                a.choices(
                    rows,
                    a.nullable(current, "${entity}_id"),
                    a.getString(R.string.all_items),
                )
            form.addView(a.label(a.getString(title), 13))
            form.addView(choice)
            selection.add(Triple(entity, choice, rows))
        }
        val kinds =
            listOf(null, "income", "expense", "expense_refund", "transfer", "opening", "adjustment")
        val kind =
            a.spinner(
                kinds.map { if (it == null) a.getString(R.string.all_items) else a.kindLabel(it) }
            )
        kind.setSelection(kinds.indexOf(a.nullable(current, "kind")).coerceAtLeast(0))
        val start = a.edit(a.getString(R.string.start_on), current.optString("start_on"))
        val end = a.edit(a.getString(R.string.end_on), current.optString("end_on"))
        val deleted =
            CheckBox(a).apply {
                text = a.getString(R.string.show_deleted)
                isChecked = current.optBoolean("include_deleted")
            }
        form.addView(kind)
        form.addView(start)
        form.addView(end)
        form.addView(deleted)
        a.confirmation(R.string.filter_title, form) { dialog ->
            val result = JSONObject().put("include_deleted", deleted.isChecked)
            for ((entity, choice, rows) in selection) result.put(
                "${entity}_id",
                a.selectedId(choice, rows),
            )
            result.put("kind", kinds[kind.selectedItemPosition] ?: JSONObject.NULL)
            if (start.text.isNotBlank()) result.put("start_on", start.text.toString())
            if (end.text.isNotBlank()) result.put("end_on", end.text.toString())
            done(result)
            dialog.dismiss()
        }
    }
}
