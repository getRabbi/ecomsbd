package com.ecomsbd.app.intake

import android.content.Context
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequest
import androidx.work.PeriodicWorkRequest
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.TimeUnit

/**
 * The three device → server calls, with the ingest key (rule L3). Plain
 * `HttpURLConnection`: no new HTTP dependency for three small JSON requests.
 * Nothing from a request or response body is ever logged.
 */
class IntakeApi(private val credentials: IntakeCredentials, private val appVersion: String?) {

    class Reply(val status: Int, val body: String?, val etag: String?) {
        val ok: Boolean get() = status in 200..299
        val json: JSONObject? get() = body?.let { runCatching { JSONObject(it) }.getOrNull() }

        /** `details.reason` of an error body (e.g. `PACKAGE_NOT_ENABLED`). */
        val reason: String? get() = json?.optJSONObject("details")?.optString("reason")?.ifEmpty { null }
        val code: String? get() = json?.optString("code")?.ifEmpty { null }
    }

    @Throws(IOException::class)
    fun rules(etag: String?): Reply = request("GET", "/intake/rules", null) {
        if (etag != null) setRequestProperty("If-None-Match", etag)
    }

    @Throws(IOException::class)
    fun heartbeat(listenerState: String, coveredPackages: List<String>): Reply =
        request(
            "POST",
            "/intake/heartbeat",
            JSONObject()
                .put("app_version", appVersion ?: JSONObject.NULL)
                .put("covered_packages", JSONArray(coveredPackages))
                .put("listener_state", listenerState),
        )

    @Throws(IOException::class)
    fun capture(batch: PendingBatch): Reply {
        val messages = JSONArray()
        batch.messages.forEach { message ->
            messages.put(
                JSONObject()
                    .put("text", message.text)
                    .put("sent_at", message.sentAt?.let(::isoUtc) ?: JSONObject.NULL),
            )
        }
        val body = JSONObject()
            .put("package", batch.pkg)
            .put("conversation_key", batch.conversationKey)
            .put("sender_display", batch.senderDisplay?.take(120) ?: JSONObject.NULL)
            .put("messages", messages)
        return request("POST", "/intake/captures", body) {
            setRequestProperty("Idempotency-Key", batch.batchId)
        }
    }

    private fun request(
        method: String,
        path: String,
        body: JSONObject?,
        extra: HttpURLConnection.() -> Unit = {},
    ): Reply {
        val connection = URL(credentials.apiBase.trimEnd('/') + path).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = TIMEOUT_MS
            connection.readTimeout = TIMEOUT_MS
            connection.useCaches = false
            connection.setRequestProperty("Authorization", "Bearer ${credentials.ingestKey}")
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("User-Agent", "ecomsbd-android-intake/${appVersion ?: "unknown"}")
            connection.extra()
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            val stream = if (status >= 400) connection.errorStream else connection.inputStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
            return Reply(status, text, connection.getHeaderField("ETag"))
        } finally {
            connection.disconnect()
        }
    }

    companion object {
        private const val TIMEOUT_MS = 15_000

        fun isoUtc(millis: Long): String =
            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
                .apply { timeZone = TimeZone.getTimeZone("UTC") }
                .format(Date(millis))

        fun forContext(context: Context, credentials: IntakeCredentials): IntakeApi {
            val version = runCatching {
                context.packageManager.getPackageInfo(context.packageName, 0).versionName
            }.getOrNull()
            return IntakeApi(credentials, version)
        }
    }
}

/** WorkManager scheduling: every call survives process death and retries with backoff. */
object IntakeWork {
    private const val UPLOAD = "intake-upload"
    private const val HEARTBEAT = "intake-heartbeat"
    private const val HEARTBEAT_NOW = "intake-heartbeat-now"
    private const val BACKOFF_SECONDS = 30L
    private const val HEARTBEAT_HOURS = 6L

    private val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    /** After linking: heartbeat + rules now, then every 6 h. */
    fun start(context: Context) {
        val periodic = PeriodicWorkRequest.Builder(IntakeHeartbeatWorker::class.java, HEARTBEAT_HOURS, TimeUnit.HOURS)
            .setConstraints(online)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, BACKOFF_SECONDS, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context)
            .enqueueUniquePeriodicWork(HEARTBEAT, ExistingPeriodicWorkPolicy.KEEP, periodic)
        heartbeatNow(context, refreshRules = true)
    }

    fun stop(context: Context) {
        val work = WorkManager.getInstance(context)
        listOf(UPLOAD, HEARTBEAT, HEARTBEAT_NOW).forEach(work::cancelUniqueWork)
    }

    /** On listener connect/disconnect, after linking, after a settings change. */
    fun heartbeatNow(context: Context, refreshRules: Boolean = false) {
        if (Intake.get(context).credentials() == null) return
        val request = OneTimeWorkRequest.Builder(IntakeHeartbeatWorker::class.java)
            .setConstraints(online)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, BACKOFF_SECONDS, TimeUnit.SECONDS)
            .setInputData(androidx.work.Data.Builder().putBoolean(KEY_FORCE_RULES, refreshRules).build())
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(HEARTBEAT_NOW, ExistingWorkPolicy.REPLACE, request)
    }

    fun uploadNow(context: Context) {
        val request = OneTimeWorkRequest.Builder(IntakeUploadWorker::class.java)
            .setConstraints(online)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, BACKOFF_SECONDS, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(UPLOAD, ExistingWorkPolicy.APPEND_OR_REPLACE, request)
    }

    internal const val KEY_FORCE_RULES = "force_rules"
}

