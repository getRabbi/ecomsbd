package com.ecomsbd.app.intake

import org.json.JSONArray
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * "Ei sender ke ar na": conversations the seller never wants a heads-up for.
 *
 * Only an HMAC of `package|title` under a per-install secret is kept, so the
 * list on disk does not name anyone. Newest [MAX] entries are kept.
 */
class SenderBlockList(private val secret: ByteArray, hashes: Collection<String> = emptyList()) {
    private val hashes = LinkedHashSet(hashes)

    fun isBlocked(pkg: String, title: String): Boolean = hash(pkg, title) in hashes

    fun block(pkg: String, title: String) {
        val value = hash(pkg, title)
        hashes.remove(value)
        hashes.add(value)
        while (hashes.size > MAX) hashes.remove(hashes.first())
    }

    val size: Int get() = hashes.size

    fun toJson(): String = JSONArray(hashes.toList()).toString()

    private fun hash(pkg: String, title: String): String {
        val mac = Mac.getInstance(ALGORITHM)
        mac.init(SecretKeySpec(secret, ALGORITHM))
        val digest = mac.doFinal(ConversationBuffer.key(pkg, title).toByteArray(Charsets.UTF_8))
        return digest.joinToString("") { "%02x".format(it) }
    }

    companion object {
        const val MAX = 500
        private const val ALGORITHM = "HmacSHA256"

        @JvmStatic
        fun fromJson(secret: ByteArray, raw: String?): SenderBlockList {
            if (raw.isNullOrBlank()) return SenderBlockList(secret)
            return try {
                val array = JSONArray(raw)
                SenderBlockList(secret, (0 until array.length()).map { array.getString(it) })
            } catch (error: Exception) {
                SenderBlockList(secret)
            }
        }
    }
}

/** What one notification showed, already taken apart by the listener. */
data class CapturedNotification(
    val pkg: String,
    /** The conversation as the app names it (chat title). */
    val title: String,
    val senderDisplay: String?,
    val messages: List<ConversationBuffer.Incoming>,
)

/**
 * The listener's decision, kept free of Android so it can be tested on the JVM:
 * package check → block list → buffer → classify → heads-up and/or upload.
 */
class IntakeEngine(
    private val rules: IntakeRules,
    private val buffer: ConversationBuffer,
    private val blockList: SenderBlockList,
) {
    private val classifier = IntakeClassifier(rules)

    enum class Skip { PACKAGE_NOT_ENABLED, SENDER_BLOCKED, NOTHING_NEW }

    sealed interface Outcome {
        /** Conversations that closed or were pushed out: cancel their heads-ups. */
        val dropped: List<ConversationBuffer.Conversation>
    }

    data class Skipped(
        val reason: Skip,
        override val dropped: List<ConversationBuffer.Conversation> = emptyList(),
    ) : Outcome

    data class Buffered(
        val conversation: ConversationBuffer.Conversation,
        val result: IntakeClassifier.Result,
        /** Show or update this conversation's heads-up. */
        val notify: Boolean,
        /** Autopilot only: messages to upload now (null = nothing to upload). */
        val upload: List<ConversationBuffer.Message>?,
        override val dropped: List<ConversationBuffer.Conversation>,
    ) : Outcome

    fun classify(conversation: ConversationBuffer.Conversation): IntakeClassifier.Result =
        classifier.classify(conversation.text)

    /**
     * @param autopilotOn the server reported Autopilot consent for this shop
     *   (Phase 4). Off: nothing is uploaded until the seller taps Create (L4).
     */
    fun onNotification(capture: CapturedNotification, now: Long, autopilotOn: Boolean): Outcome {
        if (capture.pkg !in rules.enabledPackages) return Skipped(Skip.PACKAGE_NOT_ENABLED)
        if (blockList.isBlocked(capture.pkg, capture.title)) return Skipped(Skip.SENDER_BLOCKED)
        val added = buffer.add(
            pkg = capture.pkg,
            title = capture.title,
            senderDisplay = capture.senderDisplay,
            incoming = capture.messages,
            now = now,
            windowMillis = rules.bufferWindowMillis,
        )
        if (added.added == 0) return Skipped(Skip.NOTHING_NEW, added.dropped)
        val conversation = added.conversation
        val result = classifier.classify(conversation.text)
        val notify = result.orderLike && !conversation.ignored
        if (notify) conversation.notified = true
        var upload: List<ConversationBuffer.Message>? = null
        if (autopilotOn && result.orderLike && conversation.uploadedCount < conversation.messages.size) {
            upload = conversation.messages.drop(conversation.uploadedCount)
            conversation.uploadedCount = conversation.messages.size
        }
        return Buffered(conversation, result, notify, upload, added.dropped)
    }

    /** Ignore: keep reading the chat, but no heads-up until it closes. */
    fun ignore(id: String): ConversationBuffer.Conversation? =
        buffer.get(id)?.also { it.ignored = true }

    /** Ei sender ke ar na: block the sender and forget what was buffered. */
    fun block(id: String): ConversationBuffer.Conversation? =
        buffer.remove(id)?.also { blockList.block(it.pkg, it.title) }
}
