import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../l10n/app_locale.dart';
import '../commerce/commerce_providers.dart';
import '../commerce/models.dart' show PagedResult;
import '../commerce/paged_list_controller.dart';
import '../commerce/repository_support.dart';
import 'analytics_repository.dart';
import 'models.dart';
import 'rto_models.dart';

/// Wiring for Home, Insights, expenses and the notification centre.

final analyticsRepositoryProvider = Provider<AnalyticsRepository>((ref) {
  return AnalyticsRepository(
    api: ref.watch(apiClientProvider),
    db: ref.watch(databaseProvider),
    tenantId: ref.watch(tenantIdProvider),
  );
});

/// Master spec section 1.1's daily money control screen.
final homeMetricsProvider = FutureProvider<Sourced<HomeMetrics>>((ref) {
  return ref.watch(analyticsRepositoryProvider).home();
});

/// P&L over the default 30-day window.
final profitReportProvider = FutureProvider<Sourced<ProfitReport>>((ref) {
  return ref.watch(analyticsRepositoryProvider).profit();
});

/// Section 19's return economics.
final returnReportProvider = FutureProvider<Sourced<ReturnReport>>((ref) {
  return ref.watch(analyticsRepositoryProvider).returns();
});

/// Return / RTO intelligence (V2.2). Every figure is the server's.
final rtoSummaryProvider = FutureProvider<Sourced<RtoSummary>>((ref) {
  return ref.watch(analyticsRepositoryProvider).rtoSummary();
});

final rtoTrendProvider = FutureProvider<Sourced<List<RtoWeek>>>((ref) {
  return ref.watch(analyticsRepositoryProvider).rtoTrend();
});

final rtoCouriersProvider = FutureProvider<Sourced<CourierRtoReport>>((ref) {
  return ref.watch(analyticsRepositoryProvider).rtoCouriers();
});

final rtoAreasProvider = FutureProvider<Sourced<AreaRtoReport>>((ref) {
  return ref.watch(analyticsRepositoryProvider).rtoAreas();
});

final rtoPatternsProvider = FutureProvider<Sourced<List<CustomerPattern>>>((
  ref,
) {
  return ref.watch(analyticsRepositoryProvider).rtoPatterns();
});

final rtoCustomerProvider = FutureProvider.autoDispose
    .family<CustomerRtoHistory, String>((ref, customerId) {
      return ref.watch(analyticsRepositoryProvider).rtoCustomer(customerId);
    });

/// Contribution profit by product, best first.
final productProfitProvider = FutureProvider<Sourced<List<ProductLine>>>((ref) {
  return ref.watch(analyticsRepositoryProvider).products();
});

/// The Friday figures, computed on demand so the screen works midweek.
final weeklySummaryProvider = FutureProvider<Sourced<WeeklySummary>>((ref) {
  return ref.watch(analyticsRepositoryProvider).weeklySummary();
});

/// The notification badge.
///
/// Kept separate from the list so the shell can show a count without pulling
/// thirty rows it will not render.
final unreadNotificationCountProvider = FutureProvider<int>((ref) {
  return ref.watch(analyticsRepositoryProvider).unreadCount();
});

/// What one parcel cost.
final consignmentChargesProvider =
    FutureProvider.family<List<ConsignmentCharge>, String>((
      ref,
      consignmentId,
    ) {
      return ref.watch(analyticsRepositoryProvider).chargesFor(consignmentId);
    });

// --------------------------------------------------------------------------- //
// Lists
// --------------------------------------------------------------------------- //

class ExpenseListController extends PagedListController<Expense> {
  ExpenseListController(this._repository) {
    refresh();
  }

  final AnalyticsRepository _repository;

  ExpenseKind? _kind;

  ExpenseKind? get kind => _kind;

  @override
  Future<Sourced<PagedResult<Expense>>> fetchPage({String? cursor}) {
    return _repository.expenses(cursor: cursor, kind: _kind);
  }

  void setKind(ExpenseKind? value) {
    _kind = value;
    refresh();
  }

  Future<Expense> record({
    required ExpenseKind kind,
    required int amountPaisa,
    required DateTime periodStart,
    required DateTime periodEnd,
    required String description,
    String? productId,
    AllocationMethod method = AllocationMethod.equalPerDeliveredOrder,
  }) async {
    final expense = await _repository.recordExpense(
      kind: kind,
      amountPaisa: amountPaisa,
      periodStart: periodStart,
      periodEnd: periodEnd,
      description: description,
      productId: productId,
      method: method,
    );
    await refresh();
    return expense;
  }

  Future<AllocationResult> allocate(
    String expenseId, {
    AllocationMethod? method,
    String? reason,
  }) async {
    final result = await _repository.allocateExpense(
      expenseId,
      method: method,
      reason: reason,
    );
    await refresh();
    return result;
  }
}

final expenseListProvider =
    StateNotifierProvider<ExpenseListController, PagedListState<Expense>>(
      (ref) => ExpenseListController(ref.watch(analyticsRepositoryProvider)),
    );

class NotificationListController extends PagedListController<AppNotification> {
  NotificationListController(this._repository, {this.lang}) {
    refresh();
  }

  final AnalyticsRepository _repository;

  /// The app's language, so the server words each alert in it.
  final String? lang;

  bool _unreadOnly = false;
  String? _category;

  bool get unreadOnly => _unreadOnly;

  /// Null for every category.
  String? get category => _category;

  @override
  Future<Sourced<PagedResult<AppNotification>>> fetchPage({String? cursor}) {
    return _repository.notifications(
      cursor: cursor,
      unreadOnly: _unreadOnly,
      category: _category,
      lang: lang,
    );
  }

  void setUnreadOnly(bool value) {
    _unreadOnly = value;
    refresh();
  }

  void setCategory(String? value) {
    _category = value;
    refresh();
  }

  Future<void> markRead(String notificationId) async {
    await _repository.markRead(notificationId);
    await refresh();
  }

  Future<void> markAllRead() async {
    await _repository.markAllRead();
    await refresh();
  }
}

final notificationListProvider =
    StateNotifierProvider<
      NotificationListController,
      PagedListState<AppNotification>
    >(
      (ref) => NotificationListController(
        ref.watch(analyticsRepositoryProvider),
        lang: ref.watch(localeProvider).name,
      ),
    );
