import 'dart:async';

import 'package:ecomsbd/core/network/network_monitor.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const soon = Duration(milliseconds: 5);

  test('request outcomes set the state; nothing is probed before start', () {
    final monitor = NetworkMonitor();
    addTearDown(monitor.dispose);

    expect(monitor.isOffline, isFalse);
    monitor.reportUnreachable();
    expect(monitor.isOffline, isTrue);
    monitor.reportReachable();
    expect(monitor.isOffline, isFalse);
  });

  test('offline, it probes with backoff and recovers by itself', () async {
    final monitor = NetworkMonitor();
    addTearDown(monitor.dispose);
    final answers = <bool>[false, true];
    var probes = 0;
    monitor.start(
      probe: () async {
        probes++;
        return answers.removeAt(0);
      },
      retryDelay: (_) => soon,
      watchLifecycle: false,
    );
    final states = <bool>[];
    monitor.addListener(states.add, fireImmediately: false);

    monitor.reportUnreachable();
    await Future<void>.delayed(const Duration(milliseconds: 80));

    expect(probes, 2);
    expect(monitor.isOffline, isFalse);
    expect(states, <bool>[true, false]);

    // Back online, nothing keeps asking.
    await Future<void>.delayed(const Duration(milliseconds: 40));
    expect(probes, 2);
  });

  test(
    'a burst of failed requests schedules one probe, not one each',
    () async {
      final monitor = NetworkMonitor();
      addTearDown(monitor.dispose);
      var probes = 0;
      monitor.start(
        probe: () async {
          probes++;
          return true;
        },
        retryDelay: (_) => soon,
        watchLifecycle: false,
      );

      for (var i = 0; i < 10; i++) {
        monitor.reportUnreachable();
      }
      await Future<void>.delayed(const Duration(milliseconds: 60));

      expect(probes, 1);
      expect(monitor.isOffline, isFalse);
    },
  );

  test('no network at all is offline at once; a network returning is checked '
      'straight away', () async {
    final network = StreamController<bool>();
    final monitor = NetworkMonitor();
    addTearDown(() {
      monitor.dispose();
      unawaited(network.close());
    });
    var probes = 0;
    monitor.start(
      probe: () async {
        probes++;
        return true;
      },
      hasNetwork: network.stream,
      retryDelay: (_) => const Duration(hours: 1),
      watchLifecycle: false,
    );

    network.add(false);
    await pumpEventQueue();
    expect(monitor.isOffline, isTrue);
    expect(probes, 0, reason: 'nothing to ask without a network');

    network.add(true);
    await pumpEventQueue();
    expect(probes, 1);
    expect(monitor.isOffline, isFalse);
  });

  test('a network with no route to the server stays offline', () async {
    final network = StreamController<bool>();
    final monitor = NetworkMonitor();
    addTearDown(() {
      monitor.dispose();
      unawaited(network.close());
    });
    monitor.start(
      probe: () async => false,
      hasNetwork: network.stream,
      retryDelay: (_) => const Duration(hours: 1),
      watchLifecycle: false,
    );

    network.add(false);
    network.add(true);
    await pumpEventQueue();

    expect(monitor.isOffline, isTrue);
  });

  test('in the background it stops probing, and asks once on return', () async {
    final monitor = NetworkMonitor();
    addTearDown(monitor.dispose);
    var probes = 0;
    monitor.start(
      probe: () async {
        probes++;
        return true;
      },
      retryDelay: (_) => soon,
      watchLifecycle: false,
    );

    monitor.setForeground(false);
    monitor.reportUnreachable();
    await Future<void>.delayed(const Duration(milliseconds: 40));
    expect(probes, 0);

    monitor.setForeground(true);
    await pumpEventQueue();
    expect(probes, 1);
    expect(monitor.isOffline, isFalse);
  });
}
