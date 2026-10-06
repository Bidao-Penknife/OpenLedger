package org.openledger.android

import android.content.Context
import org.json.JSONObject

/** The same pinned ISO precision file is used by Python and every native formatter. */
object CurrencyCatalog {
    private var precision: Map<String, Int> = mapOf("CNY" to 2)
    val codes: List<String>
        get() =
            listOf("CNY", "USD", "EUR", "JPY", "HKD", "GBP", "KWD") +
                precision.keys.sorted().filter {
                    it !in setOf("CNY", "USD", "EUR", "JPY", "HKD", "GBP", "KWD")
                }

    fun initialize(context: Context) {
        val rows =
            JSONObject(
                    context.assets.open("currencies.json").bufferedReader().use { it.readText() }
                )
                .getJSONArray("currencies")
        precision =
            (0 until rows.length()).associate {
                val row = rows.getJSONObject(it)
                row.getString("code") to row.getInt("digits")
            }
    }

    fun digits(code: String): Int = precision[code] ?: error("CURRENCY_MISMATCH")

    fun label(code: String): String = if (code == "CNY") "¥" else code
}
