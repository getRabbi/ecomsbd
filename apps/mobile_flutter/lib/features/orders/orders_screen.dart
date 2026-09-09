import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../demo/demo_dashboard.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/order_card.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../shared/demo_data_notice.dart';
import '../shared/responsive.dart';

/// The Orders tab.
///
/// Master spec section 3: a fast seller workflow, not an ERP table. Each row is
/// a card carrying the four facts that decide what to do next — COD, courier,
/// money state and profit — with courier state and money state shown
/// separately, never merged.
///
/// Phase A renders the layout from fixtures; `GET /v1/orders` ships in Phase B.
class OrdersScreen extends ConsumerStatefulWidget {
  const OrdersScreen({super.key, this.onNavigate});

  final ValueChanged<String>? onNavigate;

  @override
  ConsumerState<OrdersScreen> createState() => _OrdersScreenState();
}

class _OrdersScreenState extends ConsumerState<OrdersScreen> {
  int _selectedFilter = 0;

  @override
  Widget build(BuildContext context) {
    final pending = ref.watch(pendingMutationCountProvider).valueOrNull ?? 0;
    final isOffline = ref.watch(isOfflineProvider);
    final topInset = MediaQuery.viewPaddingOf(context).top;

    return ListView(
      padding: EdgeInsets.fromLTRB(
        EcomsbdSpacing.page,
        topInset + EcomsbdTouch.minTarget + EcomsbdSpacing.lg,
        EcomsbdSpacing.page,
        EcomsbdSpacing.bottomNavClearance,
      ),
      children: <Widget>[
        const PageHeader(
          eyebrow: 'Order → courier → COD → profit',
          title: 'Orders',
          description: 'Fast seller workflow, not an ERP table.',
        ),
        if (isOffline) ...<Widget>[
          OfflineBanner(pendingCount: pending),
          const SizedBox(height: EcomsbdSpacing.sm),
        ],
        const DemoDataNotice(
          detail:
              'Demo data — order list ships with GET /v1/orders in Phase B.',
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        const _OrderMetrics(),
        const SizedBox(height: EcomsbdSpacing.sm),
        const _SearchField(),
        const SizedBox(height: EcomsbdSpacing.sm),
        _FilterChips(
          selected: _selectedFilter,
          onSelected: (index) => setState(() => _selectedFilter = index),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        for (final order in DemoDashboard.orders)
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
            child: OrderCard(
              reference: order.reference,
              customerName: order.customerName,
              status: StatusChip(
                label: order.statusLabel,
                tone: order.statusTone,
              ),
              facts: order.facts,
              maskedPhone: order.maskedPhone,
              area: order.area,
              itemSummary: order.itemSummary,
            ),
          ),
      ],
    );
  }
}

class _OrderMetrics extends StatelessWidget {
  const _OrderMetrics();

  @override
  Widget build(BuildContext context) {
    return const ResponsiveGrid(
      minTileWidth: 150,
      maxColumns: 4,
      spacing: EcomsbdSpacing.xs,
      children: <Widget>[
        MetricTile(label: 'Today', value: '46', caption: '৳54.2k order value'),
        MetricTile(label: 'Delivered', value: '31', caption: 'today'),
        MetricTile(label: 'COD pending', value: '61', caption: '৳87.5k'),
        MetricTile(
          label: 'Returns',
          value: '5',
          caption: 'today',
          tone: Tone.bad,
        ),
      ],
    );
  }
}

class _SearchField extends StatelessWidget {
  const _SearchField();

  @override
  Widget build(BuildContext context) {
    return const GlassSurface(
      borderRadius: EcomsbdRadii.cardMedium,
      padding: EdgeInsets.symmetric(horizontal: EcomsbdSpacing.md),
      child: Row(
        children: <Widget>[
          Icon(Icons.search_rounded, size: 19, color: EcomsbdColors.muted),
          SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: TextField(
              // Master spec section 129: search covers phone, order number,
              // tracking code, customer, SKU and provider reference, with
              // Bangla numeral normalisation applied first.
              decoration: InputDecoration(
                hintText: 'Phone, order no, tracking, customer, SKU',
                border: InputBorder.none,
                enabledBorder: InputBorder.none,
                focusedBorder: InputBorder.none,
                filled: false,
                contentPadding: EdgeInsets.symmetric(vertical: 14),
              ),
              style: EcomsbdType.body,
            ),
          ),
        ],
      ),
    );
  }
}

class _FilterChips extends StatelessWidget {
  const _FilterChips({required this.selected, required this.onSelected});

  final int selected;
  final ValueChanged<int> onSelected;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: EcomsbdTouch.minTarget,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: DemoDashboard.orderFilters.length,
        separatorBuilder: (_, __) => const SizedBox(width: EcomsbdSpacing.xs),
        itemBuilder: (context, index) {
          final filter = DemoDashboard.orderFilters[index];
          final isActive = index == selected;
          return Center(
            child: Material(
              color: isActive
                  ? const Color(0xFF111821)
                  : const Color(0xADFFFFFF),
              borderRadius: EcomsbdRadii.round,
              child: InkWell(
                onTap: () => onSelected(index),
                borderRadius: EcomsbdRadii.round,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 9,
                  ),
                  decoration: BoxDecoration(
                    borderRadius: EcomsbdRadii.round,
                    border: Border.all(
                      color: isActive
                          ? const Color(0xFF111821)
                          : EcomsbdColors.whiteStroke,
                    ),
                  ),
                  child: Text(
                    '${filter.label} ${filter.count}',
                    style: EcomsbdType.chip.copyWith(
                      color: isActive ? Colors.white : EcomsbdColors.ink,
                    ),
                  ),
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}
