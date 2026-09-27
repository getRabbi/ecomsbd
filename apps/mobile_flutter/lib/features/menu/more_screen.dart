import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../design/components/pills.dart';
import '../../design/components/seller_blocks.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../billing/plans_screen.dart';
import '../couriers/courier_compare_screen.dart';
import '../customers/customers_screen.dart';
import '../expenses/expenses_screen.dart';
import '../home/widgets/seller_today.dart';
import '../imports/imports_screen.dart';
import '../insights/insights_screen.dart';
import '../insights/rto_screen.dart';
import '../messaging/campaigns_screen.dart';
import '../money/cases_screen.dart';
import '../money/payouts_screen.dart';
import '../products/products_screen.dart';
import '../risk/risk_check_screen.dart';
import '../settings/connections_screen.dart';
import '../settings/courier_accounts_screen.dart';
import '../settings/integrations_screen.dart';
import '../settings/settings_screen.dart';
import '../settings/team_screen.dart';
import '../shared/inputs.dart';

/// One tool in the More list.
class _Tool {
  const _Tool(this.icon, this.titleKey, this.subtitleKey, {this.open});

  final IconData icon;
  final String titleKey;
  final String subtitleKey;

  /// Absent for a tool that has not shipped; the row says so.
  final void Function(BuildContext context)? open;
}

void _push(BuildContext context, Widget page) {
  unawaited(
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => page)),
  );
}

void Function(BuildContext) _page(Widget Function() build) =>
    (context) => _push(context, build());

/// The More tab: every tool that is not a daily tab, grouped and searchable.
///
/// A compact list rather than a card grid, so the whole map of the app fits
/// on about two screens and a seller finds a tool by name.
class MoreScreen extends ConsumerStatefulWidget {
  const MoreScreen({super.key, this.onNavigate});

  final ValueChanged<String>? onNavigate;

  @override
  ConsumerState<MoreScreen> createState() => _MoreScreenState();
}

class _MoreScreenState extends ConsumerState<MoreScreen> {
  final TextEditingController _search = TextEditingController();
  String _query = '';

