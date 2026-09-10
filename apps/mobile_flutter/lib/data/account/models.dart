/// Account, device and privacy models.
///
/// Master spec sections 89, 99, 100. Nothing here carries a credential: a push
/// token is reported as a boolean, an export token exists for exactly one
/// response, and a phone number never appears at all.
library;

/// One signed-in installation.
class DeviceInfo {
  const DeviceInfo({
    required this.id,
    required this.platform,
    required this.lastSeenAt,
    required this.createdAt,
    required this.revoked,
    required this.isCurrent,
    required this.pushEnabled,
    this.appVersion,
    this.osVersion,
    this.model,
  });

  factory DeviceInfo.fromJson(Map<String, dynamic> json) {
    return DeviceInfo(
      id: json['id'] as String,
      platform: json['platform'] as String,
      appVersion: json['app_version'] as String?,
      osVersion: json['os_version'] as String?,
      model: json['model'] as String?,
      lastSeenAt: DateTime.parse(json['last_seen_at'] as String).toLocal(),
      createdAt: DateTime.parse(json['created_at'] as String).toLocal(),
      revoked: json['revoked'] as bool? ?? false,
      isCurrent: json['is_current'] as bool? ?? false,
      pushEnabled: json['push_enabled'] as bool? ?? false,
    );
  }

  final String id;
  final String platform;
  final String? appVersion;
  final String? osVersion;
  final String? model;
  final DateTime lastSeenAt;
  final DateTime createdAt;
  final bool revoked;
  final bool isCurrent;
  final bool pushEnabled;

  String get displayName {
    final parts = <String>[
      if (model != null && model!.isNotEmpty) model! else platform,
      if (osVersion != null && osVersion!.isNotEmpty) osVersion!,
    ];
    return parts.join(' · ');
  }
}

class SessionInfo {
  const SessionInfo({
    required this.id,
    required this.createdAt,
    required this.lastSeenAt,
    required this.isCurrent,
    this.deviceId,
  });

  factory SessionInfo.fromJson(Map<String, dynamic> json) {
    return SessionInfo(
      id: json['id'] as String,
      deviceId: json['device_id'] as String?,
      createdAt: DateTime.parse(json['created_at'] as String).toLocal(),
      lastSeenAt: DateTime.parse(json['last_seen_at'] as String).toLocal(),
      isCurrent: json['is_current'] as bool? ?? false,
    );
  }

  final String id;
  final String? deviceId;
  final DateTime createdAt;
  final DateTime lastSeenAt;
  final bool isCurrent;
}

/// What the shop wants to be interrupted about (master spec section 95).
class NotificationPreferences {
  const NotificationPreferences({
    required this.pushEnabled,
    required this.smsEnabled,
    required this.routineTrackingPush,
    required this.mutedKinds,
    required this.pushTransportAvailable,
    required this.smsTransportAvailable,
    this.quietHoursStart,
    this.quietHoursEnd,
  });

  factory NotificationPreferences.fromJson(Map<String, dynamic> json) {
    return NotificationPreferences(
      pushEnabled: json['push_enabled'] as bool? ?? true,
      smsEnabled: json['sms_enabled'] as bool? ?? false,
      routineTrackingPush: json['routine_tracking_push'] as bool? ?? false,
      mutedKinds: <String>[
        for (final kind
            in (json['muted_kinds'] as List<dynamic>? ?? const <dynamic>[]))
          kind as String,
      ],
      pushTransportAvailable:
          json['push_transport_available'] as bool? ?? false,
      smsTransportAvailable: json['sms_transport_available'] as bool? ?? false,
      quietHoursStart: json['quiet_hours_start'] as int?,
      quietHoursEnd: json['quiet_hours_end'] as int?,
    );
  }

  final bool pushEnabled;
  final bool smsEnabled;
  final bool routineTrackingPush;
  final List<String> mutedKinds;

  /// Whether a transport exists in this deployment at all. The screen shows
  /// "not available yet" rather than a switch that changes nothing.
  final bool pushTransportAvailable;
  final bool smsTransportAvailable;

