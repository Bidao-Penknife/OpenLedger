package org.openledger.android

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import java.io.File
import java.util.UUID

/** Copy only user-selected documents between SAF streams and private staging. */
class FileActions(private val activity: MainActivity) {
    private val ledger
        get() = activity.application as LedgerApplication

    private var operation = ""
    private var filename = ""
    private var directory = ""

    fun restoreState(state: Bundle?) {
        operation = state?.getString("file_operation") ?: ""
        filename = state?.getString("file_name") ?: ""
        directory = state?.getString("file_directory") ?: ""
    }

    fun saveState(state: Bundle) {
        state.putString("file_operation", operation)
        state.putString("file_name", filename)
        state.putString("file_directory", directory)
    }

    fun export(name: String, mime: String) {
        check(Regex("[A-Za-z0-9][A-Za-z0-9_.-]{0,119}").matches(name) && !name.contains(".."))
        operation = "export"
        directory = ledger.directory
        filename = name
        activity.startActivityForResult(
            Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                addCategory(Intent.CATEGORY_OPENABLE)
                type = mime
                putExtra(Intent.EXTRA_TITLE, name)
            },
            REQUEST,
        )
    }

    fun pick(task: String, extension: String, mime: String = "*/*") {
        operation = task
        directory = ledger.directory
        filename = "selected-${UUID.randomUUID()}.$extension"
        activity.startActivityForResult(
            Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                addCategory(Intent.CATEGORY_OPENABLE)
                type = mime
                if (mime == "image/*")
                    putExtra(
                        Intent.EXTRA_MIME_TYPES,
                        arrayOf("image/png", "image/jpeg", "image/webp"),
                    )
            },
            REQUEST,
        )
    }

    fun result(code: Int, result: Int, data: Intent?): Boolean {
        if (code != REQUEST) return false
        val uri = data?.data
        if (result != Activity.RESULT_OK || uri == null) {
            operation = ""
            return true
        }
        val task = operation
        val name = filename
        val expectedDirectory = directory
        operation = ""
        activity.fileBusy(true)
        ledger.worker.execute {
            var success = false
            try {
                check(expectedDirectory == ledger.directory) { "DATA_DIRECTORY_CHANGED" }
                val file = File(ledger.staging, name)
                if (task == "export") {
                    activity.contentResolver.openOutputStream(uri, "w")!!.use { out ->
                        file.inputStream().use { it.copyTo(out) }
                    }
                } else {
                    // Bounded on disk, never load a backup or spreadsheet into RAM.
                    val limit = if (task == "restore") 1_140_850_688L else 20L * 1024 * 1024
                    activity.contentResolver.openInputStream(uri)!!.use { input ->
                        file.outputStream().use { out ->
                            val buffer = ByteArray(64 * 1024)
                            var total = 0L
                            while (true) {
                                val count = input.read(buffer)
                                if (count < 0) break
                                total += count
                                check(total <= limit) { "FILE_TOO_LARGE" }
                                out.write(buffer, 0, count)
                            }
                        }
                    }
                }
                success = true
            } catch (_: Exception) {
                if (task != "export") File(ledger.staging, name).delete()
            }
            activity.runOnUiThread {
                activity.fileBusy(false)
                if (activity.isDestroyed || activity.isFinishing) return@runOnUiThread
                if (!success)
                    Toast.makeText(activity, R.string.file_failed, Toast.LENGTH_LONG).show()
                else if (task == "export")
                    Toast.makeText(activity, R.string.file_saved, Toast.LENGTH_LONG).show()
                else activity.importedFile(task, name)
            }
        }
        return true
    }

    companion object {
        const val REQUEST = 401
    }
}
