package org.openledger.android

import android.app.Application
import com.chaquo.python.Kwarg
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.util.concurrent.Executors
import org.json.JSONObject

/** Keep one Python service and one writer queue for the entire app process. */
class LedgerApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        CurrencyCatalog.initialize(this)
    }

    val worker = Executors.newSingleThreadExecutor()
    private var service: PyObject? = null
    private var serviceDirectory: String? = null
    val secrets by lazy { SecureSecrets(this) }
    val timeZone: String
        get() =
            getSharedPreferences("presentation", MODE_PRIVATE)
                .getString("time_zone", "Asia/Shanghai")!!

    @Synchronized
    fun setTimeZone(zone: String) {
        check(pending() == null) { "PENDING_CONFIRMATION" }
        val reply = call("validate_time_zone", JSONObject().put("time_zone", zone))
        check(reply.getBoolean("ok"))
        check(
            getSharedPreferences("presentation", MODE_PRIVATE)
                .edit()
                .putString("time_zone", zone)
                .commit()
        )
        service = null
    }

    val staging: File
        get() = File(cacheDir, "exchange").apply { mkdirs() }

    val directory: String
        get() =
            getSharedPreferences("confirmed-command", MODE_PRIVATE)
                .getString("directory", "ledger")!!

    fun directories(): List<String> =
        filesDir
            .listFiles()
            .orEmpty()
            .filter { validDirectory(it.name) && File(it, "database/openledger.sqlite3").isFile }
            .map { it.name }
            .sorted()

    fun directoryLabel(name: String, context: android.content.Context): String {
        check(validDirectory(name))
        if (name == "ledger") return context.getString(R.string.original_data)
        val receipt = File(filesDir, "$name/restore-receipt.json")
        val stamp = runCatching {
            check(receipt.length() in 1..8192)
            JSONObject(receipt.readText()).getString("restored_at_utc").take(19).replace('T', ' ') +
                " UTC"
        }
            .getOrDefault("")
        return context.getString(R.string.restored_data, stamp)
    }

    private fun validDirectory(value: String): Boolean =
        value == "ledger" ||
            Regex("restored-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
                .matches(value)

    private fun open(name: String): PyObject {
        check(validDirectory(name))
        if (!Python.isStarted()) Python.start(AndroidPlatform(this))
        return Python.getInstance()
            .getModule("openledger.mobile.bridge")
            .callAttr(
                "MobileLedger",
                File(filesDir, name).absolutePath,
                Kwarg("staging_directory", staging.absolutePath),
                Kwarg("time_zone", timeZone),
            )
    }

    @Synchronized
    fun switchDirectory(name: String) {
        check(pending() == null) { "PENDING_CONFIRMATION" }
        check(name in directories())
        val opened = open(name)
        check(
            getSharedPreferences("confirmed-command", MODE_PRIVATE)
                .edit()
                .putString("directory", name)
                .commit()
        )
        service = opened
        serviceDirectory = name
    }

    fun pending(): JSONObject? {
        val saved =
            getSharedPreferences("confirmed-command", MODE_PRIVATE).getString("pending", null)
                ?: return null
        return JSONObject(saved)
    }

    /** Persist the user's exact confirmation before executing its UUID once. */
    @Synchronized
    fun confirmed(action: String, body: JSONObject): JSONObject {
        val preferences = getSharedPreferences("confirmed-command", MODE_PRIVATE)
        val previous = pending()
        if (
            previous != null &&
                (previous.optString("directory", "ledger") != directory ||
                    previous.getString("action") != action ||
                    previous.getJSONObject("body").getString("request_id") !=
                        body.getString("request_id") ||
                    previous.getJSONObject("body").toString() != body.toString())
        ) {
            return JSONObject()
                .put("api_version", 1)
                .put("ok", false)
                .put("error", JSONObject().put("code", "PENDING_CONFIRMATION"))
        }
        val envelope =
            JSONObject().put("action", action).put("body", body).put("directory", directory)
        check(preferences.edit().putString("pending", envelope.toString()).commit())
        val result = call(action, body)
        val retryable =
            setOf("STORAGE_IO_ERROR", "DATABASE_BUSY", "DATABASE_READ_ONLY", "DISK_FULL")
        if (
            result.getBoolean("ok") || result.getJSONObject("error").getString("code") !in retryable
        ) {
            val change = preferences.edit().remove("pending")
            if (result.getBoolean("ok") && action == "backup_restore") {
                val name = result.getJSONObject("data").getString("directory")
                val opened = open(name)
                change.putString("directory", name)
                check(change.commit())
                service = opened
                serviceDirectory = name
            } else check(change.commit())
        }
        return result
    }

    @Synchronized
    fun call(action: String, body: JSONObject = JSONObject()): JSONObject {
        if (serviceDirectory != directory) service = null
        val current =
            service
                ?: open(directory).also {
                    service = it
                    serviceDirectory = directory
                }
        val request = JSONObject().put("api_version", 1).put("action", action).put("body", body)
        val secret =
            try {
                if (action == "ai_preview")
                    secrets.get(directory, body.getJSONObject("config").getString("base_url"))
                else null
            } catch (_: Exception) {
                return JSONObject()
                    .put("api_version", 1)
                    .put("ok", false)
                    .put("error", JSONObject().put("code", "CREDENTIAL_UNAVAILABLE"))
            }
        val reply = JSONObject(current.callAttr("call", request.toString(), secret).toString())
        check(reply.getInt("api_version") == 1) { "Unsupported mobile API" }
        return reply
    }
}