  final int? quietHoursStart;
  final int? quietHoursEnd;
}

/// What deletion would do, in the words the server actually implements.
class PrivacyStatus {
  const PrivacyStatus({
    required this.retentionNote,
    required this.graceDays,
    required this.deletionRequested,
    required this.anonymisedOnDeletion,
    required this.retainedAfterDeletion,
    this.scheduledFor,
  });

  factory PrivacyStatus.fromJson(Map<String, dynamic> json) {
    return PrivacyStatus(
      retentionNote: json['retention_note'] as String,
      graceDays: json['grace_days'] as int? ?? 0,
      deletionRequested: json['deletion_requested'] as bool? ?? false,
      scheduledFor: json['scheduled_for'] == null
          ? null
          : DateTime.parse(json['scheduled_for'] as String).toLocal(),
      anonymisedOnDeletion: <String>[
        for (final item
            in (json['anonymised_on_deletion'] as List<dynamic>? ??
                const <dynamic>[]))
          item as String,
      ],
      retainedAfterDeletion: <String>[
        for (final item
            in (json['retained_after_deletion'] as List<dynamic>? ??
                const <dynamic>[]))
          item as String,
      ],
    );
  }

  final String retentionNote;
  final int graceDays;
  final bool deletionRequested;
  final DateTime? scheduledFor;
  final List<String> anonymisedOnDeletion;
  final List<String> retainedAfterDeletion;
}

class DeletionSchedule {
  const DeletionSchedule({
    required this.status,
    required this.scheduledFor,
    required this.graceDays,
    required this.retentionNote,
  });

  factory DeletionSchedule.fromJson(Map<String, dynamic> json) {
    return DeletionSchedule(
      status: json['status'] as String,
      scheduledFor: DateTime.parse(json['scheduled_for'] as String).toLocal(),
      graceDays: json['grace_days'] as int? ?? 0,
      retentionNote: json['retention_note'] as String,
    );
  }

  final String status;
  final DateTime scheduledFor;
  final int graceDays;
  final String retentionNote;

  bool get isScheduled => status == 'SCHEDULED';
}

/// One export the seller has requested.
class ExportJob {
  const ExportJob({
    required this.id,
    required this.kind,
    required this.status,
    required this.rowCount,
    required this.byteSize,
    required this.createdAt,
    this.filename,
    this.expiresAt,
    this.downloadedAt,
    this.downloadToken,
  });

  factory ExportJob.fromJson(Map<String, dynamic> json) {
    DateTime? parse(String key) => json[key] == null
        ? null
        : DateTime.parse(json[key] as String).toLocal();

    return ExportJob(
      id: json['id'] as String,
      kind: json['kind'] as String,
      status: json['status'] as String,
      rowCount: json['row_count'] as int? ?? 0,
      byteSize: json['byte_size'] as int? ?? 0,
      filename: json['filename'] as String?,
      createdAt: DateTime.parse(json['created_at'] as String).toLocal(),
      expiresAt: parse('expires_at'),
      downloadedAt: parse('downloaded_at'),
      downloadToken: json['download_token'] as String?,
    );
  }

  final String id;
  final String kind;
  final String status;
  final int rowCount;
  final int byteSize;
  final String? filename;
  final DateTime createdAt;
  final DateTime? expiresAt;
  final DateTime? downloadedAt;

  /// Present only on the response that created the export. It is a bearer
  /// credential and the server keeps only a hash, so it cannot be shown again.
  final String? downloadToken;

  bool get isReady => status == 'READY';
  bool get hasExpired =>
      status == 'EXPIRED' ||
      (expiresAt != null && expiresAt!.isBefore(DateTime.now()));

  String get label => switch (kind) {
    'ORDERS' => 'Orders',
    'CUSTOMERS' => 'Customers',
    'PRODUCTS' => 'Products',
    'PAYOUTS' => 'Payouts',
    'RECONCILIATION' => 'COD reconciliation',
    'PROFIT_SUMMARY' => 'Profit summary',
    _ => kind,
  };
}
