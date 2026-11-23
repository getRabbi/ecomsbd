import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// The phone's native order detection (V4 WP1.4, Android only), over the
/// MethodChannel `ecomsbd/intake`.
///
/// The ingest key goes to native once ([IntakeNative.link]) and never comes
/// back: [IntakeDeviceStatus] only says whether the phone is linked.
@immutable
class IntakeDeviceStatus {
  const IntakeDeviceStatus({
    required this.supported,
    this.accessGranted = false,
    this.notificationsEnabled = false,
    this.linked = false,
    this.revoked = false,
    this.connectionId,
    this.lastHeartbeatAt,
    this.listenerConnected = false,
    this.rulesVersion,
    this.enabledPackages = const <String>[],
    this.autopilotOn = false,
    this.pendingUploads = 0,
    this.manufacturer = '',
  });

  factory IntakeDeviceStatus.fromMap(Map<Object?, Object?> map) {
    final heartbeat = map['lastHeartbeatAt'];
    return IntakeDeviceStatus(
      supported: map['supported'] == true,
      accessGranted: map['accessGranted'] == true,
      notificationsEnabled: map['notificationsEnabled'] == true,
      linked: map['linked'] == true,
      revoked: map['revoked'] == true,
      connectionId: map['connectionId'] as String?,
      lastHeartbeatAt: heartbeat is int
          ? DateTime.fromMillisecondsSinceEpoch(heartbeat, isUtc: true)
          : null,
      listenerConnected: map['listenerConnected'] == true,
      rulesVersion: map['rulesVersion'] as int?,
      enabledPackages: <String>[
        for (final item in (map['enabledPackages'] as List<Object?>? ?? []))
          if (item is String) item,
      ],
      autopilotOn: map['autopilotOn'] == true,
      pendingUploads: map['pendingUploads'] as int? ?? 0,
      manufacturer: (map['manufacturer'] as String? ?? '').toLowerCase(),
    );
  }

  /// Not Android (or an older build): the screen says so.
  static const unsupported = IntakeDeviceStatus(supported: false);

  /// The server marks a phone NEEDS_ATTENTION after this long without one.
  static const staleAfter = Duration(hours: 24);

  final bool supported;
  final bool accessGranted;
  final bool notificationsEnabled;
  final bool linked;

  /// The server refused the key (unlinked from the web or the team member removed).
  final bool revoked;
  final String? connectionId;
  final DateTime? lastHeartbeatAt;
  final bool listenerConnected;
  final int? rulesVersion;
  final List<String> enabledPackages;
  final bool autopilotOn;
  final int pendingUploads;
  final String manufacturer;

  bool get working => linked && !revoked && accessGranted;

  /// Linked, but the phone has not checked in for a day: usually the OEM
  /// battery manager stopped the app, so the auto-start guide is shown.
  bool isStale(DateTime now) {
    if (!linked || revoked) return false;
    final last = lastHeartbeatAt;
    return last != null && now.difference(last) > staleAfter;
  }
}

/// What the Create tap carries: the buffered chat, read on the phone.
@immutable
class QuickOrderCapture {
  const QuickOrderCapture({
    required this.conversationId,
    required this.expired,
    this.source = deviceNotification,
    this.package,
    this.conversationKey,
    this.senderDisplay,
    this.connectionId,
    this.maskedPhone,
    this.district,
    this.messages = const <QuickOrderMessage>[],
  });

  factory QuickOrderCapture.fromMap(Map<Object?, Object?> map) {
    return QuickOrderCapture(
      conversationId: map['conversationId']! as String,
      expired: map['expired'] == true,
      source: map['source'] == share ? share : deviceNotification,
      package: map['package'] as String?,
      conversationKey: map['conversationKey'] as String?,
      senderDisplay: map['senderDisplay'] as String?,
      connectionId: map['connectionId'] as String?,
      maskedPhone: map['maskedPhone'] as String?,
      district: map['district'] as String?,
      messages: <QuickOrderMessage>[
        for (final item in (map['messages'] as List<Object?>? ?? []))
          if (item is Map)
            QuickOrderMessage(
              text: item['text'] as String? ?? '',
              sentAt: item['sentAt'] is int
                  ? DateTime.fromMillisecondsSinceEpoch(
                      item['sentAt'] as int,
                      isUtc: true,
                    )
                  : null,
            ),
      ],
    );
  }

  static const deviceNotification = 'DEVICE_NOTIFICATION';

  /// Text another app shared to ecomsbd (Android share target, V4 WP1.5).
  static const share = 'SHARE';

  final String conversationId;

  /// [deviceNotification] or [share].
  final String source;

  bool get isShare => source == share;

  /// The chat closed on the phone (25 minutes without a message) before the tap.
  final bool expired;
  final String? package;
  final String? conversationKey;
  final String? senderDisplay;
  final String? connectionId;
  final String? maskedPhone;
  final String? district;
  final List<QuickOrderMessage> messages;

  /// The conversation on the server. A share has none of its own; its id
  /// keeps later "Aro message lagbe" pastes on the same draft.
  String get _conversation => conversationKey ?? 'share:$conversationId';

