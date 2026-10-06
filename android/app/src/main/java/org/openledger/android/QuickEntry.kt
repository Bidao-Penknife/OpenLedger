package org.openledger.android

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ShortcutInfo
import android.content.pm.ShortcutManager
import android.graphics.drawable.Icon
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/** Both OS entry points return to the same review-before-write screen. */
object QuickEntry {
    const val ACTION = "org.openledger.android.QUICK_ENTRY"

    fun intent(context: Context) =
        Intent(context, MainActivity::class.java)
            .setAction(ACTION)
            .addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK or
                    Intent.FLAG_ACTIVITY_CLEAR_TOP or
                    Intent.FLAG_ACTIVITY_SINGLE_TOP
            )

    @android.annotation.TargetApi(25)
    private fun shortcut(context: Context): ShortcutInfo =
        ShortcutInfo.Builder(context, "quick-ledger")
            .setShortLabel(context.getString(R.string.quick_title))
            .setLongLabel(context.getString(R.string.quick_hint))
            .setIcon(Icon.createWithResource(context, R.drawable.ic_launcher))
            .setIntent(intent(context))
            .build()

    fun publish(context: Context) {
        if (Build.VERSION.SDK_INT >= 25)
            runCatching {
                context.getSystemService(ShortcutManager::class.java).dynamicShortcuts =
                    listOf(shortcut(context))
            }
    }

    fun pin(context: Context): Boolean =
        if (Build.VERSION.SDK_INT >= 26) {
            val manager = context.getSystemService(ShortcutManager::class.java)
            manager.isRequestPinShortcutSupported &&
                manager.requestPinShortcut(shortcut(context), null)
        } else false
}

class QuickEntryTile : TileService() {
    override fun onStartListening() {
        super.onStartListening()
        qsTile?.apply {
            state = Tile.STATE_ACTIVE
            label = AppPreferences.context(this@QuickEntryTile).getString(R.string.quick_title)
            updateTile()
        }
    }

    override fun onClick() {
        super.onClick()
        unlockAndRun {
            val intent = QuickEntry.intent(this)
            if (Build.VERSION.SDK_INT >= 34)
                startActivityAndCollapse(
                    PendingIntent.getActivity(
                        this,
                        0,
                        intent,
                        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                    )
                )
            else launchBeforeAndroid14(intent)
        }
    }

    // The PendingIntent overload was introduced in API 34. Older devices need this
    // guarded overload to open the entry screen and collapse the shade.
    @android.annotation.SuppressLint("StartActivityAndCollapseDeprecated")
    @Suppress("DEPRECATION")
    private fun launchBeforeAndroid14(intent: Intent) = startActivityAndCollapse(intent)
}
