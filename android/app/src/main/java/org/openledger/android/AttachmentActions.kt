package org.openledger.android

import android.app.AlertDialog
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.Toast
import java.io.File
import java.util.UUID
import org.json.JSONObject

/** User-selected images are reviewed before audited metadata is committed. */
class AttachmentActions(private val a: MainActivity) {
    private val ledger
        get() = a.application as LedgerApplication

    private fun decode(name: String): Bitmap {
        check(Regex("[A-Za-z0-9][A-Za-z0-9_.-]{0,119}").matches(name) && !name.contains(".."))
        val path = File(ledger.staging, name).absolutePath
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(path, bounds)
        check(
            bounds.outWidth > 0 &&
                bounds.outHeight > 0 &&
                bounds.outWidth.toLong() * bounds.outHeight <= 50_000_000L
        )
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 1600) sample *= 2
        return checkNotNull(
            BitmapFactory.decodeFile(path, BitmapFactory.Options().apply { inSampleSize = sample })
        )
    }

    private fun show(name: String, onConfirm: (() -> Unit)? = null) {
        a.fileBusy(true)
        ledger.worker.execute {
            val result = runCatching { decode(name) }
            a.runOnUiThread {
                a.fileBusy(false)
                val bitmap = result.getOrNull()
                if (bitmap == null || a.isDestroyed || a.isFinishing) {
                    bitmap?.recycle()
                    if (!a.isDestroyed)
                        Toast.makeText(a, R.string.image_failed, Toast.LENGTH_LONG).show()
                    return@runOnUiThread
                }
                val image =
                    ImageView(a).apply {
                        setImageBitmap(bitmap)
                        adjustViewBounds = true
                        maxHeight = a.dp(400)
                        setPadding(a.dp(12), a.dp(12), a.dp(12), a.dp(12))
                        contentDescription = a.getString(R.string.image_attachment)
                    }
                val builder =
                    AlertDialog.Builder(a)
                        .setTitle(R.string.image_attachment)
                        .setView(image)
                        .setNegativeButton(R.string.close, null)
                if (onConfirm != null)
                    builder.setPositiveButton(R.string.confirm_save) { _, _ -> onConfirm() }
                builder.create().apply {
                    setOnDismissListener {
                        image.setImageDrawable(null)
                        bitmap.recycle()
                    }
                    show()
                }
            }
        }
    }

    fun imported(task: String, name: String) {
        val parts = task.split(':')
        if (parts.size != 3) return
        // A native decoder rejects malformed or excessively large image dimensions.
        show(name) {
            a.request(
                "attachment_prepare",
                JSONObject()
                    .put("filename", name)
                    .put("transaction_id", parts[1])
                    .put("expected_transaction_version", parts[2].toLong()),
            ) { payload ->
                a.request(
                    "attachment_commit",
                    JSONObject()
                        .put("request_id", UUID.randomUUID().toString())
                        .put("payload", payload),
                    true,
                ) {
                    a.transactions.detail(parts[1])
                }
            }
        }
    }

    fun render(form: LinearLayout, transaction: JSONObject, dismiss: () -> Unit) {
        form.addView(a.label(a.getString(R.string.image_attachment), 16, true))
        if (transaction.isNull("deleted_at_utc"))
            form.addView(
                a.button(a.getString(R.string.add_image)) {
                    dismiss()
                    a.files.pick(
                        "attachment:${transaction.getString("id")}:${transaction.getLong("version")}",
                        "image",
                        "image/*",
                    )
                }
            )
        val rows = a.jsonRows(transaction.getJSONArray("attachments"))
        for ((index, row) in rows.withIndex()) {
            val deleted = !row.isNull("deleted_at_utc")
            form.addView(
                a.button(
                    a.getString(R.string.image_number, index + 1) +
                        if (deleted) " · ${a.getString(R.string.deleted)}" else ""
                ) {
                    a.request("attachment_view", JSONObject().put("id", row.getString("id"))) {
                        show(it.getString("filename"))
                    }
                }
            )
            if (!deleted || transaction.isNull("deleted_at_utc"))
                form.addView(
                    a.button(
                        a.getString(
                            if (deleted) R.string.restore_record else R.string.delete_record
                        )
                    ) {
                        AlertDialog.Builder(a)
                            .setMessage(R.string.image_delete_note)
                            .setNegativeButton(R.string.cancel, null)
                            .setPositiveButton(R.string.confirm_save) { _, _ ->
                                a.request(
                                    if (deleted) "attachment_restore" else "attachment_delete",
                                    JSONObject()
                                        .put("id", row.getString("id"))
                                        .put("expected_version", row.getLong("version"))
                                        .put("request_id", UUID.randomUUID().toString()),
                                    true,
                                ) {
                                    dismiss()
                                    a.transactions.detail(transaction.getString("id"))
                                }
                            }
                            .show()
                    }
                )
        }
    }
}