  /// `POST /v1/intake/captures/manual` body for this tap.
  Map<String, Object?> manualCaptureBody() => _body(<Map<String, Object?>>[
    for (final message in messages)
      <String, Object?>{
        'text': message.text,
        'sent_at': message.sentAt?.toUtc().toIso8601String(),
      },
  ]);

  /// More messages the seller pasted into the open sheet ("Aro message
  /// lagbe"), for the same conversation and so the same draft.
  Map<String, Object?> moreMessagesBody(String text, DateTime now) => _body(
    <Map<String, Object?>>[
      <String, Object?>{'text': text, 'sent_at': now.toUtc().toIso8601String()},
    ],
  );

  Map<String, Object?> _body(List<Map<String, Object?>> messages) =>
      <String, Object?>{
        'source': source,
        'package': package,
        'conversation_key': _conversation,
        'sender_display': senderDisplay,
        'connection_id': connectionId,
        'messages': messages,
      };
}

@immutable
class QuickOrderMessage {
  const QuickOrderMessage({required this.text, this.sentAt});
  final String text;
  final DateTime? sentAt;
}

/// Calls from native to Dart.
enum IntakeEvent {
  /// The sheet is open and another Create tap arrived.
  quickOrderChanged,

  /// Inside the app: a Create tap arrived; open the quick-order screen.
  openQuickOrder,
}

abstract interface class IntakeNative {
  Future<IntakeDeviceStatus> status();
  Future<String> installId();
  Future<void> link({
    required String apiBase,
    required String ingestKey,
    required String connectionId,
  });
  Future<void> unlink();
  Future<void> syncNow();
  Future<bool> openNotificationAccess();
  Future<bool> openAppSettings();
  Future<bool> openApp();
  Future<QuickOrderCapture?> takeQuickOrder();
  Future<void> finishQuickOrder({
    required String conversationId,
    required bool created,
  });
  Stream<IntakeEvent> get events;
}

/// The real channel. On anything but Android, [status] reports unsupported.
class MethodChannelIntake implements IntakeNative {
  MethodChannelIntake({MethodChannel? channel, bool? android})
    : _channel = channel ?? const MethodChannel(channelName),
      _android =
          android ??
          (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
    _channel.setMethodCallHandler(_onCall);
  }

  static const channelName = 'ecomsbd/intake';

  final MethodChannel _channel;
  final bool _android;
  final _events = StreamController<IntakeEvent>.broadcast();

  Future<Object?> _onCall(MethodCall call) async {
    switch (call.method) {
      case 'quickOrderChanged':
        _events.add(IntakeEvent.quickOrderChanged);
      case 'openQuickOrder':
        _events.add(IntakeEvent.openQuickOrder);
    }
    return null;
  }

  @override
  Stream<IntakeEvent> get events => _events.stream;

  Future<T?> _call<T>(String method, [Map<String, Object?>? arguments]) {
    return _channel.invokeMethod<T>(method, arguments);
  }

  @override
  Future<IntakeDeviceStatus> status() async {
    if (!_android) return IntakeDeviceStatus.unsupported;
    try {
      final map = await _call<Map<Object?, Object?>>('status');
      return map == null
          ? IntakeDeviceStatus.unsupported
          : IntakeDeviceStatus.fromMap(map);
    } on MissingPluginException {
      return IntakeDeviceStatus.unsupported;
    }
  }

  @override
  Future<String> installId() async => (await _call<String>('installId'))!;

  @override
  Future<void> link({
    required String apiBase,
    required String ingestKey,
    required String connectionId,
  }) => _call<void>('link', <String, Object?>{
    'apiBase': apiBase,
    'ingestKey': ingestKey,
    'connectionId': connectionId,
  });

  @override
  Future<void> unlink() => _call<void>('unlink');

  @override
  Future<void> syncNow() => _call<void>('syncNow');

  @override
  Future<bool> openNotificationAccess() async =>
      await _call<bool>('openNotificationAccess') ?? false;

  @override
  Future<bool> openAppSettings() async =>
      await _call<bool>('openAppSettings') ?? false;

  @override
  Future<bool> openApp() async => await _call<bool>('openApp') ?? false;

  @override
  Future<QuickOrderCapture?> takeQuickOrder() async {
    if (!_android) return null;
    final map = await _call<Map<Object?, Object?>>('takeQuickOrder');
    return map == null ? null : QuickOrderCapture.fromMap(map);
  }

  @override
  Future<void> finishQuickOrder({
    required String conversationId,
    required bool created,
  }) => _call<void>('finishQuickOrder', <String, Object?>{
    'conversationId': conversationId,
    'created': created,
  });
}

final intakeNativeProvider = Provider<IntakeNative>(
  (ref) => MethodChannelIntake(),
);

/// The phone's state, re-read when the screen asks (after returning from
/// system settings, after linking).
final intakeDeviceStatusProvider =
    FutureProvider.autoDispose<IntakeDeviceStatus>(
      (ref) => ref.watch(intakeNativeProvider).status(),
    );