  static final List<(String, List<_Tool>)> _groups = <(String, List<_Tool>)>[
    (
      'more.sell',
      <_Tool>[
        _Tool(
          Icons.inventory_2_outlined,
          'menu.products',
          'menu.productsSub',
          open: _page(ProductsScreen.new),
        ),
        _Tool(
          Icons.people_outline,
          'menu.customers',
          'menu.customersSub',
          open: _page(CustomersScreen.new),
        ),
        _Tool(
          Icons.storefront_outlined,
          'more.website',
          'more.websiteSub',
          open: _page(ConnectionsScreen.new),
        ),
        _Tool(
          Icons.campaign_outlined,
          'more.marketing',
          'more.marketingSub',
          open: _page(CampaignsScreen.new),
        ),
      ],
    ),
    (
      'more.delivery',
      <_Tool>[
        _Tool(
          Icons.compare_arrows_rounded,
          'more.compare',
          'more.compareSub',
          open: (context) => unawaited(CourierCompareScreen.open(context)),
        ),
        _Tool(
          Icons.local_shipping_outlined,
          'menu.courierAccounts',
          'menu.courierAccountsSub',
          open: _page(CourierAccountsScreen.new),
        ),
        _Tool(
          Icons.shield_outlined,
          'menu.riskCheck',
          'menu.riskCheckSub',
          open: _page(RiskCheckScreen.new),
        ),
        _Tool(
          Icons.assignment_return_outlined,
          'more.returns',
          'more.returnsSub',
          open: _page(RtoScreen.new),
        ),
      ],
    ),
    (
      'more.money',
      <_Tool>[
        _Tool(
          Icons.payments_outlined,
          'menu.expenses',
          'menu.expensesSub',
          open: _page(ExpensesScreen.new),
        ),
        _Tool(
          Icons.receipt_long_outlined,
          'menu.payouts',
          'menu.payoutsSub',
          open: _page(PayoutsScreen.new),
        ),
        _Tool(
          Icons.rule_folder_outlined,
          'menu.reconciliation',
          'menu.reconciliationSub',
          open: _page(CasesScreen.new),
        ),
        _Tool(
          Icons.import_export_rounded,
          'menu.imports',
          'menu.importsSub',
          open: _page(ImportsScreen.new),
        ),
      ],
    ),
    (
      'more.grow',
      <_Tool>[
        _Tool(
          Icons.insights_rounded,
          'more.analytics',
          'more.analyticsSub',
          open: _page(InsightsPage.new),
        ),
        _Tool(
          Icons.hub_outlined,
          'more.integrations',
          'more.integrationsSub',
          open: _page(IntegrationsScreen.new),
        ),
        _Tool(
          Icons.sensors_rounded,
          'more.connectionHealth',
          'more.connectionHealthSub',
          open: _page(ConnectionsScreen.new),
        ),
      ],
    ),
    (
      'more.business',
      <_Tool>[
        _Tool(
          Icons.checklist_rounded,
          'setup.title',
          'more.setupSub',
          open: (context) => unawaited(SetupChecklistSheet.show(context)),
        ),
        _Tool(
          Icons.groups_outlined,
          'menu.team',
          'menu.teamSub',
          open: _page(TeamScreen.new),
        ),
        _Tool(
          Icons.star_outline_rounded,
          'menu.subscription',
          'menu.subscriptionSub',
          open: _page(PlansScreen.new),
        ),
        _Tool(
          Icons.settings_outlined,
          'menu.settings',
          'menu.settingsSub',
          open: _page(SettingsScreen.new),
        ),
        const _Tool(
          Icons.help_outline_rounded,
          'menu.support',
          'menu.supportSub',
        ),
      ],
    ),
  ];

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  bool _matches(BuildContext context, _Tool tool) {
    if (_query.isEmpty) return true;
    final text = '${context.tr(tool.titleKey)} ${context.tr(tool.subtitleKey)}'
        .toLowerCase();
    return text.contains(_query);
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: EdgeInsets.fromLTRB(
        EcomsbdSpacing.page,
        EcomsbdLayout.shellTopPadding(context),
        EcomsbdSpacing.page,
        EcomsbdSpacing.bottomNavClearance,
      ),
      children: <Widget>[
        PageHeader(
          eyebrow: context.tr('more.eyebrow'),
          title: context.tr('more.title'),
          description: context.tr('more.description'),
        ),
        CommerceSearchField(
          controller: _search,
          hint: context.tr('more.search'),
          onChanged: (value) =>
              setState(() => _query = value.trim().toLowerCase()),
        ),
        for (final (titleKey, tools) in _groups)
          if (tools.any((tool) => _matches(context, tool))) ...<Widget>[
            GroupTitle(context.tr(titleKey)),
            ListCard(
              children: <Widget>[
                for (final tool in tools.where(
                  (tool) => _matches(context, tool),
                ))
                  _ToolRow(tool: tool),
              ],
            ),
          ],
      ],
    );
  }
}

class _ToolRow extends StatelessWidget {
  const _ToolRow({required this.tool});

  final _Tool tool;

  @override
  Widget build(BuildContext context) {
    final available = tool.open != null;
    return ListCardRow(
      icon: tool.icon,
      title: context.tr(tool.titleKey),
      subtitle: available
          ? context.tr(tool.subtitleKey)
          : context.tr('more.comingSoon'),
      enabled: available,
      onTap: available ? () => tool.open!(context) : null,
    );
  }
}

/// Insights, pushed from More or Home rather than living in the bottom bar.
///
/// [InsightsScreen] is built as a tab body; this gives it the page chrome and
/// a way back.
class InsightsPage extends StatelessWidget {
  const InsightsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return EcomsbdScaffold(
      topBar: GlassTopBar(
        leading: GlassBackPill(onPressed: () => Navigator.of(context).pop()),
        actions: const <Widget>[],
      ),
      child: const InsightsScreen(),
    );
  }
}
