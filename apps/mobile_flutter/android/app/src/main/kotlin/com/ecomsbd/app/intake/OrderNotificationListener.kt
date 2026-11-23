package com.ecomsbd.app.intake

import android.app.Notification
import android.content.ComponentName
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.os.Parcelable
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log

/**
 * Reads chat notifications of the apps the shop turned on (V4 WP1.4, rule L4/L6).
 *
 * Nothing here is sent anywhere by itself: text goes into the encrypted buffer,
 * the classifier runs on the phone, and an order-like chat raises a heads-up.
 * Only the seller's **Create** tap (or Autopilot consent, Phase 4) uploads it.
 *
 * A listener that throws is a crash in the seller's notification pipeline, so
 * every callback catches everything and logs only the exception class.
 */
class OrderNotificationListener : NotificationListenerService() {

    override fun onListenerConnected() {
        guard {
            Intake.get(this).setListenerConnected(true)
            IntakeWork.heartbeatNow(this)
        }
    }

    override fun onListenerDisconnected() {
        guard {
            Intake.get(this).setListenerConnected(false)
            IntakeWork.heartbeatNow(this)
            // Ask the system to bind again (it unbinds after updates and on some OEM kills).
            requestRebind(ComponentName(this, OrderNotificationListener::class.java))
        }
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        if (sbn == null) return
        guard {
            val intake = Intake.get(this)
            val rules = intake.rules() ?: return@guard
            if (sbn.packageName !in rules.enabledPackages) return@guard
            val capture = NotificationReader.read(sbn, packageName) ?: return@guard
            val now = System.currentTimeMillis()
            val outcome = intake.onNotification(capture, now) ?: return@guard
            for (gone in outcome.dropped) OrderDetectNotifier.cancel(this, gone.notificationId)
            if (outcome is IntakeEngine.Buffered) {
                if (outcome.notify) {
                    OrderDetectNotifier.show(this, outcome.conversation, outcome.result, rules.bufferWindowMillis)
                }
                if (outcome.upload != null) IntakeWork.uploadNow(this)
            }
        }
    }

    private inline fun guard(block: () -> Unit) {
        try {
            block()
        } catch (error: Exception) {
            Log.w(TAG, "intake listener skipped a notification: ${error.javaClass.simpleName}")
        }
    }

    companion object {
        private const val TAG = "EcomsbdIntake"

        /** Has the seller granted Notification Access to this app? */
        @JvmStatic
        fun accessGranted(context: Context): Boolean {
            val component = ComponentName(context, OrderNotificationListener::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
                val manager = context.getSystemService(android.app.NotificationManager::class.java)
                return manager?.isNotificationListenerAccessGranted(component) == true
            }
            val enabled = Settings.Secure.getString(context.contentResolver, "enabled_notification_listeners")
            return enabled?.split(':')?.any { ComponentName.unflattenFromString(it) == component } == true
        }
    }
}

/** Takes a [StatusBarNotification] apart into what the engine needs, or null to skip it. */
object NotificationReader {

    fun read(sbn: StatusBarNotification, ownPackage: String): CapturedNotification? {
        if (sbn.packageName == ownPackage) return null
        val notification = sbn.notification ?: return null
        if (notification.flags and Notification.FLAG_GROUP_SUMMARY != 0) return null
        if (sbn.isOngoing || notification.flags and Notification.FLAG_ONGOING_EVENT != 0) return null
        val extras = notification.extras ?: return null
        // Group chats are not one customer's order; their titles name the group.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P &&
            extras.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION, false)
        ) {
            return null
        }
        val title = (extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)
            ?: extras.getCharSequence(Notification.EXTRA_TITLE))
            ?.toString()?.trim()?.take(MAX_TITLE)
        if (title.isNullOrEmpty()) return null

        val styled = messagingStyle(extras)
        if (styled != null) {
            if (styled.isEmpty()) return null // only the seller's own replies
            return CapturedNotification(
                pkg = sbn.packageName,
                title = title,
                senderDisplay = styled.last().second ?: title,
                messages = styled.map { it.first },
            )
        }
        val text = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
            ?: extras.getCharSequence(Notification.EXTRA_TEXT))?.toString()
        if (text.isNullOrBlank()) return null
        return CapturedNotification(
            pkg = sbn.packageName,
            title = title,
            senderDisplay = title,
            messages = listOf(ConversationBuffer.Incoming(text, notification.`when`.takeIf { it > 0 })),
        )
    }

    /**
     * MessagingStyle messages from someone other than the phone's owner, with
     * their sender's name; null when the notification is not MessagingStyle.
     */
    private fun messagingStyle(extras: Bundle): List<Pair<ConversationBuffer.Incoming, String?>>? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return null
        val parcels: Array<Parcelable>? =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                extras.getParcelableArray(Notification.EXTRA_MESSAGES, Parcelable::class.java)
            } else {
                @Suppress("DEPRECATION")
                extras.getParcelableArray(Notification.EXTRA_MESSAGES)
            }
        if (parcels.isNullOrEmpty()) return null
        return Notification.MessagingStyle.Message.getMessagesFromBundleArray(parcels).mapNotNull { message ->
            // A null sender is the device owner: the seller's own reply is not an order.
            @Suppress("DEPRECATION") // the pre-Person field, still set by some apps
            val legacySender = message.sender
            val sender = message.senderPerson?.name ?: legacySender ?: return@mapNotNull null
            val text = message.text?.toString()
            if (text.isNullOrBlank()) return@mapNotNull null
            ConversationBuffer.Incoming(text, message.timestamp.takeIf { it > 0 }) to sender.toString()
        }
    }

    private const val MAX_TITLE = 200
}
