package org.openledger.android

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** OS-held AES key; endpoint-scoped API secrets never enter the ledger or backups. */
class SecureSecrets(private val context: Context) {
    private val preferences
        get() = context.getSharedPreferences("encrypted-ai-credentials", Context.MODE_PRIVATE)

    private fun identity(directory: String, endpoint: String): String =
        MessageDigest.getInstance("SHA-256")
            .digest((directory + "\n" + endpoint.trimEnd('/')).toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it.toInt() and 255) }

    private fun alias(id: String) = "OpenLedger-AI-$id"

    private fun key(id: String, create: Boolean): SecretKey? {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias(id), null) as? SecretKey)?.let {
            return it
        }
        if (!create) return null
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            .apply {
                init(
                    KeyGenParameterSpec.Builder(
                            alias(id),
                            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                        )
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .setRandomizedEncryptionRequired(true)
                        .setKeySize(256)
                        .build()
                )
            }
            .generateKey()
    }

    fun set(directory: String, endpoint: String, value: String) {
        require(
            value.length in 1..1280 && value == value.trim() && value.all { it.code in 33..126 }
        )
        val id = identity(directory, endpoint)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key(id, true))
        cipher.updateAAD(id.toByteArray(Charsets.US_ASCII))
        val encoded =
            Base64.encodeToString(cipher.iv, Base64.NO_WRAP) +
                ":" +
                Base64.encodeToString(
                    cipher.doFinal(value.toByteArray(Charsets.UTF_8)),
                    Base64.NO_WRAP,
                )
        check(preferences.edit().putString(id, encoded).commit())
    }

    fun get(directory: String, endpoint: String): String? {
        val id = identity(directory, endpoint)
        val saved = preferences.getString(id, null) ?: return null
        require(saved.length <= 4096)
        val values = saved.split(':')
        require(values.size == 2)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            key(id, false) ?: error("CREDENTIAL_UNAVAILABLE"),
            GCMParameterSpec(128, Base64.decode(values[0], Base64.NO_WRAP)),
        )
        cipher.updateAAD(id.toByteArray(Charsets.US_ASCII))
        return cipher.doFinal(Base64.decode(values[1], Base64.NO_WRAP)).toString(Charsets.UTF_8)
    }

    fun delete(directory: String, endpoint: String) {
        val id = identity(directory, endpoint)
        check(preferences.edit().remove(id).commit())
        KeyStore.getInstance("AndroidKeyStore").apply {
            load(null)
            deleteEntry(alias(id))
        }
    }
}
