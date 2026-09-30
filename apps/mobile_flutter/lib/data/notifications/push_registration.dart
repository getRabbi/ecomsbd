import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

bool get _isIos => !kIsWeb && defaultTargetPlatform == TargetPlatform.iOS;

/// Firebase reads android/app/google-services.json on Android and the
/// GoogleService-Info.plist bundled into the iOS Runner on iOS.
Future<void> initializePush() async {
  if (!kIsWeb &&
      (defaultTargetPlatform == TargetPlatform.android ||
          defaultTargetPlatform == TargetPlatform.iOS)) {
    await Firebase.initializeApp();
  }
}

class PushRegistration {
  StreamSubscription<String>? _subscription;
  bool get _available => Firebase.apps.isNotEmpty;

  Future<String?> token() async {
    if (!_available) return null;
    try {
      final messaging = FirebaseMessaging.instance;
      final permission = await messaging.requestPermission();
      if (permission.authorizationStatus == AuthorizationStatus.denied) {
        return null;
      }
      // iOS mints an FCM token only once APNs has issued the device token,
      // and asking before that throws. If APNs is slow, onTokenRefresh
      // delivers the token when it arrives.
      if (_isIos && !await _apnsTokenReady(messaging)) return null;
      return await messaging.getToken();
    } on FirebaseException {
      // Push is optional; the authenticated notification centre remains available.
      return null;
    }
  }

  static Future<bool> _apnsTokenReady(FirebaseMessaging messaging) async {
    for (var attempt = 0; attempt < 10; attempt++) {
      if (await messaging.getAPNSToken() != null) return true;
      await Future<void>.delayed(const Duration(milliseconds: 500));
    }
    return false;
  }

  void listen(Future<void> Function(String) register) {
    if (!_available || _subscription != null) return;
    _subscription = FirebaseMessaging.instance.onTokenRefresh.listen((token) {
      unawaited(register(token));
    }, onError: (Object _) {});
  }

  Future<void> clear() async {
    if (!_available) return;
    try {
      await FirebaseMessaging.instance.deleteToken();
    } on FirebaseException {
      // Backend logout also clears the associated device token.
    }
  }

  void dispose() => unawaited(_subscription?.cancel());
}
