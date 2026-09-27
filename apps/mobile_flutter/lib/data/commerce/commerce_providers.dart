import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../auth/auth_models.dart';
import '../sync/outbox.dart';
import '../sync/sync_engine.dart';
import 'customers_repository.dart';
import 'imports_repository.dart';
import 'orders_repository.dart';
import 'products_repository.dart';
import 'risk_repository.dart';

/// Wiring for the commerce core.
///
/// Every repository is built against the *current* tenant, so switching shops
/// rebuilds them and the mirrors they read are scoped to the new shop. A
/// repository that cached a tenant id at startup would keep serving the
/// previous shop's rows.
///
/// None of these providers can reach `lib/demo`. Demo fixtures exist for the
/// Phase A layout preview only; a production repository that silently fell back
/// to them would show a seller invented orders (master spec section 20).

/// The signed-in shop, or `null` before onboarding completes.
final tenantIdProvider = Provider<String?>((ref) {
  return ref.watch(authControllerProvider).tenantId;
});

/// The shop's own name, for the Home hero.
///
/// Null before onboarding completes, and null rather than a placeholder if the
/// profile has not loaded: a hero that says "Demo Shop" would be a lie on the
/// most prominent line of the app.
final shopNameProvider = Provider<String?>((ref) {
  final auth = ref.watch(authControllerProvider);
  final tenantId = auth.tenantId;
  if (tenantId == null) return null;
  for (final tenant in auth.profile?.tenants ?? const <TenantSummary>[]) {
    if (tenant.id == tenantId) return tenant.name;
  }
  return null;
});

final outboxWriterProvider = Provider<OutboxWriter>((ref) {
  return OutboxWriter(
    db: ref.watch(databaseProvider),
    uuid: ref.watch(uuidProvider),
  );
});

final productsRepositoryProvider = Provider<ProductsRepository>((ref) {
  return ProductsRepository(
    api: ref.watch(apiClientProvider),
    db: ref.watch(databaseProvider),
    outbox: ref.watch(outboxWriterProvider),
    tenantId: ref.watch(tenantIdProvider),
  );
});

final customersRepositoryProvider = Provider<CustomersRepository>((ref) {
  return CustomersRepository(
    api: ref.watch(apiClientProvider),
    db: ref.watch(databaseProvider),
    outbox: ref.watch(outboxWriterProvider),
    tenantId: ref.watch(tenantIdProvider),
  );
});

/// Delivery-risk lookups.
///
/// Network-only and unmemoised on purpose: each check is metered against the
/// shop's daily quota, so answering a repeat from memory would understate what
/// has been spent and blunt the limit that keeps this from being a number
/// lookup service.
final riskRepositoryProvider = Provider<RiskRepository>((ref) {
  return RiskRepository(api: ref.watch(apiClientProvider));
});

final ordersRepositoryProvider = Provider<OrdersRepository>((ref) {
  return OrdersRepository(
    api: ref.watch(apiClientProvider),
    db: ref.watch(databaseProvider),
    outbox: ref.watch(outboxWriterProvider),
    tenantId: ref.watch(tenantIdProvider),
  );
});

final importsRepositoryProvider = Provider<ImportsRepository>((ref) {
  return ImportsRepository(
    api: ref.watch(apiClientProvider),
    db: ref.watch(databaseProvider),
    tenantId: ref.watch(tenantIdProvider),
  );
});

final syncEngineProvider = Provider<SyncEngine>((ref) {
  return SyncEngine(
    api: ref.watch(apiClientProvider),
    db: ref.watch(databaseProvider),
    tenantId: ref.watch(tenantIdProvider),
  );
});

/// Orders the seller changed that the server has not accepted yet.
final unsyncedOrderCountProvider = StreamProvider<int>((ref) {
  final tenant = ref.watch(tenantIdProvider);
  if (tenant == null) {
    return Stream<int>.value(0);
  }
  return ref.watch(databaseProvider).watchUnsyncedOrderCount(tenant);
});

/// Runs a sync pass and reports what happened.
///
/// Exposed as a controller rather than fired on a timer: the seller pulls to
/// refresh, and the app syncs when a screen that needs fresh data opens. A
/// background poll on a metered connection is a cost the seller did not agree
/// to.
class SyncController extends StateNotifier<AsyncValue<SyncReport>> {
  SyncController(this._engine)
    : super(const AsyncValue<SyncReport>.data(SyncReport()));

  final SyncEngine _engine;

  Future<SyncReport> sync() async {
    state = const AsyncValue<SyncReport>.loading();
    try {
      // The offline banner needs no update here: every request the engine
      // makes reports its outcome to the network monitor.
      final report = await _engine.run();
      state = AsyncValue<SyncReport>.data(report);
      return report;
    } on Object catch (error, stack) {
      state = AsyncValue<SyncReport>.error(error, stack);
      rethrow;
    }
  }
}

final syncControllerProvider =
    StateNotifierProvider<SyncController, AsyncValue<SyncReport>>((ref) {
      return SyncController(ref.watch(syncEngineProvider));
    });
