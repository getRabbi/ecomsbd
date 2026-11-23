package com.ecomsbd.app.intake

import android.content.Context
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.security.SecureRandom
import java.util.UUID

/** Where a linked phone sends its captures. Stored only in [EncryptedStore]. */
data class IntakeCredentials(val apiBase: String, val ingestKey: String, val connectionId: String)

/** One conversation's messages waiting for `POST /v1/intake/captures` (Autopilot path). */
data class PendingBatch(
    /** The `Idempotency-Key`: a retry of the same batch is a replay, never a second copy. */
    val batchId: String,
    val pkg: String,
    val conversationKey: String,
    val senderDisplay: String?,
    val messages: List<ConversationBuffer.Message>,
)

/**
 * The phone's intake state, shared by the listener, the workers, the action
 * receiver and the Flutter channel (all in one process). Every change is written
 * through to [EncryptedStore] before the call returns.
 */
class Intake private constructor(private val context: Context) {
    private val store = EncryptedStore(context)
    private val lock = Any()

    private val state: JSONObject = parseObject(store.read(STATE))
    private var credentials: IntakeCredentials? = readCredentials()
    private var rules: IntakeRules? = store.read(RULES)?.let { raw ->
        runCatching { IntakeRules.parse(JSONObject(raw).getString("body")) }.getOrNull()
    }
    private val buffer: ConversationBuffer = ConversationBuffer.fromJson(store.read(BUFFER))
    private val blockList: SenderBlockList = SenderBlockList.fromJson(blockSecret(), store.read(BLOCKED))
    private val pending: MutableList<PendingBatch> = readPending()

    // ------------------------------------------------------------ identity ---

    /** Stable per install; `POST /v1/devices/link` is idempotent on it. */
    fun installId(): String = synchronized(lock) {
        state.optString("install_id").ifEmpty {
            UUID.randomUUID().toString().also {
                state.put("install_id", it)
                saveState()
            }
        }
    }

    fun credentials(): IntakeCredentials? = synchronized(lock) { credentials }

    fun link(value: IntakeCredentials) = synchronized(lock) {
        credentials = value
        store.write(
            CREDENTIALS,
            JSONObject()
                .put("api_base", value.apiBase)
                .put("ingest_key", value.ingestKey)
                .put("connection_id", value.connectionId)
                .toString(),
        )
        state.remove("revoked")
        state.remove("last_heartbeat_at")
        saveState()
    }

    /** Forget the key and everything read. The block list and install id stay. */
    fun unlink(): List<ConversationBuffer.Conversation> = synchronized(lock) {
        credentials = null
        store.write(CREDENTIALS, null)
        val gone = buffer.all()
        buffer.clear()
        pending.clear()
        rules = null
        store.write(RULES, null)
        state.remove("autopilot_on")
        state.remove("last_heartbeat_at")
        state.remove("revoked")
        saveState()
        saveBuffer()
        savePending()
        gone
    }

    /** The server refused the key (401): stop sending, ask the seller to relink. */
    fun markRevoked() = synchronized(lock) {
        state.put("revoked", true)
        pending.clear()
        saveState()
        savePending()
    }

    // --------------------------------------------------------------- rules ---

    fun rules(): IntakeRules? = synchronized(lock) { rules }

    fun rulesEtag(): String? = synchronized(lock) {
        store.read(RULES)?.let { runCatching { JSONObject(it).optString("etag").ifEmpty { null } }.getOrNull() }
    }

    fun rulesDue(now: Long): Boolean = synchronized(lock) {
        val current = rules ?: return@synchronized true
        now - state.optLong("rules_checked_at", 0L) >= current.refreshAfterMillis
    }

    /** Keep a new body only if this build can read it; otherwise keep the old one. */
    fun saveRules(body: String, etag: String?, now: Long): Boolean = synchronized(lock) {
        val parsed = try {
            IntakeRules.parse(body)
        } catch (error: IntakeRules.UnsupportedRulesException) {
            return@synchronized false
        }
        rules = parsed
        store.write(RULES, JSONObject().put("body", body).put("etag", etag ?: JSONObject.NULL).toString())
        state.put("rules_checked_at", now)
        saveState()
        true
    }

