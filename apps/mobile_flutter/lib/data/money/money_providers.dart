import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../commerce/commerce_providers.dart';
import '../commerce/models.dart' show PagedResult;
import '../commerce/paged_list_controller.dart';
import '../commerce/repository_support.dart';
import 'models.dart';
import 'money_repository.dart';

/// Wiring for the money core.

final moneyRepositoryProvider = Provider<MoneyRepository>((ref) {
  return MoneyRepository(
    api: ref.watch(apiClientProvider),
    db: ref.watch(databaseProvider),
    tenantId: ref.watch(tenantIdProvider),
  );
});

/// The Money screen's headline figures.
final moneySummaryProvider = FutureProvider<Sourced<MoneySummary>>((ref) {
  return ref.watch(moneyRepositoryProvider).summary();
});

/// Every money event behind one parcel.
final receivableLedgerProvider =
    FutureProvider.family<List<LedgerEntry>, String>((ref, receivableId) {
      return ref.watch(moneyRepositoryProvider).ledgerFor(receivableId);
    });

/// One payout with its lines and adjustments.
final payoutProvider = FutureProvider.family<Payout, String>((ref, payoutId) {
  return ref.watch(moneyRepositoryProvider).payout(payoutId);
});

// --------------------------------------------------------------------------- //
// Lists
// --------------------------------------------------------------------------- //

class ReceivableListController extends PagedListController<Receivable> {
  ReceivableListController(this._repository) {
    refresh();
  }

  final MoneyRepository _repository;

  bool _openOnly = true;
  String? _status;

  bool get openOnly => _openOnly;
  String? get status => _status;

  @override
  Future<Sourced<PagedResult<Receivable>>> fetchPage({String? cursor}) {
    return _repository.receivables(
      cursor: cursor,
      openOnly: _openOnly,
      status: _status,
    );
  }

  void setOpenOnly(bool value) {
    _openOnly = value;
    refresh();
  }

  void setStatus(String? value) {
    _status = value;
    _openOnly = value == null;
    refresh();
  }
}

final receivableListProvider =
    StateNotifierProvider<ReceivableListController, PagedListState<Receivable>>(
      (ref) => ReceivableListController(ref.watch(moneyRepositoryProvider)),
    );

class PayoutListController extends PagedListController<Payout> {
  PayoutListController(this._repository) {
    refresh();
  }

  final MoneyRepository _repository;

  @override
  Future<Sourced<PagedResult<Payout>>> fetchPage({String? cursor}) {
    return _repository.payouts(cursor: cursor);
  }
}

final payoutListProvider =
    StateNotifierProvider<PayoutListController, PagedListState<Payout>>(
      (ref) => PayoutListController(ref.watch(moneyRepositoryProvider)),
    );

class CaseListController extends PagedListController<ReconciliationCase> {
  CaseListController(this._repository) {
    refresh();
  }

  final MoneyRepository _repository;

  String? _status = 'OPEN';
  String? _kind;

  String? get status => _status;
  String? get kind => _kind;

  @override
  Future<Sourced<PagedResult<ReconciliationCase>>> fetchPage({String? cursor}) {
    return _repository.cases(cursor: cursor, status: _status, kind: _kind);
  }

  void setStatus(String? value) {
    _status = value;
    refresh();
  }

  void setKind(String? value) {
    _kind = value;
    refresh();
  }
}

final caseListProvider =
    StateNotifierProvider<
      CaseListController,
      PagedListState<ReconciliationCase>
    >((ref) => CaseListController(ref.watch(moneyRepositoryProvider)));

/// Expected, actual and the difference, for the reconciliation screen.
final reconciliationSummaryProvider =
    FutureProvider<Sourced<ReconciliationSummary>>((ref) {
      return ref.watch(moneyRepositoryProvider).reconciliationSummary();
    });

/// Parcels compared expected-against-actual. Filtered and paged on the
/// server; the device only ever holds the page it is showing.
class ReconciliationItemListController
    extends PagedListController<ReconciliationItem> {
  ReconciliationItemListController(this._repository) {
    refresh();
  }

  final MoneyRepository _repository;

  /// `discrepancies`, `unmatched`, or null for everything.
  String? _view = 'discrepancies';

  String? get view => _view;

  @override
  Future<Sourced<PagedResult<ReconciliationItem>>> fetchPage({String? cursor}) {
    return _repository.reconciliationItems(cursor: cursor, view: _view);
  }

  void setView(String? value) {
    _view = value;
    refresh();
  }
}

final reconciliationItemListProvider =
    StateNotifierProvider<
      ReconciliationItemListController,
      PagedListState<ReconciliationItem>
    >(
      (ref) =>
          ReconciliationItemListController(ref.watch(moneyRepositoryProvider)),
    );
