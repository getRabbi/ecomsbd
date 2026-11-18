package com.ecomsbd.app.intake

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import com.ecomsbd.app.MainActivity
import io.flutter.FlutterInjector
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.android.FlutterActivityLaunchConfigs
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.engine.FlutterEngineCache
import io.flutter.embedding.engine.dart.DartExecutor
import java.lang.ref.WeakReference

/** The conversation the last Create tap was for; read once by `takeQuickOrder`. */
object QuickOrderState {
    @Volatile
    var pendingConversationId: String? = null
}

/**
 * Create on the heads-up lands here first (no UI). The seller's session may be
 * held by only one Flutter engine at a time — two engines each refreshing the
 * same Supabase session would trip refresh-token reuse detection and sign the
 * seller out. So:
 *
 * * the app is open → the quick-order screen opens inside it;
 * * the app is not open → the translucent [QuickOrderActivity] on its own engine.
 */
class QuickOrderLauncherActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id = intent?.getStringExtra(OrderDetectNotifier.EXTRA_CONVERSATION)
        QuickOrderState.pendingConversationId = id
        val target = if (MainActivity.isAlive) MainActivity::class.java else QuickOrderActivity::class.java
        startActivity(
            Intent(this, target)
                .putExtra(OrderDetectNotifier.EXTRA_CONVERSATION, id)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        )
        finish()
    }
}

/**
 * The translucent quick-order sheet (rule L6: an activity, never an overlay).
 * It shows the Flutter route `/quick-order` on a cached engine, so taps in a
 * burst open instantly; the buffered text reaches Flutter through
 * [IntakeChannel], never through the intent (only the conversation's random id
 * travels there).
 */
class QuickOrderActivity : FlutterActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        intent?.getStringExtra(OrderDetectNotifier.EXTRA_CONVERSATION)?.let {
            QuickOrderState.pendingConversationId = it
        }
        current = WeakReference(this)
        QuickOrderEngine.cancelIdleRelease()
        super.onCreate(savedInstanceState)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        intent.getStringExtra(OrderDetectNotifier.EXTRA_CONVERSATION)?.let {
            QuickOrderState.pendingConversationId = it
        }
        QuickOrderEngine.channel?.quickOrderChanged()
    }

    override fun onDestroy() {
        val wasCurrent = current?.get() === this
        if (wasCurrent) current = null
        super.onDestroy()
        if (wasCurrent) QuickOrderEngine.afterSheetClosed()
    }

    override fun provideFlutterEngine(context: Context): FlutterEngine = QuickOrderEngine.obtain(context)

    override fun shouldDestroyEngineWithHost(): Boolean = false

    override fun getBackgroundMode(): FlutterActivityLaunchConfigs.BackgroundMode =
        FlutterActivityLaunchConfigs.BackgroundMode.transparent

    companion object {
        @Volatile
        private var current: WeakReference<QuickOrderActivity>? = null

        val isOpen: Boolean get() = current?.get() != null

        fun finishCurrent() {
            current?.get()?.finish()
        }
    }
}

/**
 * One Flutter engine for the quick-order sheet: started on first use, kept for
 * [IDLE_MILLIS] after the sheet closes (orders come in bursts), then released
 * so no second engine idles in the background. Released at once when the app
 * itself opens (one session holder at a time).
 */
object QuickOrderEngine {
    const val ID = "ecomsbd.quick_order"
    const val ROUTE = "/quick-order"
    private const val ENTRYPOINT = "quickOrderMain"
    private const val IDLE_MILLIS = 10 * 60_000L

    private val main = Handler(Looper.getMainLooper())
    private val idleRelease = Runnable { release() }
    private var releaseWhenClosed = false

    @Volatile
    var channel: IntakeChannel? = null
        private set

    @Synchronized
    fun obtain(context: Context): FlutterEngine {
        releaseWhenClosed = false
        FlutterEngineCache.getInstance().get(ID)?.let { return it }
        val app = context.applicationContext
        val engine = FlutterEngine(app)
        engine.navigationChannel.setInitialRoute(ROUTE)
        channel = IntakeChannel.register(engine, app, quickOrderEngine = true)
        engine.dartExecutor.executeDartEntrypoint(
            DartExecutor.DartEntrypoint(
                FlutterInjector.instance().flutterLoader().findAppBundlePath(),
                ENTRYPOINT,
            ),
        )
        FlutterEngineCache.getInstance().put(ID, engine)
        return engine
    }

    fun cancelIdleRelease() = main.removeCallbacks(idleRelease)

    @Synchronized
    fun afterSheetClosed() {
        if (releaseWhenClosed) {
            release()
        } else {
            main.removeCallbacks(idleRelease)
            main.postDelayed(idleRelease, IDLE_MILLIS)
        }
    }

    /** The app opened: close the sheet and drop this engine and its session. */
    @Synchronized
    fun release() {
        main.removeCallbacks(idleRelease)
        if (QuickOrderActivity.isOpen) {
            releaseWhenClosed = true
            QuickOrderActivity.finishCurrent()
            return
        }
        releaseWhenClosed = false
        val engine = FlutterEngineCache.getInstance().get(ID) ?: return
        FlutterEngineCache.getInstance().remove(ID)
        channel = null
        engine.destroy()
    }
}
