import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../shared/responsive.dart';
import 'customer_detail_screen.dart';
import '../../l10n/app_strings.dart';

/// The seller's own customer list.
///
/// Private to this shop. Nothing here is shared with any other seller, and the
/// wording never labels a person — a flag describes what the shop decided about
/// its own orders, not what kind of human the customer is (master spec
/// section 130).
///
/// Phone numbers are masked. The full number is one audited tap away on the
/// detail screen, with a reason (section 101).
class CustomersScreen extends ConsumerStatefulWidget {
  const CustomersScreen({super.key});

  @override
  ConsumerState<CustomersScreen> createState() => _CustomersScreenState();
}

class _CustomersScreenState extends ConsumerState<CustomersScreen> {
  final TextEditingController _search = TextEditingController();

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(customerListProvider);
    final controller = ref.read(customerListProvider.notifier);
    final isOffline = ref.watch(isOfflineProvider);

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      body: EcomsbdBackground(
        child: SafeArea(
          child: ContentWidthLimit(
            child: RefreshIndicator(
              edgeOffset: EcomsbdLayout.pushedRefreshOffset,
              onRefresh: controller.refresh,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(
                  EcomsbdSpacing.page,
                  EcomsbdLayout.pushedTopPadding,
                  EcomsbdSpacing.page,
                  EcomsbdSpacing.xxl,
                ),
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      IconButton(
                        onPressed: () => Navigator.of(context).maybePop(),
                        icon: const Icon(Icons.arrow_back_rounded),
                        tooltip: context.tr('common.back'),
                      ),
                      Expanded(
                        child: PageHeader(
                          eyebrow: context.tr('cust.eyebrow'),
                          title: context.tr('entity.customers'),
                          description: context.tr('cust.description'),
                        ),
                      ),
                    ],
                  ),
                  if (isOffline) ...<Widget>[
                    const OfflineBanner(),
                    const SizedBox(height: EcomsbdSpacing.sm),
                  ],
                  if (state.isStale) ...<Widget>[
                    StaleDataNotice(
                      fetchedAt: state.fetchedAt,
                      onRetry: controller.refresh,
                    ),
                    const SizedBox(height: EcomsbdSpacing.sm),
                  ],
                  CommerceSearchField(
                    controller: _search,
                    hint: context.tr('cust.searchHint'),
                    onChanged: controller.setSearch,
                  ),
                  const SizedBox(height: EcomsbdSpacing.sm),
                  Wrap(
                    spacing: EcomsbdSpacing.xs,
                    runSpacing: EcomsbdSpacing.xs,
                    children: <Widget>[
                      FilterToggle(
                        label: context.tr('cust.filterRepeat'),
                        selected: controller.repeatOnly,
                        onChanged: controller.setRepeatOnly,
                      ),
                      FilterToggle(
                        label: context.tr('common.starred'),
                        selected: controller.flag == 'STARRED',
                        onChanged: (selected) =>
                            controller.setFlag(selected ? 'STARRED' : null),
                      ),
                      FilterToggle(
                        label: context.tr('common.blocked'),
                        selected: controller.flag == 'BLOCKED',
                        onChanged: (selected) =>
                            controller.setFlag(selected ? 'BLOCKED' : null),
                      ),
                    ],
                  ),
                  const SizedBox(height: EcomsbdSpacing.sm),
                  PagedListBody<Customer>(
                    state: state,
                    onRetry: controller.refresh,
                    onLoadMore: controller.loadMore,
                    emptyIcon: Icons.people_outline,
                    emptyTitle: context.tr('cust.emptyTitle'),
                    emptyMessage: context.tr('cust.emptyBody'),
                    itemBuilder: (context, customer) => CustomerRow(
                      customer: customer,
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) =>
                              CustomerDetailScreen(customerId: customer.id),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// One customer row.
class CustomerRow extends StatelessWidget {
  const CustomerRow({required this.customer, super.key, this.onTap});

  final Customer customer;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      onTap: onTap,
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      child: Row(
        children: <Widget>[
          Container(
            width: 42,
            height: 42,
            alignment: Alignment.center,
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              color: EcomsbdColors.rowIconBackground,
            ),
            child: Icon(
              customer.isBlocked
                  ? Icons.block_rounded
                  : (customer.isStarred
                        ? Icons.star_rounded
                        : Icons.person_outline_rounded),
              size: 20,
              color: customer.isBlocked
                  ? EcomsbdColors.red
                  : (customer.isStarred
                        ? EcomsbdColors.amber
                        : EcomsbdColors.rowIconInk),
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Flexible(
                      child: Text(
                        customer.displayName,
                        style: EcomsbdType.bodyStrong,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    if (customer.isRepeatBuyer) ...<Widget>[
                      const SizedBox(width: EcomsbdSpacing.xs),
                      StatusChip(
                        label: context.tr('cust.repeatChip'),
                        tone: Tone.good,
                        icon: Icons.autorenew_rounded,
                      ),
                    ],
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  '${customer.phoneMasked} · '
                  '${context.trPlural('cust.orders', customer.orderCount)}',
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 2),
                Text(
                  // "No history yet" rather than 0%: a new customer has not
                  // failed anything.
                  customer.successRateLabel,
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted2,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.xs),
          // Only the amount sits on the right. Stacking the delivery rate here
          // too squeezed the name column to nothing on a 360dp screen.
          MoneyText(
            customer.realizedRevenue,
            style: EcomsbdType.bodyStrong,
            compact: true,
          ),
        ],
      ),
    );
  }
}
