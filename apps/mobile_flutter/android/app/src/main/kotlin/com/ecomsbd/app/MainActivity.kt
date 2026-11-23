package com.ecomsbd.app

import android.content.Intent
import android.os.Bundle
import com.ecomsbd.app.intake.IntakeChannel
import com.ecomsbd.app.intake.OrderDetectNotifier
import com.ecomsbd.app.intake.QuickOrderEngine
import com.ecomsbd.app.intake.QuickOrderState
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine

class MainActivity : FlutterActivity() {
    private var intake: IntakeChannel? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        // V4 WP1.4: one engine holds the seller's session at a time. The app is
        // opening, so the quick-order sheet's engine (if any) goes first.
        QuickOrderEngine.release()
        alive += 1
        super.onCreate(savedInstanceState)
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // The Auto order detect settings screen and in-app quick order talk to the native intake.
        intake = IntakeChannel.register(flutterEngine, applicationContext)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val conversation = intent.getStringExtra(OrderDetectNotifier.EXTRA_CONVERSATION) ?: return
        QuickOrderState.pendingConversationId = conversation
        intake?.openQuickOrder()
    }

    override fun onDestroy() {
        alive -= 1
        intake = null
        super.onDestroy()
    }

    companion object {
        @Volatile
        private var alive = 0

        /** An app window (and so the app's engine and session) exists. */
        val isAlive: Boolean get() = alive > 0
    }
}