/** Heartbeat (listener state, app version, covered apps) and the daily rules check. */
class IntakeHeartbeatWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
    override fun doWork(): Result {
        val intake = Intake.get(applicationContext)
        val credentials = intake.credentials() ?: return Result.success()
        val api = IntakeApi.forContext(applicationContext, credentials)
        return try {
            val granted = OrderNotificationListener.accessGranted(applicationContext)
            val state = when {
                !granted -> "NO_PERMISSION"
                intake.listenerConnected() -> "ON"
                else -> "OFF"
            }
            val covered = if (state == "ON") intake.rules()?.enabledPackages?.sorted().orEmpty() else emptyList()
            val reply = api.heartbeat(state, covered)
            when {
                reply.ok -> intake.heartbeatAccepted(
                    System.currentTimeMillis(),
                    // Phase 4 adds `autopilot_on`; absent means off.
                    autopilotOn = reply.json?.optBoolean("autopilot_on", false) == true,
                )
                reply.status == 401 -> {
                    intake.markRevoked()
                    return Result.success()
                }
                reply.status == 429 || reply.status >= 500 -> return Result.retry()
            }
            if (inputData.getBoolean(IntakeWork.KEY_FORCE_RULES, false) ||
                intake.rulesDue(System.currentTimeMillis())
            ) {
                if (!IntakeSync.refreshRules(applicationContext, intake, api)) return Result.retry()
            }
            Result.success()
        } catch (error: IOException) {
            Result.retry()
        } catch (error: Exception) {
            Log.w(TAG, "intake heartbeat failed: ${error.javaClass.simpleName}")
            Result.retry()
        }
    }

    private companion object {
        const val TAG = "EcomsbdIntake"
    }
}

/** Drains the Autopilot upload queue. Never runs for a tap: Create goes through the seller's session. */
class IntakeUploadWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
    override fun doWork(): Result {
        val intake = Intake.get(applicationContext)
        val credentials = intake.credentials() ?: return Result.success()
        val api = IntakeApi.forContext(applicationContext, credentials)
        for (batch in intake.pendingBatches()) {
            val reply = try {
                api.capture(batch)
            } catch (error: IOException) {
                return Result.retry()
            }
            when {
                reply.ok -> intake.removeBatch(batch.batchId)
                reply.status == 403 && reply.reason == "PACKAGE_NOT_ENABLED" -> {
                    // The shop turned this app off: drop what was read from it and refresh.
                    intake.dropPackage(batch.pkg).forEach {
                        OrderDetectNotifier.cancel(applicationContext, it.notificationId)
                    }
                    IntakeWork.heartbeatNow(applicationContext, refreshRules = true)
                }
                reply.status == 401 -> {
                    intake.markRevoked()
                    return Result.success()
                }
                reply.code == "FEATURE_DISABLED" -> {
                    intake.dropPending()
                    return Result.success()
                }
                reply.status == 429 || reply.status >= 500 -> return Result.retry()
                // Any other refusal will not change on retry: drop it, never loop on it.
                else -> intake.removeBatch(batch.batchId)
            }
        }
        return Result.success()
    }
}

internal object IntakeSync {
    /** `GET /v1/intake/rules` with If-None-Match. False = try again later. */
    fun refreshRules(context: Context, intake: Intake, api: IntakeApi): Boolean {
        val reply = api.rules(intake.rulesEtag())
        val now = System.currentTimeMillis()
        when {
            reply.status == 304 -> intake.rulesUnchanged(now)
            reply.ok && reply.body != null -> {
                if (!intake.saveRules(reply.body, reply.etag, now)) {
                    // A format this build cannot read: keep the cached rules, ask again next time.
                    intake.rulesUnchanged(now)
                }
                // Packages the shop no longer reads: forget their buffered chats.
                intake.keepOnly(intake.rules()?.enabledPackages.orEmpty()).forEach {
                    OrderDetectNotifier.cancel(context, it.notificationId)
                }
            }
            reply.status == 401 -> intake.markRevoked()
            reply.status == 429 || reply.status >= 500 -> return false
        }
        return true
    }
}