    fun rulesUnchanged(now: Long) = synchronized(lock) {
        state.put("rules_checked_at", now)
        saveState()
    }

    // ----------------------------------------------------- listener + server ---

    fun autopilotOn(): Boolean = synchronized(lock) { state.optBoolean("autopilot_on", false) }

    fun setListenerConnected(connected: Boolean) = synchronized(lock) {
        state.put("listener_connected", connected)
        saveState()
    }

    fun listenerConnected(): Boolean = synchronized(lock) { state.optBoolean("listener_connected", false) }

    fun heartbeatAccepted(at: Long, autopilotOn: Boolean) = synchronized(lock) {
        state.put("last_heartbeat_at", at)
        state.put("autopilot_on", autopilotOn)
        saveState()
    }

    fun status(): Map<String, Any?> = synchronized(lock) {
        mapOf(
            "linked" to (credentials != null),
            "revoked" to state.optBoolean("revoked", false),
            "connectionId" to credentials?.connectionId,
            "lastHeartbeatAt" to state.optLong("last_heartbeat_at", 0L).takeIf { it > 0 },
            "listenerConnected" to state.optBoolean("listener_connected", false),
            "rulesVersion" to rules?.version,
            "enabledPackages" to (rules?.enabledPackages?.sorted() ?: emptyList<String>()),
            "autopilotOn" to state.optBoolean("autopilot_on", false),
            "pendingUploads" to pending.size,
        )
    }

    // ------------------------------------------------------- notifications ---

    /** Run one notification through the engine and persist the result. */
    fun onNotification(capture: CapturedNotification, now: Long): IntakeEngine.Outcome? = synchronized(lock) {
        val current = rules ?: return@synchronized null
        if (credentials == null || state.optBoolean("revoked", false)) return@synchronized null
        val outcome = IntakeEngine(current, buffer, blockList)
            .onNotification(capture, now, autopilotOn = state.optBoolean("autopilot_on", false))
        if (outcome is IntakeEngine.Buffered && outcome.upload != null) {
            pending += PendingBatch(
                batchId = UUID.randomUUID().toString(),
                pkg = outcome.conversation.pkg,
                conversationKey = outcome.conversation.title,
                senderDisplay = outcome.conversation.senderDisplay,
                messages = outcome.upload,
            )
            savePending()
        }
        if (outcome is IntakeEngine.Buffered || outcome.dropped.isNotEmpty()) saveBuffer()
        outcome
    }

    fun classify(conversation: ConversationBuffer.Conversation): IntakeClassifier.Result? =
        synchronized(lock) { rules?.let { IntakeClassifier(it).classify(conversation.text) } }

    fun conversation(id: String): ConversationBuffer.Conversation? = synchronized(lock) { buffer.get(id) }

    fun ignore(id: String): ConversationBuffer.Conversation? = synchronized(lock) {
        buffer.get(id)?.also {
            it.ignored = true
            saveBuffer()
        }
    }

    fun block(id: String): ConversationBuffer.Conversation? = synchronized(lock) {
        buffer.remove(id)?.also {
            blockList.block(it.pkg, it.title)
            store.write(BLOCKED, blockList.toJson())
            saveBuffer()
        }
    }

    /** The seller created the draft from it: nothing of it stays on the phone. */
    fun finish(id: String): ConversationBuffer.Conversation? = synchronized(lock) {
        buffer.remove(id)?.also { saveBuffer() }
    }

    fun prune(now: Long): List<ConversationBuffer.Conversation> = synchronized(lock) {
        val window = rules?.bufferWindowMillis ?: return@synchronized emptyList()
        buffer.prune(now, window).also { if (it.isNotEmpty()) saveBuffer() }
    }

    // ------------------------------------------------------------- uploads ---

    fun pendingBatches(): List<PendingBatch> = synchronized(lock) { pending.toList() }

    fun removeBatch(batchId: String) = synchronized(lock) {
        if (pending.removeAll { it.batchId == batchId }) savePending()
    }

    /** `403 PACKAGE_NOT_ENABLED`: the shop turned the app off; drop what was read from it. */
    fun dropPackage(pkg: String): List<ConversationBuffer.Conversation> = synchronized(lock) {
        pending.removeAll { it.pkg == pkg }
        savePending()
        buffer.removePackage(pkg).also { saveBuffer() }
    }

