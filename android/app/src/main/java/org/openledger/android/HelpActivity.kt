package org.openledger.android

import android.app.Activity
import android.content.Context
import android.os.Bundle
import android.webkit.WebView
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout

/** One self-contained manual; no JavaScript, file access, content access or networking. */
class HelpActivity : Activity() {
    private var web: WebView? = null

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(AppPreferences.context(newBase))
    }

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        title = getString(R.string.help_title)
        val layout = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        layout.setOnApplyWindowInsetsListener { view, insets ->
            view.setPadding(0, insets.systemWindowInsetTop, 0, insets.systemWindowInsetBottom)
            insets
        }
        val navigation = LinearLayout(this)
        navigation.addView(
            Button(this).apply {
                text = getString(R.string.cancel)
                setOnClickListener { finish() }
            }
        )
        val search =
            EditText(this).apply {
                hint = getString(R.string.search)
                isSingleLine = true
            }
        navigation.addView(search, LinearLayout.LayoutParams(0, -2, 1f))
        navigation.addView(
            Button(this).apply {
                text = getString(R.string.search)
                setOnClickListener { web?.findAllAsync(search.text.toString()) }
            }
        )
        layout.addView(navigation)
        val viewer =
            WebView(this).apply {
                settings.javaScriptEnabled = false
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                settings.blockNetworkLoads = true
                settings.builtInZoomControls = true
                settings.displayZoomControls = false
                loadDataWithBaseURL(
                    "https://openledger.invalid/help/",
                    assets.open("help/manual.html").bufferedReader().use { it.readText() },
                    "text/html",
                    "UTF-8",
                    null,
                )
            }
        web = viewer
        layout.addView(viewer, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(layout)
    }

    override fun onDestroy() {
        web?.destroy()
        web = null
        super.onDestroy()
    }
}
