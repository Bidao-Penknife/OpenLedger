package org.openledger.android

import android.content.Context
import android.content.res.Configuration
import java.util.Locale

/** Presentation choices stay separate from financial data and can be reapplied on launch. */
object AppPreferences {
    fun context(base: Context): Context {
        val saved = base.getSharedPreferences("presentation", Context.MODE_PRIVATE)
        val configuration = Configuration(base.resources.configuration)
        configuration.setLocale(
            if (saved.getString("language", "zh") == "en") Locale.ENGLISH
            else Locale.SIMPLIFIED_CHINESE
        )
        val mode =
            when (saved.getString("theme", "system")) {
                "light" -> Configuration.UI_MODE_NIGHT_NO
                "dark" -> Configuration.UI_MODE_NIGHT_YES
                else -> base.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK
            }
        configuration.uiMode =
            (configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK.inv()) or mode
        return base.createConfigurationContext(configuration)
    }
}
