package org.openledger.android

import android.app.Activity
import android.app.AlertDialog
import android.app.DatePickerDialog
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.*
import java.util.Calendar
import java.util.Locale
import java.util.UUID
import org.json.JSONArray
import org.json.JSONObject

/** A touch UI: all financial decisions and mutations live in the shared Python core. */
class MainActivity : Activity() {
    private val ledger
        get() = application as LedgerApplication

    private lateinit var root: LinearLayout
    internal lateinit var content: LinearLayout
    private var quickInput: EditText? = null
    internal var snapshot = JSONObject()
    private var tab = "quick"
    private var page = 0
    private var search = ""
    private var inputText = ""
    private var busy = false
    internal val files = FileActions(this)
    internal val transactions = TransactionActions(this)
    internal val management = ManagementActions(this)
    internal val analysis = AnalysisActions(this)
    internal val exchange = ExchangeActions(this)
    internal val settings = SettingsActions(this)
    internal val attachments = AttachmentActions(this)
    internal val captures = CaptureActions(this)
    internal val currencies = CurrencyActions(this)
    private var filters = JSONObject()
    private val dark
        get() =
            resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK ==
                Configuration.UI_MODE_NIGHT_YES

    private val ink
        get() = Color.parseColor(if (dark) "#E6EDF3" else "#243342")

    internal val muted
        get() = Color.parseColor(if (dark) "#ABB8C4" else "#627283")

    private val surface
        get() = Color.parseColor(if (dark) "#202833" else "#FFFFFF")

    private val accent
        get() = Color.parseColor(if (dark) "#87CEBB" else "#256F62")

