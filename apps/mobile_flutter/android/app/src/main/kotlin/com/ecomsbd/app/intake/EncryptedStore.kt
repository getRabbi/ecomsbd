package com.ecomsbd.app.intake

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Log
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Small JSON documents encrypted with an AES-256-GCM key that never leaves the
 * Android Keystore. Files live in `noBackupFilesDir`, so neither the ingest key
 * nor buffered chat text is ever copied into a cloud backup.
 *
 * File = 12-byte IV + ciphertext (with the GCM tag). Anything that cannot be
 * decrypted (key wiped by a reset, file truncated) is deleted and read as
 * absent: the phone then relinks rather than failing forever.
 */
class EncryptedStore(context: Context) {
    private val dir = File(context.noBackupFilesDir, "intake").apply { mkdirs() }

    @Synchronized
    fun read(name: String): String? {
        val file = File(dir, "$name.bin")
        if (!file.isFile) return null
        return try {
            val bytes = file.readBytes()
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, bytes, 0, IV_BYTES))
            String(cipher.doFinal(bytes, IV_BYTES, bytes.size - IV_BYTES), Charsets.UTF_8)
        } catch (error: Exception) {
            Log.w(TAG, "unreadable intake file dropped: ${error.javaClass.simpleName}")
            file.delete()
            null
        }
    }

    @Synchronized
    fun write(name: String, value: String?) {
        val file = File(dir, "$name.bin")
        if (value == null) {
            file.delete()
            return
        }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val sealed = cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        val temp = File(dir, "$name.tmp")
        temp.writeBytes(sealed)
        if (!temp.renameTo(file)) {
            file.delete()
            temp.renameTo(file)
        }
    }

    @Synchronized
    fun clear() {
        dir.listFiles()?.forEach { it.delete() }
    }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (keyStore.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    private companion object {
        const val TAG = "EcomsbdIntake"
        const val KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "ecomsbd.intake.v1"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
        const val TAG_BITS = 128
    }
}
