import '../../core/api/api_client.dart';
import 'models.dart';

/// Devices, sessions, notification preferences, exports and deletion.
///
/// None of it is cached. Every read here answers a security or privacy
/// question — "which devices are signed in?", "is deletion scheduled?" — and a
/// stale answer to either is worse than no answer. A seller looking at a device
/// list to find the phone they lost needs it to be true right now.
class AccountRepository {
  const AccountRepository(this._api);

  final ApiClient _api;

  // --- devices and sessions -------------------------------------------------

  Future<List<DeviceInfo>> devices() async {
    final rows = await _api.getList('/account/devices');
    return <DeviceInfo>[
      for (final row in rows) DeviceInfo.fromJson(row as Map<String, dynamic>),
    ];
  }

  Future<List<SessionInfo>> sessions() async {
    final rows = await _api.getList('/account/sessions');
    return <SessionInfo>[
      for (final row in rows) SessionInfo.fromJson(row as Map<String, dynamic>),
    ];
  }

  Future<void> revokeDevice(String deviceId) async {
    await _api.post('/account/devices/$deviceId/revoke');
  }

  Future<void> logoutOtherDevices() async {
    await _api.post('/account/logout-others');
  }

  // --- notifications --------------------------------------------------------

  Future<NotificationPreferences> notificationPreferences() async {
    return NotificationPreferences.fromJson(
      await _api.get('/account/notification-preferences'),
    );
  }

  Future<NotificationPreferences> updateNotificationPreferences({
    bool? pushEnabled,
    bool? smsEnabled,
    bool? routineTrackingPush,
    List<String>? mutedKinds,
  }) async {
    return NotificationPreferences.fromJson(
      await _api.patch(
        '/account/notification-preferences',
        body: <String, dynamic>{
          if (pushEnabled != null) 'push_enabled': pushEnabled,
          if (smsEnabled != null) 'sms_enabled': smsEnabled,
          if (routineTrackingPush != null)
            'routine_tracking_push': routineTrackingPush,
          if (mutedKinds != null) 'muted_kinds': mutedKinds,
        },
      ),
    );
  }

  // --- privacy --------------------------------------------------------------

  Future<PrivacyStatus> privacy() async {
    return PrivacyStatus.fromJson(await _api.get('/account/privacy'));
  }

  /// Ask for the account to be deleted.
  ///
  /// [confirm] is text the seller typed. The server does not check what it
  /// says — the guard that matters is the ownership re-check and the
  /// cooling-off window — but requiring it makes the action deliberate rather
  /// than a mis-tap on the one screen that cannot be undone by tapping again.
  Future<DeletionSchedule> requestDeletion({
    required String confirm,
    String? reason,
  }) async {
    return DeletionSchedule.fromJson(
      await _api.post(
        '/account/delete',
        body: <String, dynamic>{
          'confirm': confirm,
          if (reason != null && reason.isNotEmpty) 'reason': reason,
        },
      ),
    );
  }

  Future<DeletionSchedule> cancelDeletion() async {
    return DeletionSchedule.fromJson(await _api.post('/account/delete/cancel'));
  }

  // --- exports --------------------------------------------------------------

  Future<List<ExportJob>> exports() async {
    final rows = await _api.getList('/exports');
    return <ExportJob>[
      for (final row in rows) ExportJob.fromJson(row as Map<String, dynamic>),
    ];
  }

  /// Request an export. The download token comes back **once**.
  Future<ExportJob> createExport({
    required String kind,
    DateTime? since,
    DateTime? until,
  }) async {
    String? day(DateTime? value) => value?.toIso8601String().split('T').first;

    return ExportJob.fromJson(
      await _api.post(
        '/exports',
        body: <String, dynamic>{
          'kind': kind,
          if (since != null) 'since': day(since),
          if (until != null) 'until': day(until),
        },
      ),
    );
  }
}
