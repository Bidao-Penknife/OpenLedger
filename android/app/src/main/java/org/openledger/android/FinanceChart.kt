package org.openledger.android

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.view.View
import java.math.BigDecimal
import org.json.JSONObject

/** Drawing uses bounded ratios only; all visible financial numbers stay exact. */
class FinanceChart(context: Context, private val report: JSONObject, private val kind: String) :
    View(context) {
    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val scale = resources.displayMetrics.density
        canvas.save()
        canvas.scale(scale, scale)
        draw(canvas, width / scale, height / scale, report, kind)
        canvas.restore()
    }

    companion object {
        private val colors =
            listOf("#256F62", "#DC8856", "#688CB3", "#B882A6", "#9A9B59", "#7C92A0")
                .map(Color::parseColor)

        fun draw(canvas: Canvas, width: Float, height: Float, report: JSONObject, kind: String) {
            val paint = Paint(Paint.ANTI_ALIAS_FLAG)
            paint.textSize = 12f
            if (kind == "months") {
                val rows = report.getJSONArray("months")
                val count = rows.length()
                if (count == 0) return
                var maximum = BigDecimal.ONE
                for (i in 0 until count) for (key in
                    listOf("income_minor", "net_expense_minor")) maximum =
                    maximum.max(
                        BigDecimal(rows.getJSONObject(i).getJSONObject("totals").getString(key))
                            .abs()
                    )
                val available = height - 42f
                val slot = (width - 18f) / count
                val baseline = available * 0.72f
                paint.color = Color.parseColor("#80919B")
                canvas.drawLine(0f, baseline, width, baseline, paint)
                for (i in 0 until count) {
                    val row = rows.getJSONObject(i)
                    for ((j, key) in listOf("income_minor", "net_expense_minor").withIndex()) {
                        val ratio =
                            BigDecimal(row.getJSONObject("totals").getString(key))
                                .divide(maximum, java.math.MathContext.DECIMAL64)
                                .toFloat()
                        val x = i * slot + slot * (0.15f + j * 0.35f)
                        val end =
                            baseline -
                                ratio * if (ratio >= 0) baseline * 0.9f else available * 0.23f
                        paint.color = colors[j]
                        canvas.drawRoundRect(
                            RectF(x, minOf(baseline, end), x + slot * 0.27f, maxOf(baseline, end)),
                            2f,
                            2f,
                            paint,
                        )
                    }
                    if (count <= 12 || i % ((count + 11) / 12) == 0) {
                        paint.color = Color.parseColor("#80919B")
                        canvas.drawText(
                            row.getString("month").takeLast(2),
                            i * slot,
                            height - 20f,
                            paint,
                        )
                    }
                }
                return
            }
            val rows = report.getJSONArray("categories")
            val diameter = minOf(height - 12f, width * 0.48f)
            val circle = RectF(6f, 6f, diameter, diameter)
            var start = -90f
            var slices = 0
            for (i in 0 until rows.length()) {
                val row = rows.getJSONObject(i)
                if (row.isNull("share")) continue
                slices++
                val sweep = BigDecimal(row.getString("share")).toFloat() * 360f
                paint.color = colors[i % colors.size]
                canvas.drawArc(circle, start, sweep, true, paint)
                start += sweep
                if (i < 6) {
                    val y = 23f + i * 27f
                    canvas.drawCircle(diameter + 18f, y - 4f, 5f, paint)
                    paint.color = Color.parseColor("#80919B")
                    val label =
                        row.getString("name").take(9) +
                            " " +
                            ReportRenderer.percent(row.getString("share"))
                    canvas.drawText(label, diameter + 32f, y, paint)
                }
            }
            if (slices == 0) {
                paint.color = Color.parseColor("#CFD8DD")
                canvas.drawOval(circle, paint)
            }
        }
    }
}
