import Flutter
import UIKit
import AdServices

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    guard let registrar = engineBridge.pluginRegistry.registrar(forPlugin: "FutureGrowTelemetry") else { return }
    FlutterMethodChannel(name: "ecomsbd/telemetry", binaryMessenger: registrar.messenger()).setMethodCallHandler { call, result in
      guard call.method == "appleAttributionToken" else { result(FlutterMethodNotImplemented); return }
      if #available(iOS 14.3, *) {
        do { result(try AAAttribution.attributionToken()) }
        catch { result(FlutterError(code: "attribution_unavailable", message: "Attribution token unavailable", details: nil)) }
      } else { result(nil) }
    }
  }
}
