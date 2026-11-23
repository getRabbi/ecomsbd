import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../account/account_repository.dart';
import '../account/models.dart';
import '../commerce/commerce_providers.dart';
import 'billing_repository.dart';
import 'manual_payment.dart';
import 'models.dart';

/// Wiring for billing, account and privacy.
///
/// None of these are cached on the device. An entitlement read from a cache is
/// the local `paid = true` master spec section 27.3 forbids, and a stale device
/// list is a security answer that is wrong.

final billingRepositoryProvider = Provider<BillingRepository>((ref) {
  return BillingRepository(ref.watch(apiClientProvider));
});

final accountRepositoryProvider = Provider<AccountRepository>((ref) {
  return AccountRepository(ref.watch(apiClientProvider));
});

/// Everything the plans screen needs.
final billingOverviewProvider = FutureProvider.autoDispose<BillingOverview>((
  ref,
) {
  ref.watch(entitlementsProvider);
  return ref.watch(billingRepositoryProvider).overview();
});

/// The shop's plan and entitlements, on their own.
///
/// Used by screens that gate a control on a plan. They ask the server; they do
/// not consult a flag the app is holding.
final entitlementsProvider = FutureProvider.autoDispose<Entitlements>((ref) {
  ref.watch(tenantIdProvider);
  final refresh = Timer(const Duration(minutes: 1), ref.invalidateSelf);
  ref.onDispose(refresh.cancel);
  return ref.watch(billingRepositoryProvider).entitlements();
});

/// Never flash purchase navigation while the authoritative state is unknown.
final showPlanUiProvider = Provider<bool>((ref) {
  return ref.watch(entitlementsProvider).valueOrNull?.freeLaunchMode == false;
});

/// Metered usage, refreshed on demand. The client never counts.
final usageProvider = FutureProvider<List<UsageLine>>((ref) {
  return ref.watch(billingRepositoryProvider).usage();
});

final billingHistoryProvider = FutureProvider<List<BillingEvent>>((ref) {
  return ref.watch(billingRepositoryProvider).history();
});

/// V4 WP2.5: whether bKash / Nagad TrxID payment is offered, and where to send.
/// Only the owner may ask (`billing.manage`); anyone else gets a 403, which
/// the plans screen treats as "not offered".
final manualPaymentOptionsProvider =
    FutureProvider.autoDispose<ManualPaymentOptions>((ref) {
      ref.watch(entitlementsProvider);
      return ref.watch(billingRepositoryProvider).manualPaymentOptions();
    });

final manualPaymentsProvider =
    FutureProvider.autoDispose<List<ManualPaymentSubmission>>((ref) {
      return ref.watch(billingRepositoryProvider).manualPayments();
    });

// --------------------------------------------------------------------------- //
// Account
// --------------------------------------------------------------------------- //

final devicesProvider = FutureProvider<List<DeviceInfo>>((ref) {
  return ref.watch(accountRepositoryProvider).devices();
});

final notificationPreferencesProvider = FutureProvider<NotificationPreferences>(
  (ref) => ref.watch(accountRepositoryProvider).notificationPreferences(),
);

final privacyStatusProvider = FutureProvider<PrivacyStatus>((ref) {
  return ref.watch(accountRepositoryProvider).privacy();
});

final exportsProvider = FutureProvider<List<ExportJob>>((ref) {
  return ref.watch(accountRepositoryProvider).exports();
});
