package com.ecomsbd.app.intake

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/**
 * Recent messages per conversation (`package|title`), so an order typed over
 * several messages ("ami 2ta nibo" … "01712…" … "Mirpur 10") is read as one.
 *
 * A conversation stays open while its newest message is younger than the rules'
 * buffer window; then it is dropped (and its heads-up with it). At most
 * [maxEntries] messages are kept across all conversations: the conversations
 * that went quiet longest go first. A conversation keeps its newest
 * [MAX_MESSAGES] messages (the server's batch limit), each at most [MAX_TEXT]
 * characters.
 *
 * Plain data: the Android layer persists [toJson] in an encrypted file. No
 * `android.*` imports, so it runs in JVM unit tests.
 */
class ConversationBuffer(
    private val maxEntries: Int = MAX_ENTRIES,
    private val newId: () -> String = { UUID.randomUUID().toString() },
) {
    data class Incoming(val text: String, val sentAt: Long?)

    data class Message(val text: String, val sentAt: Long?, val receivedAt: Long)

    class Conversation(
        /** Random; the only thing about a conversation that leaves this store (intent extras). */
        val id: String,
        val pkg: String,
        val title: String,
        var senderDisplay: String?,
        val messages: MutableList<Message> = mutableListOf(),
        /** The seller tapped Ignore: no heads-up again until the conversation closes. */
        var ignored: Boolean = false,
        /** A heads-up is showing (or was shown) for this conversation. */
        var notified: Boolean = false,
        /** Messages already queued for upload (Autopilot path). */
        var uploadedCount: Int = 0,
    ) {
        val key: String get() = key(pkg, title)
        val lastReceivedAt: Long get() = messages.maxOfOrNull { it.receivedAt } ?: 0L
        val text: String get() = messages.joinToString("\n") { it.text }
        val notificationId: Int get() = notificationId(key)
    }

    data class AddResult(val conversation: Conversation, val added: Int, val dropped: List<Conversation>)

    private val conversations = linkedMapOf<String, Conversation>()

    val size: Int get() = conversations.values.sumOf { it.messages.size }

    fun all(): List<Conversation> = conversations.values.toList()

    fun get(id: String): Conversation? = conversations.values.firstOrNull { it.id == id }

    fun find(pkg: String, title: String): Conversation? = conversations[key(pkg, title)]

    fun remove(id: String): Conversation? {
        val found = get(id) ?: return null
        conversations.remove(found.key)
        return found
    }

    fun removePackage(pkg: String): List<Conversation> {
        val gone = conversations.values.filter { it.pkg == pkg }
        gone.forEach { conversations.remove(it.key) }
        return gone
    }

    fun clear() = conversations.clear()

    /** Conversations whose newest message is older than [windowMillis]. */
    fun prune(now: Long, windowMillis: Long): List<Conversation> {
        val expired = conversations.values.filter { now - it.lastReceivedAt > windowMillis }
        expired.forEach { conversations.remove(it.key) }
        return expired
    }

    /**
     * Add what a notification showed. Messaging apps repost the whole visible
     * thread on every new message, so a message already held (same text and
     * time, or same text when the app gave no time) is skipped.
     */
    fun add(
        pkg: String,
        title: String,
        senderDisplay: String?,
        incoming: List<Incoming>,
        now: Long,
        windowMillis: Long,
    ): AddResult {
        val dropped = prune(now, windowMillis).toMutableList()
        val conversation = conversations.getOrPut(key(pkg, title)) {
            Conversation(id = newId(), pkg = pkg, title = title, senderDisplay = senderDisplay)
        }
        if (!senderDisplay.isNullOrBlank()) conversation.senderDisplay = senderDisplay
        // A reposted thread can reach back days: keep what falls inside the window
        // of its own newest message (relative, so a wrong phone clock does not matter).
        val newest = incoming.mapNotNull { it.sentAt }.maxOrNull()
        var added = 0
        for (item in incoming) {
            val text = item.text.trim().take(MAX_TEXT)
            if (text.isEmpty()) continue
            if (newest != null && item.sentAt != null && newest - item.sentAt > windowMillis) continue
            val duplicate = conversation.messages.any {
                it.text == text && (item.sentAt == null || it.sentAt == null || it.sentAt == item.sentAt)
            }
            if (duplicate) continue
            conversation.messages += Message(text = text, sentAt = item.sentAt, receivedAt = now)
            added++
        }
        if (conversation.messages.isEmpty()) {
            conversations.remove(conversation.key)
            return AddResult(conversation, 0, dropped)
        }
        // Keep the newest MAX_MESSAGES; anything trimmed was older than what was uploaded.
        val overflow = conversation.messages.size - MAX_MESSAGES
        if (overflow > 0) {
            repeat(overflow) { conversation.messages.removeAt(0) }
            conversation.uploadedCount = maxOf(0, conversation.uploadedCount - overflow)
        }
        // Move it to the end: insertion order is "quiet longest first".
        conversations.remove(conversation.key)
        conversations[conversation.key] = conversation
        while (size > maxEntries && conversations.size > 1) {
            val oldest = conversations.values.first()
            conversations.remove(oldest.key)
            dropped += oldest
        }
        return AddResult(conversation, added, dropped)
    }

    fun toJson(): String {
        val array = JSONArray()
        for (conversation in conversations.values) {
            val messages = JSONArray()
            for (message in conversation.messages) {
                messages.put(
                    JSONObject()
                        .put("text", message.text)
                        .put("sent_at", message.sentAt ?: JSONObject.NULL)
                        .put("received_at", message.receivedAt),
                )
            }
            array.put(
                JSONObject()
                    .put("id", conversation.id)
                    .put("package", conversation.pkg)
                    .put("title", conversation.title)
                    .put("sender_display", conversation.senderDisplay ?: JSONObject.NULL)
                    .put("ignored", conversation.ignored)
                    .put("notified", conversation.notified)
                    .put("uploaded_count", conversation.uploadedCount)
                    .put("messages", messages),
            )
        }
        return JSONObject().put("v", 1).put("conversations", array).toString()
    }

    companion object {
        /** Spec WP1.4: the buffer holds at most 200 entries. */
        const val MAX_ENTRIES = 200
        /** The server's batch and text limits (`POST /v1/intake/captures`). */
        const val MAX_MESSAGES = 50
        const val MAX_TEXT = 4000
        private const val NOTIFICATION_BASE = 0x4E0000

        @JvmStatic
        fun key(pkg: String, title: String): String = "$pkg|$title"

        /** One heads-up per conversation, updated in place: the id follows the key. */
        @JvmStatic
        fun notificationId(key: String): Int = NOTIFICATION_BASE + (key.hashCode() and 0xFFFF)

        /** Restore a buffer; an unreadable store starts empty rather than failing. */
        @JvmStatic
        fun fromJson(raw: String?, maxEntries: Int = MAX_ENTRIES): ConversationBuffer {
            val buffer = ConversationBuffer(maxEntries)
            if (raw.isNullOrBlank()) return buffer
            try {
                val array = JSONObject(raw).getJSONArray("conversations")
                for (index in 0 until array.length()) {
                    val item = array.getJSONObject(index)
                    val messages = item.getJSONArray("messages")
                    val conversation = Conversation(
                        id = item.getString("id"),
                        pkg = item.getString("package"),
                        title = item.getString("title"),
                        senderDisplay = item.optStringOrNull("sender_display"),
                        ignored = item.optBoolean("ignored"),
                        notified = item.optBoolean("notified"),
                        uploadedCount = item.optInt("uploaded_count"),
                    )
                    for (m in 0 until messages.length()) {
                        val message = messages.getJSONObject(m)
                        conversation.messages += Message(
                            text = message.getString("text"),
                            sentAt = if (message.isNull("sent_at")) null else message.getLong("sent_at"),
                            receivedAt = message.getLong("received_at"),
                        )
                    }
                    if (conversation.messages.isNotEmpty()) {
                        buffer.conversations[conversation.key] = conversation
                    }
                }
            } catch (error: Exception) {
                buffer.conversations.clear()
            }
            return buffer
        }

        private fun JSONObject.optStringOrNull(name: String): String? =
            if (isNull(name)) null else optString(name)
    }
}
