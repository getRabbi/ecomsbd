package com.ecomsbd.app.intake

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.os.Build
import android.util.Log
import com.ecomsbd.app.R

/**
 * The heads-up for an order-like chat (rule L6: no overlay, a notification).
 * One per conversation, updated in place; it times out when the buffer closes.
 *
 * On the lock screen only a public version shows ("order mone hocche"), never
 * the customer's name or number.
 */
object OrderDetectNotifier {
    const val CHANNEL = "order_detect"
    const val EXTRA_CONVERSATION = "com.ecomsbd.app.intake.CONVERSATION"
    const val ACTION_IGNORE = "com.ecomsbd.app.intake.IGNORE"
    const val ACTION_BLOCK = "com.ecomsbd.app.intake.BLOCK"
    private const val TAG = "EcomsbdIntake"

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL) != null) return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL,
                context.getString(R.string.intake_channel_name),
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = context.getString(R.string.intake_channel_description)
                lockscreenVisibility = Notification.VISIBILITY_PRIVATE
            },
        )
    }

    fun show(
        context: Context,
        conversation: ConversationBuffer.Conversation,
        result: IntakeClassifier.Result,
        windowMillis: Long,
    ) {
        ensureChannel(context)
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        val sender = conversation.senderDisplay?.takeIf { it.isNotBlank() } ?: conversation.title
        val body = listOfNotNull(result.maskedPhone, result.district).joinToString(" · ")
        val id = conversation.notificationId
        val create = PendingIntent.getActivity(
            context,
            id * 4,
            Intent(context, QuickOrderLauncherActivity::class.java)
                .putExtra(EXTRA_CONVERSATION, conversation.id)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val publicVersion = builder(context)
            .setContentTitle(context.getString(R.string.intake_public_title))
            .build()
        val notification = builder(context)
            .setContentTitle(context.getString(R.string.intake_heads_up_title, sender))
            .setContentText(body)
            .setCategory(Notification.CATEGORY_MESSAGE)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setPublicVersion(publicVersion)
            .setOnlyAlertOnce(true)
            .setAutoCancel(true)
            .setContentIntent(create)
            .addAction(action(context, R.string.intake_action_create, create))
            .addAction(action(context, R.string.intake_action_ignore, broadcast(context, ACTION_IGNORE, conversation.id, id * 4 + 1)))
            .addAction(action(context, R.string.intake_action_block, broadcast(context, ACTION_BLOCK, conversation.id, id * 4 + 2)))
            .apply {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) setTimeoutAfter(windowMillis)
                @Suppress("DEPRECATION")
                if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) setPriority(Notification.PRIORITY_HIGH)
            }
            .build()
        try {
            manager.notify(id, notification)
        } catch (error: SecurityException) {
            // POST_NOTIFICATIONS not granted: the settings screen shows that.
            Log.w(TAG, "heads-up not shown: notifications are off")
        }
    }

    fun cancel(context: Context, notificationId: Int) {
        context.getSystemService(NotificationManager::class.java)?.cancel(notificationId)
    }

    private fun builder(context: Context): Notification.Builder {
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(context, CHANNEL)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(context)
        }
        return builder
            .setSmallIcon(R.drawable.ic_stat_ecomsbd)
            .setColor(context.getColor(R.color.ecomsbd_notification))
    }

    private fun action(context: Context, label: Int, intent: PendingIntent): Notification.Action =
        Notification.Action.Builder(null as Icon?, context.getString(label), intent).build()

    private fun broadcast(context: Context, action: String, conversationId: String, requestCode: Int): PendingIntent =
        PendingIntent.getBroadcast(
            context,
            requestCode,
            Intent(context, IntakeActionReceiver::class.java)
                .setAction(action)
                .putExtra(EXTRA_CONVERSATION, conversationId),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
}

/** Ignore / "Ei sender ke ar na" from the heads-up. Not exported: only our own PendingIntents reach it. */
class IntakeActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra(OrderDetectNotifier.EXTRA_CONVERSATION) ?: return
        try {
            val intake = Intake.get(context)
            val conversation = when (intent.action) {
                OrderDetectNotifier.ACTION_IGNORE -> intake.ignore(id)
                OrderDetectNotifier.ACTION_BLOCK -> intake.block(id)
                else -> null
            } ?: return
            OrderDetectNotifier.cancel(context, conversation.notificationId)
        } catch (error: Exception) {
            Log.w("EcomsbdIntake", "heads-up action failed: ${error.javaClass.simpleName}")
        }
    }
}
