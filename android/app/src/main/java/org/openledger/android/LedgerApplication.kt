package org.openledger.android

import android.app.Application
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.util.concurrent.Executors
import org.json.JSONObject

/** Keep one Python service and one writer queue for the entire app process. */
class LedgerApplication : Application() {
    val worker = Executors.newSingleThreadExecutor()
    private var service: PyObject? = null

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
                (previous.getString("action") != action ||
                    previous.getJSONObject("body").getString("request_id") !=
                        body.getString("request_id"))
        ) {
            return JSONObject()
                .put("api_version", 1)
                .put("ok", false)
                .put("error", JSONObject().put("code", "PENDING_CONFIRMATION"))
        }
        val envelope = JSONObject().put("action", action).put("body", body)
        check(preferences.edit().putString("pending", envelope.toString()).commit())
        val result = call(action, body)
        val retryable =
            setOf("STORAGE_IO_ERROR", "DATABASE_BUSY", "DATABASE_READ_ONLY", "DISK_FULL")
        if (
            result.getBoolean("ok") || result.getJSONObject("error").getString("code") !in retryable
        ) {
            preferences.edit().remove("pending").commit()
        }
        return result
    }

    @Synchronized
    fun call(action: String, body: JSONObject = JSONObject()): JSONObject {
        if (!Python.isStarted()) Python.start(AndroidPlatform(this))
        val current =
            service
                ?: Python.getInstance()
                    .getModule("openledger.mobile.bridge")
                    .callAttr("MobileLedger", File(filesDir, "ledger").absolutePath)
                    .also { service = it }
        val request = JSONObject().put("api_version", 1).put("action", action).put("body", body)
        val reply = JSONObject(current.callAttr("call", request.toString()).toString())
        check(reply.getInt("api_version") == 1) { "Unsupported mobile API" }
        return reply
    }
}
