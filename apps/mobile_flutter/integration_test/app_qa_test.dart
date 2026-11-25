// Device QA (launch run 13): drives the real app against a real API with a
// test shop. Not part of `flutter test` (it needs a device, an account and
// the host helper), so CI only analyzes it.
//
// Run (phone on USB debugging and online), from apps/mobile_flutter:
//
//   flutter build apk --debug --target=integration_test/app_qa_test.dart //     --dart-define-from-file=env/production.json //     --dart-define-from-file=<git-ignored JSON with QA_EMAIL / QA_PASSWORD>
//   python tool/device_qa_helper.py <package>        # second terminal
//   flutter drive --driver=test_driver/integration_test.dart //     --target=integration_test/app_qa_test.dart -d <device> //     --use-application-binary=build/app/outputs/flutter-apk/app-debug.apk
//
// `--use-application-binary` makes flutter read the package id from the APK
// (without it, one machine launched a stale id and waited forever). A debug
// build cannot replace a Play-installed app (different key): use a phone
// without it, or give the debug build a local `applicationIdSuffix` (plus a
// matching google-services.json client) and pass that id to the helper —
// never committed. Xiaomi phones ask "Install via USB" on every install:
// someone must tap Install (tick "Remember my choice").
//
// Some steps leave the app: the Android share sheet hands a chat message in,
// the system print preview shows the invoice and label, and the browser opens
// the legal pages. The test prints `QA_EVENT <what>` to logcat and the helper
// answers (sends the share intent, screenshots the system screen, presses
// Back). Every step must pass; the step list is in the driver's
// qa-output/mobile/report.json.
//
// Never use a real customer: the shared message names the shop's own test
// customer (LAUNCH TEST, the operator's own number).

import 'dart:async';

import 'package:ecomsbd/main.dart' as app;
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

const _email = String.fromEnvironment('QA_EMAIL');
const _password = String.fromEnvironment('QA_PASSWORD');

