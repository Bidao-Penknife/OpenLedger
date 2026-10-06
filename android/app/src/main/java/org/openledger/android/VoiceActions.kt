package org.openledger.android

import android.Manifest
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.widget.EditText

/** Foreground, one-shot Chinese speech with explicit network consent and text fallback. */
class VoiceActions(private val a: MainActivity) {
    private var recognizer: SpeechRecognizer? = null
    private var listening: AlertDialog? = null

    fun start() {
        if (!SpeechRecognizer.isRecognitionAvailable(a)) {
            fallback(a.getString(R.string.voice_unavailable))
            return
        }
        if (
            a.checkSelfPermission(Manifest.permission.RECORD_AUDIO) !=
                PackageManager.PERMISSION_GRANTED
        ) {
            a.requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), PERMISSION)
            return
        }
        if (Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(a))
            listen(true)
        else
            AlertDialog.Builder(a)
                .setTitle(R.string.voice_title)
                .setMessage(R.string.voice_network)
                .setPositiveButton(R.string.voice_online) { _, _ -> listen(false) }
                .setNeutralButton(R.string.voice_text) { _, _ -> fallback("") }
                .setNegativeButton(R.string.cancel, null)
                .show()
    }

    fun permission(code: Int, granted: IntArray): Boolean {
        if (code != PERMISSION) return false
        if (granted.firstOrNull() == PackageManager.PERMISSION_GRANTED) start()
        else fallback(a.getString(R.string.voice_permission_denied))
        return true
    }

    private fun listen(onDevice: Boolean) {
        close()
        try {
            recognizer =
                if (onDevice && Build.VERSION.SDK_INT >= 31)
                    SpeechRecognizer.createOnDeviceSpeechRecognizer(a)
                else SpeechRecognizer.createSpeechRecognizer(a)
            listening =
                AlertDialog.Builder(a)
                    .setTitle(R.string.voice_title)
                    .setMessage(
                        if (onDevice) R.string.voice_listening_offline else R.string.voice_listening
                    )
                    .setNegativeButton(R.string.cancel) { _, _ -> close() }
                    .create()
                    .apply {
                        setOnCancelListener { close() }
                        show()
                    }
            recognizer!!.setRecognitionListener(
                object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) = Unit

                    override fun onBeginningOfSpeech() = Unit

                    override fun onRmsChanged(rmsdB: Float) = Unit

                    override fun onBufferReceived(buffer: ByteArray?) = Unit

                    override fun onEndOfSpeech() = Unit

                    override fun onPartialResults(partialResults: Bundle?) = Unit

                    override fun onEvent(eventType: Int, params: Bundle?) = Unit

                    override fun onError(error: Int) {
                        close()
                        if (!a.isFinishing && !a.isDestroyed)
                            fallback(a.getString(R.string.voice_failed, error))
                    }

                    override fun onResults(results: Bundle?) {
                        val choices =
                            results
                                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                                .orEmpty()
                                .filter { it.isNotBlank() }
                        close()
                        if (a.isFinishing || a.isDestroyed) return
                        if (choices.isEmpty()) fallback(a.getString(R.string.voice_unavailable))
                        else if (choices.size == 1) review(choices[0])
                        else
                            AlertDialog.Builder(a)
                                .setTitle(R.string.voice_review)
                                .setItems(choices.toTypedArray()) { _, index ->
                                    review(choices[index])
                                }
                                .setNegativeButton(R.string.cancel, null)
                                .show()
                    }
                }
            )
            recognizer!!.startListening(
                Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                    .putExtra(
                        RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                        RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
                    )
                    .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
                    .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, onDevice)
                    .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
            )
        } catch (_: Exception) {
            close()
            fallback(a.getString(R.string.voice_unavailable))
        }
    }

    fun fallback(message: String) {
        if (a.isFinishing || a.isDestroyed) return
        review("", message)
    }

    private fun review(text: String, message: String = "") {
        val form = a.column()
        if (message.isNotBlank()) form.addView(a.label(message, 14))
        form.addView(a.label(a.getString(R.string.voice_review_note), 14))
        val input: EditText = a.edit(a.getString(R.string.quick_hint), text, multiline = true)
        form.addView(input)
        a.confirmation(R.string.voice_review, form) { dialog ->
            if (input.text.isBlank()) return@confirmation
            a.captures.stageVoice(input.text.toString()) { dialog.dismiss() }
        }
    }

    fun close() {
        recognizer?.setRecognitionListener(null)
        recognizer?.cancel()
        recognizer?.destroy()
        recognizer = null
        listening?.dismiss()
        listening = null
    }

    companion object {
        const val PERMISSION = 2054
    }
}