    /** After a rules refresh: forget chats and uploads of apps the shop no longer reads. */
    fun keepOnly(enabled: Set<String>): List<ConversationBuffer.Conversation> = synchronized(lock) {
        val gone = buffer.all().filter { it.pkg !in enabled }
        gone.forEach { buffer.remove(it.id) }
        val pendingBefore = pending.size
        pending.removeAll { it.pkg !in enabled }
        if (gone.isNotEmpty()) saveBuffer()
        if (pending.size != pendingBefore) savePending()
        gone
    }

    fun dropPending() = synchronized(lock) {
        pending.clear()
        savePending()
    }

    // ------------------------------------------------------------ storage ---

    private fun blockSecret(): ByteArray {
        val saved = state.optString("block_secret")
        if (saved.isNotEmpty()) return Base64.decode(saved, Base64.NO_WRAP)
        val secret = ByteArray(32).also { SecureRandom().nextBytes(it) }
        state.put("block_secret", Base64.encodeToString(secret, Base64.NO_WRAP))
        saveState()
        return secret
    }

    private fun readCredentials(): IntakeCredentials? = store.read(CREDENTIALS)?.let { raw ->
        runCatching {
            val json = JSONObject(raw)
            IntakeCredentials(
                apiBase = json.getString("api_base"),
                ingestKey = json.getString("ingest_key"),
                connectionId = json.getString("connection_id"),
            )
        }.getOrNull()
    }

    private fun readPending(): MutableList<PendingBatch> {
        val raw = store.read(PENDING) ?: return mutableListOf()
        return runCatching {
            val array = JSONArray(raw)
            (0 until array.length()).map { index ->
                val item = array.getJSONObject(index)
                val messages = item.getJSONArray("messages")
                PendingBatch(
                    batchId = item.getString("batch_id"),
                    pkg = item.getString("package"),
                    conversationKey = item.getString("conversation_key"),
                    senderDisplay = if (item.isNull("sender_display")) null else item.getString("sender_display"),
                    messages = (0 until messages.length()).map { m ->
                        val message = messages.getJSONObject(m)
                        ConversationBuffer.Message(
                            text = message.getString("text"),
                            sentAt = if (message.isNull("sent_at")) null else message.getLong("sent_at"),
                            receivedAt = message.getLong("received_at"),
                        )
                    },
                )
            }.toMutableList()
        }.getOrElse { mutableListOf() }
    }

    private fun savePending() {
        while (pending.size > MAX_PENDING) pending.removeAt(0)
        val array = JSONArray()
        for (batch in pending) {
            val messages = JSONArray()
            batch.messages.forEach {
                messages.put(
                    JSONObject()
                        .put("text", it.text)
                        .put("sent_at", it.sentAt ?: JSONObject.NULL)
                        .put("received_at", it.receivedAt),
                )
            }
            array.put(
                JSONObject()
                    .put("batch_id", batch.batchId)
                    .put("package", batch.pkg)
                    .put("conversation_key", batch.conversationKey)
                    .put("sender_display", batch.senderDisplay ?: JSONObject.NULL)
                    .put("messages", messages),
            )
        }
        store.write(PENDING, array.toString())
    }

    private fun saveBuffer() = store.write(BUFFER, buffer.toJson())

    private fun saveState() = store.write(STATE, state.toString())

    companion object {
        private const val STATE = "state"
        private const val CREDENTIALS = "credentials"
        private const val RULES = "rules"
        private const val BUFFER = "buffer"
        private const val BLOCKED = "blocked"
        private const val PENDING = "pending"
        /** Upload queue cap; the oldest batches go first if the phone stays offline for days. */
        private const val MAX_PENDING = 100

        @Volatile
        private var instance: Intake? = null

        @JvmStatic
        fun get(context: Context): Intake =
            instance ?: synchronized(this) {
                instance ?: Intake(context.applicationContext).also { instance = it }
            }

        private fun parseObject(raw: String?): JSONObject =
            raw?.let { runCatching { JSONObject(it) }.getOrNull() } ?: JSONObject()
    }
}
