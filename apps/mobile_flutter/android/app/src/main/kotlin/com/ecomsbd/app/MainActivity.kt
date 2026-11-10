package com.ecomsbd.app

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import com.android.installreferrer.api.InstallReferrerClient
import com.android.installreferrer.api.InstallReferrerStateListener
import android.os.Handler
import android.os.Looper

class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "ecomsbd/telemetry").setMethodCallHandler { call, result ->
            if (call.method != "installReferrer") { result.notImplemented(); return@setMethodCallHandler }
            val client = InstallReferrerClient.newBuilder(applicationContext).build()
            val handler = Handler(Looper.getMainLooper())
            var finished = false
            fun finish(value: String?, error: String? = null) {
                if (finished) return
                finished = true
                client.endConnection()
                if (error == null) result.success(value) else result.error("referrer_unavailable", error, null)
            }
            handler.postDelayed({ finish(null, "Timed out; retry later") }, 10000)
            try {
                client.startConnection(object : InstallReferrerStateListener {
                    override fun onInstallReferrerSetupFinished(code: Int) {
                        when (code) {
                            InstallReferrerClient.InstallReferrerResponse.OK -> try { finish(client.installReferrer.installReferrer) } catch (_: Exception) { finish(null, "Referrer read failed") }
                            InstallReferrerClient.InstallReferrerResponse.FEATURE_NOT_SUPPORTED -> finish(null)
                            else -> finish(null, "Play referrer temporarily unavailable")
                        }
                    }
                    override fun onInstallReferrerServiceDisconnected() { finish(null, "Play referrer disconnected") }
                })
            } catch (_: Exception) { finish(null, "Play referrer unavailable") }
        }
    }
}
