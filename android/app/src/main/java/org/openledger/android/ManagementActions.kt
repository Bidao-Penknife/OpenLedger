package org.openledger.android

import android.app.AlertDialog
import java.util.UUID
import org.json.JSONObject

/** Catalog maintenance keeps history by archiving instead of destructive removal. */
class ManagementActions(private val a: MainActivity) {
    private val entities = listOf("account", "book", "category", "tag", "payment_method")
    private val titles =
        listOf(R.string.assets, R.string.book, R.string.category, R.string.tags, R.string.payment)

    fun choose() {
        AlertDialog.Builder(a)
            .setTitle(R.string.manage_title)
            .setItems(titles.map(a::getString).toTypedArray()) { _, index -> list(entities[index]) }
            .show()
    }

    private fun list(entity: String) {
        val rows = a.jsonRows(a.snapshot.getJSONObject("catalogs").getJSONArray(entity))
        val names = rows.map {
            it.getString("name") +
                if (it.optInt("is_archived") != 0) " (${a.getString(R.string.archived)})" else ""
        }
        AlertDialog.Builder(a)
            .setTitle(titles[entities.indexOf(entity)])
            .setItems((listOf(a.getString(R.string.create_item)) + names).toTypedArray()) { _, index
                ->
                if (index == 0) edit(entity, null) else actions(entity, rows[index - 1])
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun actions(entity: String, item: JSONObject) {
        val choices =
            mutableListOf(
                R.string.edit_item,
                if (item.optInt("is_archived") == 0) R.string.archive_item
                else R.string.unarchive_item,
            )
        if (entity in listOf("book", "account") && item.optInt("is_archived") == 0)
            choices.add(R.string.set_default)
        if (entity == "account" && item.optInt("is_archived") == 0)
            choices.addAll(
                listOf(R.string.opening_edit, R.string.adjust_balance, R.string.account_history)
            )
        AlertDialog.Builder(a)
            .setTitle(item.getString("name"))
            .setItems(choices.map(a::getString).toTypedArray()) { _, index ->
                when (choices[index]) {
                    R.string.edit_item -> edit(entity, item)
                    R.string.archive_item,
                    R.string.unarchive_item -> archive(entity, item)
                    R.string.set_default ->
                        a.transactions.mutate("$entity.default.set.v1", version(item))
                    R.string.opening_edit -> balance(item, true)
                    R.string.adjust_balance -> balance(item, false)
                    R.string.account_history ->
                        a.showTransactions(JSONObject().put("account_id", item.getString("id")))
                }
            }
            .show()
    }

    private fun version(item: JSONObject) =
        JSONObject().put("id", item.getString("id")).put("expected_version", item.getInt("version"))

    private fun archive(entity: String, item: JSONObject) {
        val payload = version(item).put("archived", item.optInt("is_archived") == 0)
        val defaultId = a.snapshot.getJSONObject("preferences").optString("default_${entity}_id")
        val replacements =
            a.selectable(entity).filter { it.getString("id") != item.getString("id") }
        val form = a.column().apply { setPadding(a.dp(20), a.dp(8), a.dp(20), a.dp(12)) }
        form.addView(a.label(a.getString(R.string.archive_note), 14))
        val replacement = a.choices(replacements, null)
        val needsReplacement =
            entity in listOf("book", "account") &&
                defaultId == item.getString("id") &&
                payload.getBoolean("archived")
        if (needsReplacement) {
            form.addView(a.label(a.getString(R.string.replacement_default), 14))
            form.addView(replacement)
        }
        a.confirmation(
            if (payload.getBoolean("archived")) R.string.archive_item else R.string.unarchive_item,
            form,
        ) { dialog ->
            if (needsReplacement)
                payload.put("replacement_default_id", a.selectedId(replacement, replacements))
            a.transactions.mutate("$entity.archive.v1", payload) {
                dialog.dismiss()
                a.refresh()
            }
        }
    }

    private fun edit(entity: String, item: JSONObject?) {
        val form = a.column().apply { setPadding(a.dp(20), a.dp(8), a.dp(20), a.dp(12)) }
        val name = a.edit(a.getString(R.string.item_name), item?.getString("name") ?: "")
        val description =
            a.edit(
                a.getString(R.string.description),
                item?.optString("description") ?: "",
                multiline = true,
            )
        val order =
            a.edit(a.getString(R.string.sort_order), item?.optInt("sort_order")?.toString() ?: "0")
        val color =
            a.edit(a.getString(R.string.color_hint), item?.let { a.nullable(it, "color") } ?: "")
        val types = listOf("cash", "bank", "wechat", "alipay", "custom")
        val accountType =
            a.spinner(
                listOf(
                        R.string.cash,
                        R.string.bank,
                        R.string.wechat,
                        R.string.alipay,
                        R.string.custom,
                    )
                    .map(a::getString)
            )
        accountType.setSelection(
            types.indexOf(item?.optString("account_type") ?: "cash").coerceAtLeast(0)
        )
        val kind = a.spinner(listOf(a.getString(R.string.expense), a.getString(R.string.income)))
        kind.setSelection(if (item?.optString("transaction_kind") == "income") 1 else 0)
        val parents =
            a.selectable("category").filter {
                it.isNull("parent_id") && it.getString("id") != item?.optString("id")
            }
        val parent =
            a.choices(
                parents,
                item?.let { a.nullable(it, "parent_id") },
                a.getString(R.string.no_parent),
            )
        val accounts = a.selectable("account", item?.let { a.nullable(it, "default_account_id") })
        val account =
            a.choices(
                accounts,
                item?.let { a.nullable(it, "default_account_id") },
                a.getString(R.string.no_binding),
            )
        val opening =
            a.edit(a.getString(R.string.opening_amount), "0.00", amount = true, signed = true)
        var day = a.snapshot.getString("today")
        val date = a.button(day) {}
        date.setOnClickListener {
            a.datePicker(day) {
                day = it
                date.text = it
            }
        }
        form.addView(name)
        form.addView(a.label(a.getString(R.string.sort_order), 13))
        form.addView(order)
        if (entity in listOf("book", "account")) form.addView(description)
        if (entity == "account") {
            form.addView(accountType)
            if (item == null) {
                form.addView(opening)
                form.addView(date)
            }
        }
        if (entity == "category") {
            form.addView(kind)
            form.addView(a.label(a.getString(R.string.parent_category), 13))
            form.addView(parent)
        }
        if (entity in listOf("category", "tag")) form.addView(color)
        if (entity == "payment_method") {
            form.addView(a.label(a.getString(R.string.payment_binding), 14))
            form.addView(account)
        }
        a.confirmation(if (item == null) R.string.create_item else R.string.edit_item, form) {
            dialog ->
            val sort = order.text.toString().toIntOrNull()
            if (sort == null || sort < 0) {
                order.error = a.getString(R.string.invalid_fields)
                return@confirmation
            }
            val payload =
                JSONObject()
                    .put("id", item?.getString("id") ?: UUID.randomUUID().toString())
                    .put("name", name.text.toString())
                    .put("sort_order", sort)
            if (item != null) payload.put("expected_version", item.getInt("version"))
            if (entity in listOf("book", "account"))
                payload.put("description", description.text.toString())
            if (entity == "account") {
                payload.put("account_type", types[accountType.selectedItemPosition])
                if (item == null)
                    payload
                        .put("opening_amount", opening.text.toString())
                        .put("balance_start_on", day)
            }
            if (entity == "category")
                payload
                    .put("kind", if (kind.selectedItemPosition == 0) "expense" else "income")
                    .put("parent_id", a.selectedId(parent, parents))
            if (entity in listOf("category", "tag"))
                payload.put(
                    "color",
                    color.text.toString().takeIf { it.isNotBlank() } ?: JSONObject.NULL,
                )
            if (entity == "payment_method") {
                if (item == null) payload.put("code", "custom-${UUID.randomUUID()}")
                payload.put("default_account_id", a.selectedId(account, accounts))
            }
            a.transactions.mutate(
                "$entity.${if (item == null) "create" else "update"}.v1",
                payload,
            ) {
                dialog.dismiss()
                a.refresh()
            }
        }
    }

    private fun balance(item: JSONObject, opening: Boolean) {
        a.request("account_detail", JSONObject().put("id", item.getString("id"))) { account ->
            val form = a.column().apply { setPadding(a.dp(20), a.dp(8), a.dp(20), a.dp(12)) }
            form.addView(
                a.label(
                    a.getString(if (opening) R.string.opening_note else R.string.adjust_note),
                    14,
                )
            )
            val value =
                a.edit(
                    a.getString(if (opening) R.string.opening_amount else R.string.target_balance),
                    a.money(
                        account.getLong(if (opening) "opening_balance_minor" else "balance_minor")
                    ),
                    amount = true,
                    signed = true,
                )
            var day =
                if (opening) account.getString("balance_start_on")
                else a.snapshot.getString("today")
            val date = a.button(day) {}
            date.setOnClickListener {
                if (opening)
                    a.datePicker(day) {
                        day = it
                        date.text = it
                    }
            }
            val reason = a.edit(a.getString(R.string.adjust_reason), multiline = true)
            form.addView(value)
            form.addView(date)
            if (!opening) form.addView(reason)
            a.confirmation(if (opening) R.string.opening_edit else R.string.adjust_balance, form) {
                dialog ->
                val payload = JSONObject().put("account_id", account.getString("id"))
                if (opening)
                    payload
                        .put("expected_account_version", account.getInt("version"))
                        .put("opening_amount", value.text.toString())
                        .put("balance_start_on", day)
                else
                    payload
                        .put("target_balance", value.text.toString())
                        .put("occurred_on", day)
                        .put("time_zone", a.snapshot.getString("time_zone"))
                        .put("reason", reason.text.toString())
                a.transactions.mutate(
                    if (opening) "account.opening.set.v1" else "account.adjust.v1",
                    payload,
                ) {
                    dialog.dismiss()
                    a.refresh()
                }
            }
        }
    }
}
