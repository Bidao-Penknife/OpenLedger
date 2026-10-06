package org.openledger.android

import android.app.Notification
import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.os.Build
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import org.json.JSONObject

/** User-selected payment notifications become private drafts, never automatic debits. */
class PaymentNotificationListener : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val preferences = getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
        if (!preferences.getBoolean("enabled", false)) return
        val packages = preferences.getStringSet("packages", DEFAULT_PACKAGES).orEmpty()
        if (sbn.packageName !in packages) return
        val notification = sbn.notification
        if (notification.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        val extras = notification.extras
        val text =
            listOf(
                    extras.getCharSequence(Notification.EXTRA_TITLE)?.toString(),
                    extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString(),
                    extras.getCharSequence(Notification.EXTRA_TEXT)?.toString(),
                    extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)?.joinToString("\n"),
                )
                .filterNotNull()
                .filter { it.isNotBlank() }
                .distinct()
                .joinToString("\n")
                .take(4000)
        if (!eligible(text)) return
        val ledger = application as LedgerApplication
        val directory = ledger.directory
        // `when` identifies the producer's event; updates to the same event reuse the key.
        // New events with a reused notification ID get a new timestamp and remain separate.
        val stamp = notification.`when`.takeIf { it > 0 } ?: sbn.postTime
        val body =
            JSONObject()
                .put("request_id", UUID.randomUUID().toString())
                .put("source_kind", "notification")
                .put("source_label", sbn.packageName)
                .put("event_key", "${sbn.key}|$stamp")
                .put("text", text)
                .put(
                    "timestamp",
                    SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.ROOT)
                        .apply { timeZone = TimeZone.getTimeZone("UTC") }
                        .format(Date(stamp.coerceAtMost(System.currentTimeMillis()))),
                )
        ledger.worker.execute {
            if (directory != ledger.directory) return@execute
            val result = runCatching { ledger.call("capture_stage", body) }.getOrNull()
            preferences
                .edit()
                .putString(
                    "last_error",
                    if (result?.optBoolean("ok") == true) ""
                    else result?.optJSONObject("error")?.optString("code") ?: "STORAGE_IO_ERROR",
                )
                .apply()
        }
    }

    companion object {
        const val PREFERENCES = "notification-capture"
        val DEFAULT_PACKAGES = setOf("com.tencent.mm", "com.eg.android.AlipayGphone")

        fun eligible(text: String): Boolean =
            Regex("支付成功|付款成功|消费|收款|到账|退款成功|转账成功").containsMatchIn(text) &&
                Regex("[0-9０-９零一二三四五六七八九十百千]+.*(?:元|块|[¥￥]|CNY|USD|JPY)|[¥￥].*[0-9]")
                    .containsMatchIn(text) &&
                !Regex("支付失败|付款失败|待付款|未支付|交易关闭|验证码").containsMatchIn(text)

        fun hasAccess(context: Context): Boolean {
            val component = ComponentName(context, PaymentNotificationListener::class.java)
            return if (Build.VERSION.SDK_INT >= 27)
                context
                    .getSystemService(NotificationManager::class.java)
                    .isNotificationListenerAccessGranted(component)
            else
                Settings.Secure.getString(context.contentResolver, "enabled_notification_listeners")
                    ?.split(':')
                    ?.any { ComponentName.unflattenFromString(it) == component } == true
        }
    }
}