    private val backdrop
        get() = Color.parseColor(if (dark) "#161B22" else "#F6F7F9")

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(AppPreferences.context(newBase))
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent.action == QuickEntry.ACTION) {
            tab = "quick"
            render()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        setTheme(R.style.AppTheme)
        super.onCreate(savedInstanceState)
        QuickEntry.publish(this)
        files.restoreState(savedInstanceState)
        analysis.restore(savedInstanceState)
        filters = JSONObject(savedInstanceState?.getString("filters") ?: "{}")
        inputText = savedInstanceState?.getString("input") ?: ""
        tab = savedInstanceState?.getString("tab") ?: "quick"
        if (intent.action == QuickEntry.ACTION) tab = "quick"
        page = savedInstanceState?.getInt("page") ?: 0
        search = savedInstanceState?.getString("search") ?: ""
        root =
            column().apply {
                setBackgroundColor(backdrop)
                setPadding(dp(20), dp(12), dp(20), 0)
            }
        root.setOnApplyWindowInsetsListener { view, insets ->
            view.setPadding(
                dp(20),
                insets.systemWindowInsetTop + dp(12),
                dp(20),
                insets.systemWindowInsetBottom,
            )
            insets
        }
        root.addView(label(getString(R.string.app_name), 27, true))
        root.addView(label(getString(R.string.subtitle), 13, color = muted))
        val navigation = row().apply { isBaselineAligned = false }
        listOf(
                "quick" to R.string.nav_quick,
                "transactions" to R.string.nav_transactions,
                "assets" to R.string.nav_assets,
                "analysis" to R.string.nav_analysis,
                "settings" to R.string.settings,
            )
            .forEach { (key, title) ->
                navigation.addView(
                    button(getString(title)) {
                            inputText = quickInput?.text?.toString() ?: inputText
                            tab = key
                            render()
                        }
                        .apply {
                            textSize = 13f
                            setPadding(dp(2), 0, dp(2), 0)
                            maxLines = 1
                            ellipsize = android.text.TextUtils.TruncateAt.END
                        },
                    LinearLayout.LayoutParams(0, dp(56), 1f),
                )
            }
        root.addView(navigation)
        content = column()
        root.addView(
            ScrollView(this).apply {
                isFillViewport = true
                addView(content)
            },
            LinearLayout.LayoutParams(-1, 0, 1f),
        )
        setContentView(root)
        root.requestApplyInsets()
        content.addView(label(getString(R.string.loading), 16))
        refresh()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        files.saveState(outState)
        analysis.save(outState)
        outState.putString("filters", filters.toString())
        outState.putString("input", quickInput?.text?.toString() ?: inputText)
        outState.putString("tab", tab)
        outState.putInt("page", page)
        outState.putString("search", search)
        super.onSaveInstanceState(outState)
    }

    override fun onStop() {
        captures.voice.close()
        super.onStop()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        captures.voice.permission(requestCode, grantResults)
    }

    internal fun refresh() {
        request(
            "snapshot",
            JSONObject(filters.toString()).put("page", page).put("search", search),
        ) {
            snapshot = it
            analysis.invalidate()
            render()
            onboarding()
        }
    }

    private fun onboarding() {
        val preferences = getSharedPreferences("presentation", MODE_PRIVATE)
        if (
            preferences.getBoolean("onboarding_seen", false) ||
                snapshot.getJSONArray("accounts").length() != 0
        )
            return
        preferences.edit().putBoolean("onboarding_seen", true).apply()
        AlertDialog.Builder(this)
            .setTitle(R.string.onboarding_title)
            .setMessage(R.string.onboarding_note)
            .setPositiveButton(R.string.onboarding_read) { _, _ ->
                startActivity(Intent(this, HelpActivity::class.java))
            }
            .setNeutralButton(R.string.new_account) { _, _ -> newAccount() }
            .setNegativeButton(R.string.ok, null)
            .show()
    }

    override fun onResume() {
        super.onResume()
        if (snapshot.length() > 0 && tab == "settings" && !busy) render()
    }

    internal fun request(
        action: String,
        body: JSONObject = JSONObject(),
        confirmed: Boolean = false,
        done: (JSONObject) -> Unit,
    ) {
        if (busy) return
        busy = true
        ledger.worker.execute {
            val reply =
                try {
                    if (confirmed) ledger.confirmed(action, body) else ledger.call(action, body)
                } catch (_: Exception) {
                    JSONObject()
                        .put("ok", false)
                        .put("error", JSONObject().put("code", "STORAGE_IO_ERROR"))
                }
            runOnUiThread {
                busy = false
                if (isDestroyed || isFinishing) return@runOnUiThread
                if (reply.optBoolean("ok")) done(reply.getJSONObject("data"))
                else {
                    Toast.makeText(
                            this,
                            errorText(reply.getJSONObject("error").optString("code")),
                            Toast.LENGTH_LONG,
                        )
                        .show()
                    if (snapshot.length() == 0 || ledger.pending() != null) render()
                }
            }
        }
    }

    private fun render() {
        content.removeAllViews()
        quickInput = null
        if (snapshot.length() == 0) {
            content.addView(button(getString(R.string.refresh)) { refresh() })
            return
        }
        if (ledger.pending() != null) {
            val warning = card()
            warning.addView(label(getString(R.string.pending_note), 14))
            warning.addView(
                button(getString(R.string.retry_pending)) {
                    val pending = ledger.pending() ?: return@button
                    request(pending.getString("action"), pending.getJSONObject("body"), true) {
                        refresh()
                    }
                }
            )
            content.addView(warning)
        }
        val overview = snapshot.getJSONObject("overview")
        val displayCode = overview.getString("currency_code")
        val summary = card()
        summary.addView(label(getString(R.string.total_assets), 14, color = muted))
        summary.addView(
            label(
                if (overview.getBoolean("assets_complete"))
                    "${CurrencyCatalog.label(displayCode)} ${ReportRenderer.money(overview.getString("total_assets_minor"), displayCode)}"
                else getString(R.string.valuation_incomplete),
                32,
                true,
                accent,
            )
        )
        summary.addView(
            label(
                getString(
                    R.string.month_totals,
                    snapshot.getString("today").take(7),
                    if (overview.getBoolean("income_complete"))
                        CurrencyCatalog.label(displayCode) +
                            ReportRenderer.money(overview.getString("income_minor"), displayCode)
                    else "—",
                    if (overview.getBoolean("expense_complete"))
                        CurrencyCatalog.label(displayCode) +
                            ReportRenderer.money(overview.getString("expense_minor"), displayCode)
                    else "—",
                ),
                13,
                color = muted,
            )
        )
        content.addView(summary)
        if (overview.getJSONArray("missing_rates").length() > 0)
            content.addView(label(getString(R.string.rate_missing), 13, color = muted))
        when (tab) {
            "quick" -> renderQuick()
            "transactions" -> renderTransactions()
            "assets" -> renderAssets()
            "settings" -> renderSettings()
            "analysis" -> analysis.render()
        }
    }

    internal fun fileBusy(value: Boolean) {
        busy = value
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        files.result(requestCode, resultCode, data)
    }

    internal fun importedFile(operation: String, filename: String) {
        if (operation == "restore") {
            request("backup_prepare", JSONObject().put("filename", filename)) {
                AlertDialog.Builder(this)
                    .setTitle(R.string.restore_title)
                    .setMessage(R.string.restore_note)
                    .setNegativeButton(R.string.cancel, null)
                    .setPositiveButton(R.string.confirm_save) { _, _ ->
                        request(
                            "backup_restore",
                            JSONObject()
                                .put("filename", filename)
                                .put("request_id", UUID.randomUUID().toString()),
                            true,
                        ) {
                            filters = JSONObject()
                            page = 0
                            analysis.invalidate(true)
                            refresh()
                        }
                    }
                    .show()
            }
        } else if (operation.startsWith("attachment:")) attachments.imported(operation, filename)
        else if (operation == "ocr") captures.ocr.imported(filename)
        else if (operation == "import" || operation == "import_gb")
            exchange.mapping(filename, if (operation == "import_gb") "gb18030" else "utf-8-sig")
    }

    private fun renderSettings() {
        val panel = card()
        panel.addView(label(getString(R.string.backup_title), 20, true))
        panel.addView(label(getString(R.string.backup_note), 14, color = muted))
        panel.addView(
            button(getString(R.string.backup_create)) {
                val filename =
                    "OpenLedger-${snapshot.getString("today")}-${UUID.randomUUID()}.olbackup"
                request("backup_create", JSONObject().put("filename", filename)) {
                    files.export(it.getString("filename"), "application/zip")
                }
            }
        )
        panel.addView(
            button(getString(R.string.restore_title)) {
                if (ledger.pending() != null)
                    Toast.makeText(this, R.string.pending_error, Toast.LENGTH_LONG).show()
                else files.pick("restore", "olbackup")
            }
        )
        panel.addView(
            label(
                getString(R.string.active_data, ledger.directoryLabel(ledger.directory, this)),
                13,
                color = muted,
            )
        )
        panel.addView(
            button(getString(R.string.switch_data)) {
                val directories = ledger.directories()
                AlertDialog.Builder(this)
                    .setTitle(R.string.switch_data)
                    .setItems(directories.map { ledger.directoryLabel(it, this) }.toTypedArray()) {
                        _,
                        index ->
                        fileBusy(true)
                        ledger.worker.execute {
                            val changed = runCatching {
                                ledger.switchDirectory(directories[index])
                            }
                                .isSuccess
                            runOnUiThread {
                                fileBusy(false)
                                if (changed) {
                                    page = 0
                                    search = ""
                                    filters = JSONObject()
                                    analysis.invalidate(true)
                                    refresh()
                                } else
                                    Toast.makeText(this, R.string.pending_error, Toast.LENGTH_LONG)
                                        .show()
                            }
                        }
                    }
                    .show()
            }
        )
        content.addView(panel)
        val intelligent = card()
        captures.settings(intelligent)
        currencies.render(intelligent)
        content.addView(intelligent)
        exchange.render()
        settings.render()
        content.addView(
            button(getString(R.string.help_title)) {
                startActivity(Intent(this, HelpActivity::class.java))
            }
        )
    }

    internal fun renderCurrent() {
        render()
    }

    private fun renderQuick() {
        content.addView(button(getString(R.string.capture_inbox)) { captures.inbox() })
        val automatic = row()
        automatic.addView(
            button(getString(R.string.ocr_title)) { captures.ocr.pick() },
            LinearLayout.LayoutParams(0, dp(56), 1f),
        )
        automatic.addView(
            button(getString(R.string.voice_title)) { captures.voice.start() },
            LinearLayout.LayoutParams(0, dp(56), 1f),
        )
        content.addView(automatic)
        val panel = card()
        panel.addView(label(getString(R.string.quick_title), 20, true))
        quickInput =
            edit(getString(R.string.quick_hint), inputText, multiline = true).apply {
                id = R.id.quick_input
            }
        panel.addView(quickInput)
        panel.addView(
            button(getString(R.string.parse), true) {
                    inputText = quickInput?.text?.toString() ?: ""
                    if (!requireAccount()) return@button
                    request("preview", JSONObject().put("text", inputText)) { result ->
                        reviewDrafts(result)
                    }
                }
                .apply { id = R.id.parse_button }
        )
        panel.addView(
            button(getString(R.string.manual)) {
                inputText = quickInput?.text?.toString() ?: inputText
                if (requireAccount()) editTransaction(null)
            }
        )
        if (activeAccounts().isEmpty()) {
            panel.addView(label(getString(R.string.no_accounts), 14, color = muted))
            panel.addView(button(getString(R.string.new_account)) { newAccount() })
        }
        content.addView(panel)
        content.addView(
            button(getString(R.string.ai_parse)) {
                inputText = quickInput?.text?.toString() ?: inputText
                if (requireAccount()) settings.parse(inputText)
            }
        )
        content.addView(label(getString(R.string.privacy_note), 13, color = muted))
        content.addView(label(getString(R.string.preview_note), 13, color = muted))
    }

    private fun requireAccount(): Boolean {
        if (activeAccounts().isNotEmpty()) return true
        Toast.makeText(this, R.string.no_accounts, Toast.LENGTH_LONG).show()
        newAccount()
        return false
    }

    internal fun reviewDrafts(result: JSONObject, ai: Boolean = false) {
        val drafts = jsonRows(result.getJSONArray("drafts"))
        if (drafts.isEmpty()) {
            AlertDialog.Builder(this)
                .setMessage(R.string.no_parse_result)
                .setPositiveButton(R.string.manual) { _, _ -> editTransaction(null) }
                .setNegativeButton(R.string.cancel, null)
                .show()
            return
        }
        for (draft in drafts) draft
            .put("parser_source", if (ai) "ai_assisted" else "local_rule")
            .put("keep_source", drafts.size > 1)
        if (drafts.size == 1) editTransaction(drafts[0])
        else
            AlertDialog.Builder(this)
                .setTitle(R.string.select_draft)
                .setItems(
                    drafts
                        .map { draft ->
                            (candidate(draft, "kind")?.let(::kindLabel) ?: "") +
                                " · " +
                                CurrencyCatalog.label(draft.optString("currency_code", "CNY")) +
                                (candidate(draft, "amount_minor")?.let {
                                    ReportRenderer.money(
                                        it,
                                        draft.optString("currency_code", "CNY"),
                                    )
                                } ?: "…")
                        }
                        .toTypedArray()
                ) { _, index ->
                    editTransaction(drafts[index])
                }
                .setNegativeButton(R.string.cancel, null)
                .show()
    }

    internal fun showTransactions(value: JSONObject) {
        filters = value
        page = 0
        tab = "transactions"
        refresh()
    }

    private fun renderTransactions() {
        content.addView(
            button(getString(R.string.filter_title)) {
                transactions.filters(filters) { value ->
                    filters = value
                    page = 0
                    refresh()
                }
            }
        )
        val searchField = edit(getString(R.string.search_hint), search)
        content.addView(searchField)
        content.addView(
            button(getString(R.string.search)) {
                search = searchField.text.toString()
                page = 0
                refresh()
            }
        )
        val transactions = snapshot.getJSONObject("transactions")
        val rows = transactions.getJSONArray("rows")
        if (rows.length() == 0) content.addView(label(getString(R.string.empty_transactions), 16))
        for (index in 0 until rows.length()) {
            val item = rows.getJSONObject(index)
            val kind = item.getString("kind")
            val panel = card()
            panel.setOnClickListener { if (!busy) this.transactions.detail(item.getString("id")) }
            if (!item.isNull("deleted_at_utc"))
                panel.addView(label(getString(R.string.deleted), 13))
            val prefix =
                if (kind == "expense") "−"
                else if (kind in listOf("income", "expense_refund")) "+" else ""
            panel.addView(
                label(
                    "${kindLabel(kind)}  $prefix${CurrencyCatalog.label(item.getString("currency_code"))} ${money(item.getLong("amount_minor"), item.getString("currency_code"))}",
                    19,
                    true,
                    if (kind == "expense") ink else accent,
                )
            )
            panel.addView(
                label(
                    getString(
                        R.string.record_summary,
                        item.getString("occurred_on"),
                        nullable(item, "category_name") ?: kindLabel(kind),
                        nullable(item, "account_name") ?: "",
                    ),
                    13,
                    color = muted,
                )
            )
            nullable(item, "note")?.takeIf { it.isNotBlank() }?.let { panel.addView(label(it, 14)) }
            content.addView(panel)
        }
        content.addView(
            label(
                getString(R.string.pagination, page + 1, transactions.getInt("total")),
                13,
                color = muted,
            )
        )
        val pager = row()
        pager.addView(
            button(getString(R.string.previous)) {
                    page--
                    refresh()
                }
                .apply { isEnabled = page > 0 },
            LinearLayout.LayoutParams(0, dp(56), 1f),
        )
        pager.addView(
            button(getString(R.string.next)) {
                    page++
                    refresh()
                }
                .apply { isEnabled = (page + 1) * 25 < transactions.getInt("total") },
            LinearLayout.LayoutParams(0, dp(56), 1f),
        )
        content.addView(pager)
    }

    private fun renderAssets() {
        val balances = snapshot.getJSONObject("overview").getJSONObject("balances")
        val accounts = snapshot.getJSONArray("accounts")
        for (index in 0 until accounts.length()) {
            val account = accounts.getJSONObject(index)
            val panel = card()
            panel.addView(label(account.getString("name"), 18, true))
            panel.addView(
                label(
                    "${CurrencyCatalog.label(account.getString("currency_code"))} ${money(balances.getLong(account.getString("id")), account.getString("currency_code"))}",
                    25,
                    true,
                    accent,
                )
            )
            if (account.optInt("is_archived") != 0)
                panel.addView(label(getString(R.string.archived), 13, color = muted))
            content.addView(panel)
        }
        content.addView(button(getString(R.string.new_account), true) { newAccount() })
        content.addView(button(getString(R.string.new_book)) { newBook() })
        content.addView(button(getString(R.string.transfer)) { transactions.transfer() })
        content.addView(button(getString(R.string.manage_title)) { management.choose() })
        content.addView(label(getString(R.string.privacy_note), 13, color = muted))
    }

    private fun newAccount() {
        if (ledger.pending() != null) {
            Toast.makeText(this, R.string.pending_error, Toast.LENGTH_LONG).show()
            return
        }
        val form = column().apply { setPadding(dp(20), dp(8), dp(20), 0) }
        val name = edit(getString(R.string.account_name))
        val opening = edit(getString(R.string.opening_amount), "0", amount = true, signed = true)
        val code = spinner(CurrencyCatalog.codes)
        val type =
            spinner(
                listOf(
                    getString(R.string.cash),
                    getString(R.string.bank),
                    getString(R.string.wechat),
                    getString(R.string.alipay),
                    getString(R.string.custom),
                )
            )
        var day = snapshot.getString("today")
        val dayButton =
            button(day) {
                datePicker(day) { value ->
                    day = value
                }
            }
        // Update the date label when the picker returns.
        dayButton.setOnClickListener {
            datePicker(day) { value ->
                day = value
                dayButton.text = value
            }
        }
        form.addView(name)
        form.addView(type)
        form.addView(label(getString(R.string.account_currency), 13, color = muted))
        form.addView(code)
        form.addView(opening)
        form.addView(label(getString(R.string.start_date), 13, color = muted))
        form.addView(dayButton)
        confirmation(R.string.new_account, form) { dialog ->
            val body =
                JSONObject()
                    .put("request_id", UUID.randomUUID().toString())
                    .put("name", name.text.toString())
                    .put(
                        "account_type",
                        listOf("cash", "bank", "wechat", "alipay", "custom")[
                            type.selectedItemPosition],
                    )
                    .put("opening_amount", opening.text.toString())
                    .put("currency_code", CurrencyCatalog.codes[code.selectedItemPosition])
                    .put("balance_start_on", day)
            request("create_account", body, true) {
                dialog.dismiss()
                refresh()
            }
        }
    }

    private fun newBook() {
        if (ledger.pending() != null) {
            Toast.makeText(this, R.string.pending_error, Toast.LENGTH_LONG).show()
            return
        }
        val form = column().apply { setPadding(dp(20), dp(8), dp(20), 0) }
        val name = edit(getString(R.string.book_name))
        form.addView(name)
        confirmation(R.string.new_book, form) { dialog ->
            request(
                "create_book",
                JSONObject()
                    .put("request_id", UUID.randomUUID().toString())
                    .put("name", name.text.toString()),
                true,
            ) {
                dialog.dismiss()
                refresh()
            }
        }
    }

    internal fun editTransaction(
        draft: JSONObject?,
        existing: JSONObject? = null,
        capture: JSONObject? = null,
    ) {
        if (ledger.pending() != null) {
            Toast.makeText(this, R.string.pending_error, Toast.LENGTH_LONG).show()
            return
        }
        val form = column().apply { setPadding(dp(20), dp(8), dp(20), dp(12)) }
        form.addView(label(getString(R.string.confirmation_note), 13, color = muted))
        val initialKind = candidate(draft, "kind") ?: "expense"
        val kind =
            spinner(listOf(getString(R.string.expense), getString(R.string.income))).apply {
                setSelection(if (initialKind == "income") 1 else 0)
                isEnabled = existing == null
            }
        val amount =
            edit(
                getString(R.string.amount),
                capture?.optString("selected_amount")
                    ?: candidate(draft, "amount_minor")?.toLongOrNull()?.let {
                        money(it, draft?.optString("currency_code", "CNY") ?: "CNY")
                    }
                    ?: "",
                amount = true,
            )
        val preferences = snapshot.getJSONObject("preferences")
        val accounts = selectable("account", candidate(draft, "account_id"))
        val books = selectable("book", candidate(draft, "book_id"))
        val categories = selectable("category", candidate(draft, "category_id"))
        val payments = selectable("payment_method", candidate(draft, "payment_method_id"))
        val account =
            choices(
                accounts,
                candidate(draft, "account_id") ?: nullable(preferences, "default_account_id"),
            )
        val book =
            choices(books, candidate(draft, "book_id") ?: nullable(preferences, "default_book_id"))
        val category =
            choices(
                categories.filter { it.getString("transaction_kind") == initialKind },
                candidate(draft, "category_id"),
            )
        var categoryRows = categories.filter { it.getString("transaction_kind") == initialKind }
        kind.onItemSelectedListener =
            object : AdapterView.OnItemSelectedListener {
                override fun onNothingSelected(parent: AdapterView<*>?) = Unit

                override fun onItemSelected(
                    parent: AdapterView<*>?,
                    view: View?,
                    position: Int,
                    id: Long,
                ) {
                    val currentKind = if (position == 0) "expense" else "income"
                    categoryRows = categories.filter {
                        it.getString("transaction_kind") == currentKind
                    }
                    populateChoices(category, categoryRows, candidate(draft, "category_id"))
                }
            }
        val payment =
            choices(payments, candidate(draft, "payment_method_id"), getString(R.string.no_payment))
        var day = candidate(draft, "occurred_on") ?: snapshot.getString("today")
        val dayButton = button(day) {}
        val periodCodes =
            mutableListOf<String?>(null, "morning", "noon", "afternoon", "evening", "night")
        val periodLabels =
            mutableListOf(
                getString(R.string.date_only),
                getString(R.string.morning),
                getString(R.string.noon),
                getString(R.string.afternoon),
                getString(R.string.evening),
                getString(R.string.night),
            )
        val exact = candidate(draft, "occurred_at_utc")
        if (exact != null) {
            periodCodes.add("exact")
            periodLabels.add(getString(R.string.parsed_exact))
        }
        val precision =
            spinner(periodLabels).apply {
                setSelection(
                    if (exact != null) periodCodes.lastIndex
                    else periodCodes.indexOf(candidate(draft, "time_period")).coerceAtLeast(0)
                )
            }
        dayButton.setOnClickListener {
            datePicker(day) { value ->
                day = value
                dayButton.text = value
                if (precision.selectedItemPosition == periodCodes.indexOf("exact"))
                    precision.setSelection(0)
            }
        }
        val note = edit(getString(R.string.note), candidate(draft, "note") ?: "", multiline = true)
        val party = edit(getString(R.string.counterparty), candidate(draft, "counterparty") ?: "")
        val merchant = edit(getString(R.string.merchant), candidate(draft, "merchant") ?: "")
        val location = edit(getString(R.string.location), candidate(draft, "location") ?: "")
        val tagSelection = transactions.tags(form, existing?.optJSONArray("tag_ids"))
        listOf(
                R.string.transaction_type to kind,
                R.string.amount to amount,
                R.string.date to dayButton,
                R.string.time_precision to precision,
                R.string.account to account,
                R.string.book to book,
                R.string.category to category,
                R.string.payment to payment,
                R.string.note to note,
                R.string.counterparty to party,
                R.string.merchant to merchant,
                R.string.location to location,
            )
            .forEach { (title, field) ->
                form.addView(label(getString(title), 13, color = muted))
                form.addView(field)
            }
        confirmation(R.string.confirm_record, form) { dialog ->
            val values =
                JSONObject()
                    .put("kind", if (kind.selectedItemPosition == 0) "expense" else "income")
                    .put("amount", amount.text.toString())
                    .put("account_id", selectedId(account, accounts))
                    .put("book_id", selectedId(book, books))
                    .put("category_id", selectedId(category, categoryRows))
                    .put("payment_method_id", selectedId(payment, payments))
                    .put("occurred_on", day)
                    .put("note", note.text.toString())
                    .put("counterparty", party.text.toString())
                    .put("merchant", merchant.text.toString())
                    .put("location", location.text.toString())
                    .put("tag_ids", tagSelection())
                    .put("time_zone", snapshot.getString("time_zone"))
            val timeCode = periodCodes[precision.selectedItemPosition]
            values.put(
                "occurrence_precision",
                if (timeCode == null) "date" else if (timeCode == "exact") "exact" else "period",
            )
            if (timeCode == "exact") values.put("occurred_at_utc", exact)
            else if (timeCode != null) values.put("time_period", timeCode)
            if (existing != null) {
                values.put("source", existing.getString("source"))
                values.put("source_text", existing.opt("source_text"))
                values.put("time_zone", existing.getString("time_zone"))
            } else if (draft != null) {
                values.put("source_text", inputText)
                values.put("source", draft.optString("parser_source", "local_rule"))
            }
            val body =
                JSONObject().put("request_id", UUID.randomUUID().toString()).put("fields", values)
            val action = if (existing == null) "record" else "mutate"
            val commandBody =
                if (existing == null) body
                else
                    transactions.command(
                        "transaction.update.v1",
                        JSONObject()
                            .put("id", existing.getString("id"))
                            .put("expected_version", existing.getInt("version"))
                            .put("fields", values),
                    )
            if (capture != null)
                commandBody
                    .put("id", capture.getString("id"))
                    .put("expected_version", capture.getInt("version"))
            request(if (capture != null) "capture_record" else action, commandBody, true) {
                dialog.dismiss()
                if (draft?.optBoolean("keep_source") != true) inputText = ""
                Toast.makeText(this, R.string.saved, Toast.LENGTH_SHORT).show()
                refresh()
            }
        }
    }

    internal fun confirmation(title: Int, form: LinearLayout, onSave: (AlertDialog) -> Unit) {
        val dialog =
            AlertDialog.Builder(this)
                .setTitle(title)
                .setView(ScrollView(this).apply { addView(form) })
                .setPositiveButton(R.string.confirm_save, null)
                .setNegativeButton(R.string.cancel, null)
                .create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).id = R.id.save_button
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                if (!busy) onSave(dialog)
            }
        }
        dialog.show()
    }

    internal fun choices(
        rows: List<JSONObject>,
        selected: String?,
        placeholder: String = getString(R.string.select_required),
    ): Spinner = spinner(emptyList()).also { populateChoices(it, rows, selected, placeholder) }

    private fun populateChoices(
        spinner: Spinner,
        rows: List<JSONObject>,
        selected: String?,
        placeholder: String = getString(R.string.select_required),
    ) {
        spinner.adapter =
            ArrayAdapter(
                this,
                android.R.layout.simple_spinner_dropdown_item,
                listOf(placeholder) +
                    rows.map {
                        it.getString("name") +
                            (if (it.has("currency_code")) " · " + it.getString("currency_code")
                            else "")
                    },
            )
        spinner.setSelection(rows.indexOfFirst { it.getString("id") == selected } + 1)
    }

    internal fun selectedId(spinner: Spinner, rows: List<JSONObject>): Any =
        rows.getOrNull(spinner.selectedItemPosition - 1)?.getString("id") ?: JSONObject.NULL

    internal fun selectable(entity: String, retained: String? = null): List<JSONObject> =
        jsonRows(snapshot.getJSONObject("catalogs").getJSONArray(entity)).filter {
            it.optInt("is_archived") == 0 || it.getString("id") == retained
        }

    internal fun activeAccounts() =
        jsonRows(snapshot.getJSONArray("accounts")).filter { it.optInt("is_archived") == 0 }

    internal fun jsonRows(array: JSONArray) =
        (0 until array.length()).map { array.getJSONObject(it) }

    internal fun nullable(value: JSONObject, key: String): String? =
        if (value.isNull(key)) null else value.optString(key).takeIf { it.isNotEmpty() }

    private fun candidate(value: JSONObject?, key: String): String? =
        value?.optJSONObject(key)?.let { nullable(it, "value") }

    internal fun money(minor: Long, code: String = "CNY"): String =
        ReportRenderer.money(minor.toString(), code)

    internal fun datePicker(value: String, done: (String) -> Unit) {
        val parts = value.split("-").map { it.toInt() }
        DatePickerDialog(
                this,
                { _, year, month, day ->
                    done(String.format(Locale.ROOT, "%04d-%02d-%02d", year, month + 1, day))
                },
                parts[0],
                parts[1] - 1,
                parts[2],
            )
            .apply {
                val today = snapshot.getString("today").split("-").map { it.toInt() }
                val maximum =
                    Calendar.getInstance().apply {
                        set(today[0], today[1] - 1, today[2], 23, 59, 59)
                    }
                datePicker.maxDate = maximum.timeInMillis
                show()
            }
    }

    internal fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    internal fun column() = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

    internal fun row() = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }

    internal fun label(value: String, size: Int, bold: Boolean = false, color: Int = ink) =
        TextView(this).apply {
            text = value
            textSize = size.toFloat()
            setTextColor(color)
            setPadding(0, dp(6), 0, dp(6))
            if (bold) setTypeface(typeface, Typeface.BOLD)
        }

    internal fun edit(
        hintText: String,
        value: String = "",
        multiline: Boolean = false,
        amount: Boolean = false,
        signed: Boolean = false,
    ) =
        EditText(this).apply {
            if (amount) id = R.id.amount_input
            hint = hintText
            setText(value)
            setTextColor(ink)
            setHintTextColor(muted)
            minHeight = dp(52)
            inputType =
                if (amount)
                    InputType.TYPE_CLASS_NUMBER or
                        InputType.TYPE_NUMBER_FLAG_DECIMAL or
                        (if (signed) InputType.TYPE_NUMBER_FLAG_SIGNED else 0)
                else
                    InputType.TYPE_CLASS_TEXT or
                        if (multiline) InputType.TYPE_TEXT_FLAG_MULTI_LINE else 0
            setSingleLine(!multiline)
            if (multiline) {
                minLines = 3
                gravity = Gravity.TOP
            }
        }

    internal fun spinner(labels: List<String>) =
        Spinner(this).apply {
            minimumHeight = dp(52)
            adapter =
                ArrayAdapter(
                    this@MainActivity,
                    android.R.layout.simple_spinner_dropdown_item,
                    labels,
                )
        }

    internal fun button(title: String, primary: Boolean = false, action: () -> Unit) =
        Button(this).apply {
            text = title
            isAllCaps = false
            minHeight = dp(48)
            if (primary) {
                setTextColor(Color.WHITE)
                backgroundTintList =
                    android.content.res.ColorStateList.valueOf(Color.parseColor("#256F62"))
            } else setTextColor(accent)
            setOnClickListener { if (!busy) action() }
        }

    internal fun card() =
        column().apply {
            setPadding(dp(18), dp(12), dp(18), dp(14))
            background =
                GradientDrawable().apply {
                    setColor(surface)
                    cornerRadius = dp(16).toFloat()
                }
            layoutParams = LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(16) }
        }

    internal fun kindLabel(kind: String) =
        getString(
            when (kind) {
                "income" -> R.string.income
                "expense_refund" -> R.string.refund
                "transfer" -> R.string.transfer
                "opening" -> R.string.opening
                "adjustment" -> R.string.adjustment
                else -> R.string.expense
            }
        )

    internal fun errorText(code: String) =
        getString(
            when {
                code.startsWith("AI_") -> R.string.ai_error
                code.startsWith("CREDENTIAL_") -> R.string.key_error
                code.startsWith("UPDATE_") || code.startsWith("INVALID_UPDATE_") ->
                    R.string.update_error
                code.startsWith("BACKUP_") -> R.string.backup_error
                code == "ACTIVE_REFUNDS_BLOCK_OPERATION" || code == "IMPORT_BATCH_CHANGED" ->
                    R.string.dependency_error
                code.startsWith("IMPORT_") -> R.string.import_error
                code.startsWith("ATTACHMENT_") || code.startsWith("INVALID_FILE_") ->
                    R.string.image_failed
                code == "VERSION_CONFLICT" -> R.string.version_error
                code == "INVALID_TIMEZONE" -> R.string.invalid_fields
                code == "EXCHANGE_RATE_MISSING" -> R.string.rate_missing
                code == "TRANSFER_TARGET_AMOUNT_REQUIRED" -> R.string.fx_required
                code == "CURRENCY_MISMATCH" || code == "ACCOUNT_CURRENCY_IMMUTABLE" ->
                    R.string.currency_mismatch
                code == "CAPTURE_ALREADY_SAVED" || code == "CAPTURE_NOT_PENDING" ->
                    R.string.capture_done
                else ->
                    when (code) {
                        "INVALID_AMOUNT",
                        "AMOUNT_PRECISION" -> R.string.invalid_amount
                        "AMOUNT_OUT_OF_RANGE",
                        "AGGREGATE_OUT_OF_RANGE" -> R.string.out_of_range
                        "INVALID_DATE",
                        "FUTURE_DATE",
                        "BEFORE_BALANCE_START" -> R.string.invalid_date
                        "NAME_CONFLICT" -> R.string.name_conflict
                        "IDEMPOTENCY_KEY_REUSED" -> R.string.retry_changed
                        "PENDING_CONFIRMATION" -> R.string.pending_error
                        "MISSING_REQUIRED_FIELD",
                        "ENTITY_NOT_FOUND",
                        "FIELD_CONFLICT",
                        "INVALID_ENVELOPE" -> R.string.invalid_fields
                        else -> R.string.storage_error
                    }
            }
        )
}
