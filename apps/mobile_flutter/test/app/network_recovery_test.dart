import 'package:ecomsbd/app/network_recovery.dart';
import 'package:ecomsbd/core/api/api_error.dart';
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
}
