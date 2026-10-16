import 'package:ecomsbd/app/network_recovery.dart';
import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_error.dart';
import 'package:ecomsbd/data/commerce/commerce_providers.dart';
import 'package:ecomsbd/data/commerce/models.dart';
import 'package:ecomsbd/data/commerce/paged_list_controller.dart';
import 'package:ecomsbd/data/commerce/repository_support.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// A list whose filter lives in its controller, as the real ones do.
class _Numbers extends PagedListController<int> {
  _Numbers(this._fetch) {
    refresh();
  }

  final Future<List<int>> Function(String filter) _fetch;
  String filter = 'all';

  @override
  Future<Sourced<PagedResult<int>>> fetchPage({String? cursor}) async {
    final items = await _fetch(filter);
    return Sourced<PagedResult<int>>.live(
      PagedResult<int>(items: items),
      DateTime.now().toUtc(),
    );
  }
}

void main() {
  late NetworkRecoveryObserver observer;
  late ProviderContainer container;

  setUp(() {
    observer = NetworkRecoveryObserver();
    container = ProviderContainer(observers: <ProviderObserver>[observer]);
  });
  tearDown(() => container.dispose());

  test('what failed offline is fetched again, once, on reconnect', () async {
    var online = false;
    var calls = 0;
    final provider = FutureProvider<int>((ref) async {
      calls++;
      if (!online) throw ApiError.offline();
      return 7;
    });
    container.listen(provider, (_, _) {});
    await expectLater(
      container.read(provider.future),
      throwsA(isA<ApiError>()),
    );
    expect(observer.waitingCount, 1);

    online = true;
    observer.retry(container);

    expect(await container.read(provider.future), 7);
    expect(calls, 2);
    expect(observer.waitingCount, 0);

    // Nothing is left waiting, so a second reconnect sends nothing.
    observer.retry(container);
    await pumpEventQueue();
    expect(calls, 2);
  });

  test('cached data served offline is refreshed on reconnect', () async {
    var calls = 0;
    final provider = FutureProvider<Sourced<int>>((ref) async {
      calls++;
      return calls == 1
          ? Sourced<int>(
              value: 1,
              origin: DataOrigin.cache,
              fetchedAt: DateTime.utc(2026),
              error: ApiError.offline(),
            )
          : Sourced<int>.live(2, DateTime.now().toUtc());
    });
    container.listen(provider, (_, _) {});
    expect((await container.read(provider.future)).isStale, isTrue);

    observer.retry(container);

    final fresh = await container.read(provider.future);
    expect(fresh.value, 2);
    expect(fresh.isStale, isFalse);
  });

  test('a server failure is not retried just because the network returned', () {
    final provider = FutureProvider<int>((ref) async {
      throw const ApiError(
        code: ApiErrorCode.internal,
        messageBn: '',
        messageEn: 'boom',
        retryable: true,
      );
    });
    container.listen(provider, (_, _) {});

    return expectLater(
      container.read(provider.future),
      throwsA(isA<ApiError>()),
    ).then((_) => expect(observer.waitingCount, 0));
  });

  test('a list reloads in place and keeps its filter', () async {
    var online = false;
    final filters = <String>[];
    final provider = StateNotifierProvider<_Numbers, PagedListState<int>>(
      (ref) => _Numbers((filter) async {
        filters.add(filter);
        if (!online) throw ApiError.offline();
        return <int>[1, 2, 3];
      }),
    );
    container.listen(provider, (_, _) {});
    container.read(provider.notifier).filter = 'unpaid';
    await pumpEventQueue();
    expect(container.read(provider).error!.isOffline, isTrue);
    expect(observer.waitingCount, 1);
    final controller = container.read(provider.notifier);

    online = true;
    observer.retry(container);
    await pumpEventQueue();

    expect(container.read(provider.notifier), same(controller));
    expect(container.read(provider).items, <int>[1, 2, 3]);
    expect(filters.last, 'unpaid');
  });

  test('a provider that has gone away is forgotten', () async {
    final provider = FutureProvider.autoDispose<int>((ref) async {
      throw ApiError.offline();
    });
    final subscription = container.listen(provider, (_, _) {});
    await expectLater(
      container.read(provider.future),
      throwsA(isA<ApiError>()),
    );
    expect(observer.waitingCount, 1);

    subscription.close();
    await pumpEventQueue();

    expect(observer.waitingCount, 0);
  });

  group('back in the foreground', () {
    // The web or another phone may have changed the shop while the seller was
    // away; every read of shop data is fetched again, and nothing else is.
    late ProviderContainer shop;
    late Map<String, int> calls;
    late FutureProvider<Sourced<int>> summary;
    late FutureProviderFamily<int, String> order;
    late FutureProvider<int> quote;
    late StateNotifierProvider<_Numbers, PagedListState<int>> orders;

    ProviderContainer shopContainer({
      String? tenant = 'shop-1',
      bool offline = false,
    }) {
      final container = ProviderContainer(
        observers: <ProviderObserver>[observer],
        overrides: <Override>[
          tenantIdProvider.overrideWithValue(tenant),
          isOfflineProvider.overrideWithValue(offline),
          pendingMutationCountProvider.overrideWith(
            (ref) => Stream<int>.value(0),
          ),
        ],
      );
      container.listen(summary, (_, _) {});
      container.listen(order('o1'), (_, _) {});
      container.listen(quote, (_, _) {});
      container.listen(orders, (_, _) {});
      return container;
    }

    int count(String name) => calls[name] ?? 0;

    setUp(() {
      resetForegroundRefresh();
      calls = <String, int>{};
      void hit(String name) => calls[name] = count(name) + 1;
      summary = FutureProvider<Sourced<int>>((ref) async {
        hit('summary');
        return Sourced<int>.live(1, DateTime.now().toUtc());
      });
      order = FutureProvider.family<int, String>((ref, id) async {
        hit('order');
        return 1;
      });
      // A metered read, such as a courier quote: never repeated on its own.
      quote = FutureProvider<int>((ref) async {
        hit('quote');
        return 1;
      });
      orders = StateNotifierProvider<_Numbers, PagedListState<int>>(
        (ref) => _Numbers((_) async {
          hit('orders');
          return <int>[1];
        }),
      );
      observer = NetworkRecoveryObserver(shopReads: <Object>{order});
    });

    tearDown(() => shop.dispose());

    Future<void> settled() async {
      await shop.read(summary.future);
      await shop.read(order('o1').future);
      await shop.read(quote.future);
      await pumpEventQueue();
    }

    test('refetches cached reads, lists and listed details only', () async {
      shop = shopContainer();
      await settled();
      expect(observer.liveCount, 3);

      await onForeground(shop, observer, now: DateTime.utc(2026, 10, 17));
      await settled();

      expect(count('summary'), 2);
      expect(count('order'), 2);
      expect(count('orders'), 2);
      expect(count('quote'), 1);
    });

    test('a quick round trip refreshes once', () async {
      shop = shopContainer();
      await settled();
      final first = DateTime.utc(2026, 10, 17, 9);

      await onForeground(shop, observer, now: first);
      await settled();
      await onForeground(
        shop,
        observer,
        now: first.add(minForegroundRefreshGap - const Duration(seconds: 1)),
      );
      await settled();
      expect(count('summary'), 2);

      await onForeground(
        shop,
        observer,
        now: first.add(minForegroundRefreshGap),
      );
      await settled();
      expect(count('summary'), 3);
    });

    test('nothing is fetched signed out, without a shop, or offline', () async {
      for (final (tenant, offline) in <(String?, bool)>[
        (null, false),
        ('shop-1', true),
      ]) {
        shop = shopContainer(tenant: tenant, offline: offline);
        await settled();
        calls.clear();

        await onForeground(shop, observer, now: DateTime.utc(2026, 10, 17));
        await settled();

        expect(calls, isEmpty);
        if (tenant == null) shop.dispose();
      }
    });
  });
}