void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('seller walk: sign in → order → call queue → print → settings', (
    tester,
  ) async {
    if (_email.isEmpty || _password.isEmpty) {
      markTestSkipped('QA_EMAIL / QA_PASSWORD not supplied');
      return;
    }

    final steps = <Map<String, Object?>>[];
    var shot = 0;

    Future<void> wait(int ms) async {
      await Future<void>.delayed(Duration(milliseconds: ms));
      await tester.pump();
    }

    Future<bool> until(Finder finder, {int seconds = 30}) async {
      final end = DateTime.now().add(Duration(seconds: seconds));
      while (DateTime.now().isBefore(end)) {
        await wait(250);
        if (finder.evaluate().isNotEmpty) return true;
      }
      return false;
    }

    Future<void> expectSeen(Finder finder, String what, {int seconds = 30}) async {
      if (!await until(finder, seconds: seconds)) {
        throw StateError('not seen within ${seconds}s: $what');
      }
    }

    Finder anyText(List<String> texts) => find.byWidgetPredicate(
      (widget) =>
          (widget is Text && texts.contains(widget.data)) ||
          (widget is Text &&
              widget.textSpan != null &&
              texts.contains(widget.textSpan!.toPlainText())),
    );

    Future<void> tapFirst(Finder finder, String what) async {
      await expectSeen(finder, what);
      await tester.ensureVisible(finder.first);
      await tester.pump();
      await tester.tap(finder.first, warnIfMissed: false);
      await wait(600);
    }

    Future<void> scrollTo(Finder finder, String what) async {
      final end = DateTime.now().add(const Duration(seconds: 20));
      while (finder.evaluate().isEmpty && DateTime.now().isBefore(end)) {
        final scrollable = find.byType(Scrollable);
        if (scrollable.evaluate().isEmpty) break;
        await tester.drag(scrollable.first, const Offset(0, -300));
        await wait(300);
      }
      await expectSeen(finder, what, seconds: 2);
      await tester.ensureVisible(finder.first);
      await wait(300);
    }

    Future<void> back() async {
      final nav = tester.state<NavigatorState>(find.byType(Navigator).last);
      await nav.maybePop();
      await wait(800);
    }

    Future<void> step(String name, Future<String?> Function() body) async {
      final watch = Stopwatch()..start();
      var result = 'PASS';
      String? note;
      try {
        note = await body();
      } catch (error) {
        result = 'FAIL';
        note = '$error'.split('\n').first;
      }
      shot += 1;
      try {
        await binding.takeScreenshot('m-${'$shot'.padLeft(2, '0')}-$name');
      } catch (error) {
        note = '${note ?? ''} (no screenshot: $error)';
      }
      final masked = (note ?? '').replaceAll(RegExp(r'01\d{9}'), '01•••••••••');
      steps.add(<String, Object?>{
        'step': name,
        'result': result,
        'note': masked,
        'ms': watch.elapsedMilliseconds,
      });
      // ignore: avoid_print
      print('QA_STEP $name $result $masked');
    }

    void hostEvent(String what) {
      // ignore: avoid_print
      print('QA_EVENT $what');
    }

    final ordersTab = anyText(<String>['Orders', 'অর্ডার']);
    final moreTab = anyText(<String>['More', 'আরও']);
    final launchTest = find.textContaining('LAUNCH TEST');

    // Back to the signed-in shell (bottom tabs visible) from wherever a step
    // stopped: a step that fails must not leave the next one under its sheet.
    Future<void> home() async {
      for (var i = 0; i < 6 && ordersTab.evaluate().isEmpty; i++) {
        await back();
      }
    }

    unawaited(app.main());
    await wait(3000);
    await binding.convertFlutterSurfaceToImage();

    await step('sign-in', () async {
      final email = find.byKey(const Key('auth-email'));
      final signedIn = ordersTab;
      final end = DateTime.now().add(const Duration(seconds: 60));
      while (DateTime.now().isBefore(end) &&
          email.evaluate().isEmpty &&
          signedIn.evaluate().isEmpty) {
        await wait(300);
      }
      if (signedIn.evaluate().isNotEmpty) return 'session restored';
      await tester.enterText(email, _email);
      await tester.enterText(find.byKey(const Key('auth-password')), _password);
      await tester.tap(find.byKey(const Key('auth-submit')));
      await expectSeen(signedIn, 'home after sign-in', seconds: 60);
      return 'signed in with email + password';
    });

    await step('orders-list', () async {
      await home();
      await tapFirst(ordersTab, 'Orders tab');
      await expectSeen(launchTest, 'a LAUNCH TEST order');
      return 'orders list shows the test shop orders';
    });

    await step('create-order-confirm-and-call', () async {
      hostEvent('share');
      final confirmCall = anyText(<String>[
        'Confirm & call customer',
        'কনফার্ম করতে কাস্টমারকে কল করুন',
      ]);
      await expectSeen(confirmCall, 'quick order form', seconds: 60);
      await tapFirst(confirmCall, 'Confirm & call customer');
      final done = find.byKey(const ValueKey('qo.ordered'));
      // The test customer has earlier LAUNCH TEST orders, so the app asks
      // whether this is a repeat (DuplicateWarningSheet): keep it.
      final keep = anyText(<String>['Keep this order', 'এই অর্ডারটি রাখুন']);
      final end = DateTime.now().add(const Duration(seconds: 30));
      while (DateTime.now().isBefore(end) &&
          done.evaluate().isEmpty &&
          keep.evaluate().isEmpty) {
        await wait(300);
      }
      var duplicateAsked = false;
      if (keep.evaluate().isNotEmpty) {
        duplicateAsked = true;
        await tapFirst(keep, 'Keep this order');
      }
      await expectSeen(done, 'order created', seconds: 45);
      final queued = find.byKey(const ValueKey('qo.queued')).evaluate().isNotEmpty;
      final close = find.byKey(const ValueKey('qo.close'));
      if (close.evaluate().isNotEmpty) {
        await tester.tap(close.first);
      } else {
        await back();
      }
      await wait(1500);
      await home();
      return 'shared chat → quick order → Confirm & call → order created '
          '(queued=$queued, duplicate warning shown=$duplicateAsked)';
    });

    await step('call-queue', () async {
      await home();
      await tapFirst(ordersTab, 'Orders tab');
      await tester.drag(find.byType(Scrollable).first, const Offset(0, 400));
      await wait(2500);
      final banner = find.byKey(const ValueKey('cq.banner'));
      await tapFirst(banner, 'call queue banner');
      await expectSeen(anyText(<String>['Call queue', 'কল তালিকা']), 'Call queue title');
      await expectSeen(launchTest, 'the test order in the queue');
      final calls = anyText(<String>['Call', 'কল']).evaluate().length;
      await back();
      return 'queue lists LAUNCH TEST ($calls Call buttons; none pressed)';
    });

    await step('print-preview', () async {
      await home();
      await tapFirst(ordersTab, 'Orders tab');
      await tapFirst(launchTest, 'newest LAUNCH TEST order');
      final printIcon = find.byIcon(Icons.print_outlined);
      await tapFirst(printIcon, 'print button');
      hostEvent('print invoice');
      await tapFirst(
        anyText(<String>['Print invoice', 'চালান প্রিন্ট করুন']),
        'Print invoice',
      );
      await wait(14000);
      if (find.byIcon(Icons.print_outlined).evaluate().isNotEmpty &&
          anyText(<String>['Print label', 'লেবেল প্রিন্ট করুন']).evaluate().isEmpty) {
        await tapFirst(find.byIcon(Icons.print_outlined), 'print button again');
      }
      hostEvent('print label');
      await tapFirst(
        anyText(<String>['Print label', 'লেবেল প্রিন্ট করুন']),
        'Print label',
      );
      await wait(14000);
      final failed = anyText(<String>[
        'Could not print. Try again.',
      ]).evaluate().isNotEmpty;
      if (failed) throw StateError('the app said printing failed');
      return 'invoice + 4×6 label sent to the system print preview (host screenshots)';
    });

    await step('settings-auto-order-detect', () async {
      await home();
      await tapFirst(moreTab, 'More tab');
      await tapFirst(anyText(<String>['Settings', 'সেটিংস']), 'Settings');
      final tile = find.byKey(const ValueKey('settings.autoDetect'));
      await scrollTo(tile, 'Auto order detect tile');
      await tester.tap(tile);
      await wait(1500);
      await expectSeen(anyText(<String>['Auto order detect', 'অটো অর্ডার শনাক্ত']), 'screen title');
      final disclosure = find.byKey(const ValueKey('intake.disclosure'));
      await scrollTo(disclosure, 'Notification access disclosure');
      await expectSeen(
        anyText(<String>['What this reads and sends', 'কী পড়ে, কী পাঠায়']),
        'disclosure title',
      );
      return 'Auto order detect opens; the disclosure card is shown';
    });

    await step('data-privacy-links', () async {
      await home();
      await tapFirst(moreTab, 'More tab');
      await tapFirst(anyText(<String>['Settings', 'সেটিংস']), 'Settings');
      final row = anyText(<String>['Your data and privacy', 'আপনার তথ্য ও প্রাইভেসি']);
      await scrollTo(row, 'Data & privacy row');
      await tester.tap(row.first);
      await wait(1500);
      hostEvent('browser privacy');
      await tapFirst(anyText(<String>['Privacy Policy', 'প্রাইভেসি পলিসি', 'গোপনীয়তা নীতি']), 'Privacy Policy');
      await wait(12000);
      hostEvent('browser deletion');
      final deletion = anyText(<String>[
        'Account deletion help or email request',
        'অ্যাকাউন্ট মুছে ফেলার সহায়তা বা ইমেইলে অনুরোধ',
      ]);
      await scrollTo(deletion, 'account deletion help');
      await tester.tap(deletion.first);
      await wait(12000);
      return 'privacy policy + account deletion opened in the browser (host checks the URL)';
    });

    // Merge: takeScreenshot keeps the screenshots in reportData too.
    binding.reportData = <String, Object?>{
      ...?binding.reportData,
      'steps': steps,
    };
    expect(
      steps.where((step) => step['result'] != 'PASS').map((step) => step['step']),
      isEmpty,
      reason: 'every step of the device walk must pass',
    );
  });
}
