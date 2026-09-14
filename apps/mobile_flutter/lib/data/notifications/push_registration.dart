import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

/// Android Firebase reads the app-module google-services configuration.
Future<void> initializePush() async {
  if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
    await Firebase.initializeApp();
  }
}

class PushRegistration {
  StreamSubscription<String>? _subscription;
  bool get _available => Firebase.apps.isNotEmpty;

  Future<String?> token() async {
    if (!_available) return null;
    try {
      final permission = await FirebaseMessaging.instance.requestPermission();
      if (permission.authorizationStatus == AuthorizationStatus.denied) {
        return null;
      }
      return await FirebaseMessaging.instance.getToken();
    } on FirebaseException {
      // Push is optional; the authenticated notification centre remains available.
      return null;
    }
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
