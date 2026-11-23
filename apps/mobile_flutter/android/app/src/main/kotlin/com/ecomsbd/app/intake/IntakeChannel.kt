package com.ecomsbd.app.intake

import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * MethodChannel `ecomsbd/intake`: what Flutter may ask of the native intake.
 *
 * The ingest key comes in once (`link`, right after `POST /v1/devices/link`)
 * and never goes back out: `status` reports only whether the phone is linked.
 */
class IntakeChannel private constructor(
    private val context: Context,
    private val channel: MethodChannel,
    /** True for the translucent sheet's own engine, false for the app's. */
    private val quickOrderEngine: Boolean,
) : MethodChannel.MethodCallHandler {

    private val intake: Intake get() = Intake.get(context)

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        try {
            when (call.method) {
                "status" -> result.success(status())
                "installId" -> result.success(intake.installId())
                "link" -> {
                    val apiBase = call.argument<String>("apiBase")
                    val key = call.argument<String>("ingestKey")
                    val connection = call.argument<String>("connectionId")
                    if (apiBase.isNullOrBlank() || key.isNullOrBlank() || connection.isNullOrBlank()) {
                        result.error("INVALID_ARGUMENT", "apiBase, ingestKey and connectionId are required", null)
                        return
                    }
                    intake.link(IntakeCredentials(apiBase, key, connection))
                    OrderDetectNotifier.ensureChannel(context)
                    IntakeWork.start(context)
                    result.success(null)
                }
                "unlink" -> {
                    intake.unlink().forEach { OrderDetectNotifier.cancel(context, it.notificationId) }
                    IntakeWork.stop(context)
                    result.success(null)
                }
                "syncNow" -> {
                    IntakeWork.heartbeatNow(context, refreshRules = true)
                    result.success(null)
                }
                "openNotificationAccess" -> result.success(openNotificationAccess())
                "openAppSettings" -> result.success(
                    launch(
                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                            .setData(Uri.fromParts("package", context.packageName, null)),
                    ),
                )
                "takeQuickOrder" -> result.success(quickOrder())
                "finishQuickOrder" -> {
                    val id = call.argument<String>("conversationId")
                    if (id != null && QuickOrderState.pendingShare?.id == id) {
                        QuickOrderState.pendingShare = null
                    } else if (id != null && call.argument<Boolean>("created") == true) {
                        intake.finish(id)?.let { OrderDetectNotifier.cancel(context, it.notificationId) }
                    }
                    QuickOrderState.pendingConversationId = null
                    // Only the sheet closes itself; inside the app the screen just pops.
                    if (quickOrderEngine) QuickOrderActivity.finishCurrent()
                    result.success(null)
                }
                "openApp" -> {
                    val launch = context.packageManager.getLaunchIntentForPackage(context.packageName)
                    result.success(launch != null && launch(launch))
                }
                else -> result.notImplemented()
            }
        } catch (error: Exception) {
            Log.w(TAG, "intake channel ${call.method} failed: ${error.javaClass.simpleName}")
            result.error("INTAKE_ERROR", error.javaClass.simpleName, null)
        }
    }

    /** Tell the quick-order screen a new Create tap arrived while it was open. */
    fun quickOrderChanged() {
        channel.invokeMethod("quickOrderChanged", null)
    }

    /** Inside the app: a Create tap arrived; open the quick-order screen. */
    fun openQuickOrder() {
        channel.invokeMethod("openQuickOrder", null)
    }

    private fun status(): Map<String, Any?> {
        val notifications = context.getSystemService(NotificationManager::class.java)
        return intake.status() + mapOf(
            "supported" to true,
            "accessGranted" to OrderNotificationListener.accessGranted(context),
            "notificationsEnabled" to (notifications?.areNotificationsEnabled() == true),
            "manufacturer" to Build.MANUFACTURER.orEmpty().lowercase(),
        )
    }

    private fun quickOrder(): Map<String, Any?>? {
        val id = QuickOrderState.pendingConversationId ?: return null
        QuickOrderState.pendingShare?.takeIf { it.id == id }?.let { share ->
            return mapOf(
                "conversationId" to share.id,
                "expired" to false,
                "source" to "SHARE",
                "messages" to listOf(mapOf("text" to share.text, "sentAt" to share.sharedAt)),
            )
        }
        val conversation = intake.conversation(id) ?: return mapOf("conversationId" to id, "expired" to true)
        val result = intake.classify(conversation)
        return mapOf(
            "conversationId" to conversation.id,
            "expired" to false,
            "package" to conversation.pkg,
            "conversationKey" to conversation.title,
            "senderDisplay" to conversation.senderDisplay,
            "connectionId" to intake.credentials()?.connectionId,
            "maskedPhone" to result?.maskedPhone,
            "district" to result?.district,
            "messages" to conversation.messages.map { mapOf("text" to it.text, "sentAt" to it.sentAt) },
        )
    }

    private fun openNotificationAccess(): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val component = ComponentName(context, OrderNotificationListener::class.java)
            val detail = Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS)
                .putExtra(Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME, component.flattenToString())
            if (launch(detail)) return true
        }
        return launch(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
    }

    private fun launch(intent: Intent): Boolean = try {
        context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        true
    } catch (error: Exception) {
        false
    }

    companion object {
        const val NAME = "ecomsbd/intake"
        private const val TAG = "EcomsbdIntake"

        fun register(engine: FlutterEngine, context: Context, quickOrderEngine: Boolean = false): IntakeChannel {
            val channel = MethodChannel(engine.dartExecutor.binaryMessenger, NAME)
            return IntakeChannel(context.applicationContext, channel, quickOrderEngine)
                .also(channel::setMethodCallHandler)
        }
    }
}
