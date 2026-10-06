package org.openledger.android

import android.app.AlertDialog
import android.content.Context
import android.graphics.BitmapFactory
import android.media.ExifInterface
import android.widget.Toast
import com.google.mlkit.common.MlKit
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions
import java.io.File
import java.util.UUID
import org.json.JSONObject

/** The bundled SDK must initialize once per process, only after an OCR request. */
internal object OcrRuntime {
    private var initialized = false

    @Synchronized
    fun initialize(context: Context) {
        if (!initialized) {
            MlKit.initialize(context.applicationContext)
            initialized = true
        }
    }
}

/** Bundled Chinese ML Kit model: first-use OCR works without Play Services or a download. */
class OcrActions(private val a: MainActivity) {
    fun pick() {
        val preferences = a.getSharedPreferences("ocr", android.content.Context.MODE_PRIVATE)
        if (preferences.getBoolean("acknowledged", false)) a.files.pick("ocr", "image", "image/*")
        else
            AlertDialog.Builder(a)
                .setTitle(R.string.ocr_title)
                .setMessage(R.string.ocr_privacy)
                .setPositiveButton(R.string.ocr_continue) { _, _ ->
                    preferences.edit().putBoolean("acknowledged", true).apply()
                    a.files.pick("ocr", "image", "image/*")
                }
                .setNegativeButton(R.string.cancel, null)
                .show()
    }

    fun imported(filename: String) {
        val ledger = a.application as LedgerApplication
        val directory = ledger.directory
        val file = File(ledger.staging, filename)
        a.fileBusy(true)
        ledger.worker.execute {
            val bitmap = runCatching {
                check(file.length() in 1..20L * 1024 * 1024)
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeFile(file.absolutePath, bounds)
                check(bounds.outWidth > 0 && bounds.outHeight > 0)
                var sample = 1
                while (
                    bounds.outWidth.toLong() * bounds.outHeight / sample / sample > 6_000_000
                ) sample *= 2
                BitmapFactory.decodeFile(
                    file.absolutePath,
                    BitmapFactory.Options().apply { inSampleSize = sample },
                )!!
            }
                .getOrNull()
            val orientation = runCatching {
                ExifInterface(file.absolutePath)
                    .getAttributeInt(
                        ExifInterface.TAG_ORIENTATION,
                        ExifInterface.ORIENTATION_NORMAL,
                    )
            }
                .getOrDefault(ExifInterface.ORIENTATION_NORMAL)
            val rotation =
                when (orientation) {
                    ExifInterface.ORIENTATION_ROTATE_90 -> 90
                    ExifInterface.ORIENTATION_ROTATE_180 -> 180
                    ExifInterface.ORIENTATION_ROTATE_270 -> 270
                    else -> 0
                }
            a.runOnUiThread {
                if (
                    bitmap == null ||
                        a.isDestroyed ||
                        a.isFinishing ||
                        directory != ledger.directory
                ) {
                    bitmap?.recycle()
                    a.fileBusy(false)
                    if (!a.isDestroyed && !a.isFinishing)
                        Toast.makeText(a, R.string.ocr_failed, Toast.LENGTH_LONG).show()
                    return@runOnUiThread
                }
                OcrRuntime.initialize(a)
                val recognizer =
                    TextRecognition.getClient(ChineseTextRecognizerOptions.Builder().build())
                recognizer
                    .process(InputImage.fromBitmap(bitmap, rotation))
                    .addOnSuccessListener { result ->
                        if (result.text.isBlank() || directory != ledger.directory) {
                            a.fileBusy(false)
                            if (!a.isDestroyed && !a.isFinishing)
                                Toast.makeText(a, R.string.ocr_empty, Toast.LENGTH_LONG).show()
                        } else if (!a.isDestroyed && !a.isFinishing) {
                            a.fileBusy(false)
                            a.captures.stage(
                                JSONObject()
                                    .put("source_kind", "ocr")
                                    .put("source_label", a.getString(R.string.ocr_title))
                                    .put("event_key", UUID.randomUUID().toString())
                                    .put("text", result.text.take(4000))
                                    .put("filename", filename)
                            )
                        } else a.fileBusy(false)
                    }
                    .addOnFailureListener {
                        a.fileBusy(false)
                        if (!a.isDestroyed && !a.isFinishing)
                            Toast.makeText(a, R.string.ocr_failed, Toast.LENGTH_LONG).show()
                    }
                    .addOnCompleteListener {
                        recognizer.close()
                        bitmap.recycle()
                    }
            }
        }
    }
}
