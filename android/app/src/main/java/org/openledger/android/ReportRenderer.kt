package org.openledger.android

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.pdf.PdfDocument
import java.io.File
import java.math.BigDecimal
import java.math.BigInteger
import java.math.RoundingMode
import java.util.UUID
import org.json.JSONObject

/** Native Android PDF/PNG output, with complete paginated text and exact amounts. */
class ReportRenderer(private val context: Context) {
    companion object {
        fun money(minor: String): String {
            val value = BigInteger(minor)
            val parts = value.abs().divideAndRemainder(BigInteger.valueOf(100))
            return (if (value.signum() < 0) "-" else "") +
                parts[0] +
                "." +
                parts[1].toString().padStart(2, '0')
        }

        fun percent(ratio: String): String =
            BigDecimal(ratio)
                .multiply(BigDecimal(100))
                .setScale(1, RoundingMode.HALF_UP)
                .toPlainString() + "%"

        fun share(row: JSONObject): String =
            if (row.isNull("share")) "—" else percent(row.getString("share"))
    }

    private fun lines(report: JSONObject): List<String> {
        val result = mutableListOf<String>()
        val totals = report.getJSONObject("totals")
        val filters = report.getJSONObject("filters")
        result.add(filters.getString("start_on") + " — " + filters.getString("end_on"))
        result.add(
            context.getString(
                R.string.data_revision,
                report.getLong("data_revision"),
                report.getString("generated_at_utc"),
            )
        )
        for ((key, title) in
            listOf(
                "income_minor" to R.string.income,
                "gross_expense_minor" to R.string.gross_expense,
                "refund_minor" to R.string.refund,
                "net_expense_minor" to R.string.net_expense,
                "surplus_minor" to R.string.surplus,
            )) result.add(context.getString(title) + ": ¥ " + money(totals.getString(key)))
        if (!totals.isNull("savings_rate"))
            result.add(
                context.getString(R.string.savings_rate) +
                    ": " +
                    percent(totals.getString("savings_rate"))
            )
        val scopes = report.getJSONArray("scope_labels")
        for (i in 0 until scopes.length()) result.add(scopes.getString(i))
        val previous = report.optJSONObject("comparison_totals")
        if (previous != null)
            result.add(
                context.getString(
                    R.string.comparison_note,
                    report.getString("comparison_start"),
                    report.getString("comparison_end"),
                    money(previous.getString("income_minor")),
                    money(previous.getString("net_expense_minor")),
                )
            )
        result.add(context.getString(R.string.month_trend))
        val months = report.getJSONArray("months")
        for (i in 0 until months.length()) {
            val row = months.getJSONObject(i)
            val amounts = row.getJSONObject("totals")
            result.add(
                row.getString("month") +
                    " · " +
                    context.getString(R.string.income) +
                    " ¥" +
                    money(amounts.getString("income_minor")) +
                    " · " +
                    context.getString(R.string.net_expense) +
                    " ¥" +
                    money(amounts.getString("net_expense_minor"))
            )
        }
        result.add(context.getString(R.string.category_share))
        result.add(context.getString(R.string.category_basis))
        val categories = report.getJSONArray("categories")
        for (i in 0 until categories.length()) {
            val row = categories.getJSONObject(i)
            result.add(
                row.getString("name") +
                    " · ¥" +
                    money(row.getString("net_expense_minor")) +
                    " · " +
                    share(row)
            )
        }
        result.add(context.getString(R.string.expense_rank))
        val ranks = report.getJSONArray("ranking")
        for (i in 0 until ranks.length()) {
            val row = ranks.getJSONObject(i)
            result.add(
                "${i + 1}. ${row.getString("label")} · ¥${money(row.getString("amount_minor"))} · ${row.getInt("count")}"
            )
        }
        val notes = report.getJSONArray("notes")
        for (i in 0 until notes.length()) result.add(notes.getString(i))
        return result
    }

    private fun wrapped(report: JSONObject, paint: Paint): List<String> =
        lines(report).flatMap { text ->
            val parts = mutableListOf<String>()
            var line = ""
            for (point in text.codePoints().toArray()) {
                val character = String(Character.toChars(point))
                if (paint.measureText(line + character) > 510f && line.isNotEmpty()) {
                    parts.add(line)
                    line = ""
                }
                line += character
            }
            parts.add(line)
            parts
        }

    private fun header(canvas: Canvas, report: JSONObject, paint: Paint, page: Int) {
        canvas.drawColor(Color.WHITE)
        paint.color = Color.parseColor("#243342")
        paint.textSize = 22f
        canvas.drawText(context.getString(R.string.report_title), 42f, 43f, paint)
        paint.textSize = 11f
        canvas.drawText("OpenLedger · $page", 42f, 64f, paint)
        if (page == 1) {
            canvas.save()
            canvas.translate(42f, 85f)
            FinanceChart.draw(canvas, 510f, 170f, report, "months")
            canvas.restore()
            canvas.drawText(context.getString(R.string.chart_legend), 42f, 258f, paint)
            canvas.save()
            canvas.translate(42f, 275f)
            FinanceChart.draw(canvas, 510f, 160f, report, "categories")
            canvas.restore()
        }
        paint.textSize = 12f
    }

    fun export(report: JSONObject, format: String, directory: File): File {
        require(format in listOf("pdf", "png"))
        val destination = File(directory, "OpenLedger-report-${UUID.randomUUID()}.$format")
        val paint =
            Paint(Paint.ANTI_ALIAS_FLAG).apply {
                textSize = 12f
                color = Color.parseColor("#243342")
            }
        val text = wrapped(report, paint)
        try {
            if (format == "pdf") {
                val document = PdfDocument()
                try {
                    var offset = 0
                    var number = 1
                    while (offset < text.size) {
                        val page =
                            document.startPage(
                                PdfDocument.PageInfo.Builder(595, 842, number).create()
                            )
                        header(page.canvas, report, paint, number)
                        var y = if (number == 1) 460f else 92f
                        while (offset < text.size && y <= 790f) {
                            page.canvas.drawText(text[offset++], 42f, y, paint)
                            y += 19f
                        }
                        document.finishPage(page)
                        number++
                    }
                    destination.outputStream().use(document::writeTo)
                } finally {
                    document.close()
                }
            } else {
                val height = 490 + text.size * 19
                require(height <= 4000) { "REPORT_TOO_LARGE" }
                val bitmap = Bitmap.createBitmap(1190, height * 2, Bitmap.Config.ARGB_8888)
                try {
                    val canvas = Canvas(bitmap)
                    canvas.scale(2f, 2f)
                    header(canvas, report, paint, 1)
                    var y = 460f
                    for (line in text) {
                        canvas.drawText(line, 42f, y, paint)
                        y += 19f
                    }
                    destination.outputStream().use {
                        check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it))
                    }
                } finally {
                    bitmap.recycle()
                }
            }
            return destination
        } catch (error: Exception) {
            destination.delete()
            throw error
        }
    }
}
