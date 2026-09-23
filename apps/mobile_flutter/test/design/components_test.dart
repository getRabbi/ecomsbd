import 'package:ecomsbd/core/money.dart';
import 'package:ecomsbd/design/components/badges.dart';
import 'package:ecomsbd/design/components/cards.dart';
import 'package:ecomsbd/design/components/navigation.dart';
import 'package:ecomsbd/design/components/order_card.dart';
import 'package:ecomsbd/design/components/states.dart';
import 'package:ecomsbd/design/components/surfaces.dart';
import 'package:ecomsbd/design/glass.dart';
import 'package:ecomsbd/design/tokens.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../helpers.dart';

void main() {
  group('StatusChip', () {
    testWidgets('renders an icon alongside the label', (tester) async {
      // Master spec section 122: state is never colour alone.
      await pumpAtSize(
        tester,
        const Scaffold(
          body: Center(
            child: StatusChip(label: 'Delivered', tone: Tone.good),
          ),
        ),
      );
      expect(find.text('Delivered'), findsOneWidget);
      expect(find.byType(Icon), findsOneWidget);
    });

    testWidgets('is announced to screen readers', (tester) async {
      final handle = tester.ensureSemantics();
      await pumpAtSize(
        tester,
        const Scaffold(
          body: Center(
            child: StatusChip(label: 'Overdue', tone: Tone.bad),
          ),
        ),
      );
      // Icon-only status would be unreadable; the chip carries a label.
      expect(find.bySemanticsLabel('Overdue'), findsWidgets);
      handle.dispose();
    });
  });

  group('RiskBadge', () {
    testWidgets('never labels a person', (tester) async {
      // Master spec section 130 forbids "fraud"/"scammer"/"blacklist" wording;
      // the badge describes the order's risk, not the customer.
      for (final level in RiskLevel.values) {
        await pumpAtSize(
          tester,
          Scaffold(
            body: Center(child: RiskBadge(level: level)),
          ),
        );
        final text = tester
            .widgetList<Text>(find.byType(Text))
            .map((t) => t.data ?? '')
            .join(' ')
            .toLowerCase();
        expect(text.contains('fraud'), isFalse);
        expect(text.contains('scam'), isFalse);
        expect(text.contains('blacklist'), isFalse);
      }
    });
  });

  group('DataQualityBadge', () {
    testWidgets('marks an estimate as an estimate', (tester) async {
      // Master spec section 135: an estimated figure is never shown as exact.
      await pumpAtSize(
        tester,
        const Scaffold(
          body: Center(
            child: DataQualityBadge(
              quality: DataQuality.estimated,
              detail: 'ad cost missing',
            ),
          ),
        ),
      );
      expect(find.textContaining('Estimated'), findsOneWidget);
      expect(find.textContaining('ad cost missing'), findsOneWidget);
    });
  });

  group('MoneyText', () {
    testWidgets('prefixes an estimate with a tilde', (tester) async {
      await pumpAtSize(
        tester,
        const Scaffold(
          body: Center(child: MoneyText(Money(48500), estimated: true)),
        ),
      );
      expect(find.text('~৳485'), findsOneWidget);
    });

    testWidgets('colours a loss red when asked', (tester) async {
      await pumpAtSize(
        tester,
        const Scaffold(
          body: Center(child: MoneyText(Money(-8000), colorBySign: true)),
        ),
      );
      final text = tester.widget<Text>(find.text('-৳80'));
      expect(text.style?.color, EcomsbdColors.red);
    });
  });

  group('HeroMoneyCard', () {
    testWidgets('renders the amount and its KPIs at 360dp', (tester) async {
      await pumpAtSize(
        tester,
        const Scaffold(
          body: SingleChildScrollView(
            child: Padding(
              padding: EdgeInsets.all(EcomsbdSpacing.page),
              child: HeroMoneyCard(
                eyebrow: 'COD outstanding',
                amount: Money(8745000),
                subtitle: '61 eligible parcels',
                trailing: StatusChip(label: '৳8,750 overdue', tone: Tone.bad),
                kpis: <HeroKpi>[
                  HeroKpi(label: 'Expected today', value: '৳21,300'),
                  HeroKpi(label: 'Settled', value: '৳2.84L'),
                  HeroKpi(label: 'Mismatch', value: '-৳4,240', tone: Tone.bad),
                ],
              ),
            ),
          ),
        ),
      );
      expect(find.text('৳87,450'), findsOneWidget);
      expect(find.text('Expected today'.toUpperCase()), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });

  group('OrderCard', () {
    testWidgets('shows courier state and money state separately', (
      tester,
    ) async {
      // Master spec section 1.4: a parcel can be DELIVERED while its COD is
      // unpaid. Collapsing the two hides money from the seller.
      await pumpAtSize(
        tester,
        const Scaffold(
          body: Padding(
            padding: EdgeInsets.all(EcomsbdSpacing.page),
            child: OrderCard(
              reference: 'CP-20260909-0041',
              customerName: 'Rafi Hasan',
              status: StatusChip(label: 'Delivered', tone: Tone.good),
              facts: <OrderFact>[
                OrderFact(label: 'COD', value: '৳1,850'),
                OrderFact(label: 'Courier', value: 'Steadfast'),
                OrderFact(label: 'Money', value: 'Pending', tone: Tone.bad),
                OrderFact(label: 'Profit', value: '৳638'),
              ],
              maskedPhone: '01819****22',
            ),
          ),
        ),
      );
      expect(find.text('Delivered'), findsOneWidget);
      expect(find.text('Pending'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('never renders a full phone number', (tester) async {
      await pumpAtSize(
        tester,
        const Scaffold(
          body: Padding(
            padding: EdgeInsets.all(EcomsbdSpacing.page),
            child: OrderCard(
              reference: 'CP-1',
              customerName: 'Test',
              status: StatusChip(label: 'Booked'),
              facts: <OrderFact>[],
              maskedPhone: '01819****22',
            ),
          ),
        ),
      );
      final allText = tester
          .widgetList<Text>(find.byType(Text))
          .map((t) => t.data ?? '')
          .join(' ');
      expect(allText.contains('****'), isTrue);
      expect(RegExp(r'01\d{9}').hasMatch(allText), isFalse);
    });
  });

  group('Timeline', () {
    testWidgets('keeps courier and financial steps as separate entries', (
      tester,
    ) async {
      await pumpAtSize(
        tester,
        const Scaffold(
          body: Padding(
            padding: EdgeInsets.all(EcomsbdSpacing.page),
            child: Timeline(
              entries: <TimelineEntry>[
                TimelineEntry(
                  title: 'Courier booked',
                  detail: 'Steadfast · charge snapshot ৳80',
                  state: TimelineState.done,
                ),
                TimelineEntry(
                  title: 'In transit',
                  detail: 'Last provider update 10:11 AM',
                  state: TimelineState.current,
                ),
                TimelineEntry(
                  title: 'COD receivable',
                  detail: 'Eligible only after a verified delivery outcome.',
                  state: TimelineState.pending,
                ),
              ],
            ),
          ),
        ),
      );
      expect(find.text('Courier booked'), findsOneWidget);
      expect(find.text('COD receivable'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });

  group('states', () {
    testWidgets('offline banner says what still works', (tester) async {
      await pumpAtSize(
        tester,
        const Scaffold(
          body: Padding(
            padding: EdgeInsets.all(EcomsbdSpacing.page),
            child: OfflineBanner(pendingCount: 3),
          ),
        ),
      );
      expect(find.text('Offline'), findsOneWidget);
      // Master spec section 62.17: an offline queue must never imply a courier
      // action succeeded, so the banner names the limit explicitly.
      expect(
        find.textContaining('Courier booking needs a connection'),
        findsOneWidget,
      );
      expect(find.textContaining('3 changes'), findsOneWidget);
    });

    testWidgets('empty state offers the next action', (tester) async {
      var tapped = false;
      await pumpAtSize(
        tester,
        Scaffold(
          body: EmptyState(
            icon: Icons.receipt_long_outlined,
            title: 'No orders yet',
            message: 'Paste a Messenger order or add one by hand.',
            actionLabel: 'New order',
            onAction: () => tapped = true,
          ),
        ),
      );
      await tester.tap(find.text('New order'));
      expect(tapped, isTrue);
    });

    testWidgets('skeleton renders without a network call', (tester) async {
      await pumpAtSize(tester, const Scaffold(body: DashboardSkeleton()));
      expect(find.byType(SkeletonLoader), findsWidgets);
    });
  });

  group('BottomGlassNavigation', () {
    testWidgets('has exactly the four locked destinations', (tester) async {
      await pumpAtSize(
        tester,
        Scaffold(
          body: Align(
            alignment: Alignment.bottomCenter,
            child: BottomGlassNavigation(
              current: MainDestination.home,
              onSelected: (_) {},
            ),
          ),
        ),
      );
      expect(find.text('Home'), findsOneWidget);
      expect(find.text('Orders'), findsOneWidget);
      expect(find.text('Money'), findsOneWidget);
      expect(find.text('Insights'), findsOneWidget);
      // The menu opens from the top bar's hamburger, not from a tab.
      expect(find.text('Menu'), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('reports the selected destination', (tester) async {
      MainDestination? selected;
      await pumpAtSize(
        tester,
        Scaffold(
          body: Align(
            alignment: Alignment.bottomCenter,
            child: BottomGlassNavigation(
              current: MainDestination.home,
              onSelected: (value) => selected = value,
            ),
          ),
        ),
      );
      await tester.tap(find.text('Money'));
      expect(selected, MainDestination.money);
    });
  });

  group('GlassSurface', () {
    testWidgets('uses a BackdropFilter in full effects mode', (tester) async {
      // The chrome that keeps a live blur — top bar, bottom nav, sheets — uses
      // GlassSurface at its default sigma.
      await tester.pumpWidget(
        wrapForTest(
          const Scaffold(body: GlassSurface(child: Text('x'))),
          effects: EffectsMode.full,
        ),
      );
      expect(find.byType(BackdropFilter), findsOneWidget);
    });

    testWidgets('list cards opt out of the live blur even in full mode', (
      tester,
    ) async {
      // A card repeats down a scrolling list, where a BackdropFilter per card
      // is a saveLayer and a re-blur per card per frame. It takes the
      // thickened fill instead, whatever the device tier.
      await tester.pumpWidget(
        wrapForTest(
          const Scaffold(body: GlassCard(child: Text('x'))),
          effects: EffectsMode.full,
        ),
      );
      expect(find.byType(BackdropFilter), findsNothing);
      expect(find.text('x'), findsOneWidget);
    });

    testWidgets('drops the blur on a low-end device', (tester) async {
      // Master spec section 53: the appearance is preserved with an opaque
      // fallback rather than paying for a live blur per frame.
      await tester.pumpWidget(
        wrapForTest(
          const Scaffold(body: GlassSurface(child: Text('x'))),
          effects: EffectsMode.reduced,
        ),
      );
      expect(find.byType(BackdropFilter), findsNothing);
      expect(find.text('x'), findsOneWidget);
    });
  });
}
