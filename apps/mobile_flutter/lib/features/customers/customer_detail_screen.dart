import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../shared/responsive.dart';

/// One customer: history, private notes, and the audited phone reveal.
class CustomerDetailScreen extends ConsumerStatefulWidget {
  const CustomerDetailScreen({required this.customerId, super.key});

  final String customerId;

  @override
  ConsumerState<CustomerDetailScreen> createState() =>
      _CustomerDetailScreenState();
}

class _CustomerDetailScreenState extends ConsumerState<CustomerDetailScreen> {
  /// Held in memory only, for as long as this screen is open.
  String? _revealedPhone;

  Future<void> _revealPhone() async {
    final reason = await _RevealReasonDialog.show(context);
    if (reason == null || !mounted) {
      return;
    }
    try {
      final phone = await ref
          .read(customersRepositoryProvider)
          .revealPhone(widget.customerId, reason: reason);
      if (mounted) {
        setState(() => _revealedPhone = phone);
      }
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    }
  }

  Future<void> _setFlag(Customer customer, String flag) async {
    String? reason;
    if (flag == 'BLOCKED') {
      reason = await _FlagReasonDialog.show(context);
      if (reason == null) {
        return;
      }
    }
    try {
      await ref
          .read(customersRepositoryProvider)
          .update(customer.id, flag: flag, flagReason: reason);
      ref.invalidate(customerProvider(customer.id));
      unawaited(ref.read(customerListProvider.notifier).refresh());
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    }
  }

  Future<void> _editNotes(Customer customer) async {
    final notes = await _NotesDialog.show(context, initial: customer.notes);
    if (notes == null) {
      return;
    }
    try {
      await ref
          .read(customersRepositoryProvider)
          .update(customer.id, notes: notes);
      ref.invalidate(customerProvider(customer.id));
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(customerProvider(widget.customerId));

    return async.when(
      loading: () => DetailScaffold(
        title: 'Customer',
        children: <Widget>[SkeletonLoader.card(height: 180)],
      ),
      error: (error, _) => DetailScaffold(
        title: 'Customer',
        children: <Widget>[
          if (error is ApiError)
            ErrorStateCard(
              error: error,
              onRetry: () =>
                  ref.invalidate(customerProvider(widget.customerId)),
            )
          else
            EmptyState(
              icon: Icons.error_outline,
              title: 'Could not load',
              message: '$error',
            ),
        ],
      ),
      data: (customer) => _CustomerBody(
        customer: customer,
        revealedPhone: _revealedPhone,
        onReveal: _revealPhone,
        onFlag: (flag) => _setFlag(customer, flag),
        onEditNotes: () => _editNotes(customer),
      ),
    );
  }
}

class _CustomerBody extends ConsumerWidget {
  const _CustomerBody({
    required this.customer,
    required this.revealedPhone,
    required this.onReveal,
    required this.onFlag,
    required this.onEditNotes,
  });

  final Customer customer;
  final String? revealedPhone;
  final VoidCallback onReveal;
  final ValueChanged<String> onFlag;
  final VoidCallback onEditNotes;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final history = ref.watch(customerOrdersProvider(customer.id));

    return DetailScaffold(
      title: customer.displayName,
      subtitle: revealedPhone ?? customer.phoneMasked,
      children: <Widget>[
        if (customer.isBlocked) ...<Widget>[
          ProviderHealthBanner(
            provider: 'Blocked for this shop',
            detail: customer.flagReason?.isNotEmpty == true
                ? customer.flagReason!
                : 'You marked this customer blocked. Orders can still be '
                      'created; nothing is enforced automatically.',
            tone: Tone.bad,
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
        ],
        GlassCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      revealedPhone ?? customer.phoneMasked,
                      style: EcomsbdType.sectionTitle,
                    ),
                  ),
                  if (revealedPhone == null)
                    TextButton.icon(
                      onPressed: onReveal,
                      icon: const Icon(Icons.visibility_outlined, size: 17),
                      label: const Text('Show number'),
                      style: TextButton.styleFrom(
                        foregroundColor: EcomsbdColors.orange,
                        minimumSize: const Size(0, EcomsbdTouch.minTarget),
                        textStyle: EcomsbdType.chip,
                      ),
                    )
                  else
                    const StatusChip(
                      label: 'Revealed · recorded',
                      tone: Tone.info,
                      icon: Icons.receipt_long_outlined,
                    ),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                revealedPhone == null
                    ? 'Numbers stay masked. Showing one asks for a reason and '
                          'is written to your shop’s audit log.'
                    : 'This reveal was recorded with your reason.',
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
            ],
          ),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        ResponsiveGrid(
          minTileWidth: 150,
          maxColumns: 2,
          spacing: EcomsbdSpacing.xs,
          children: <Widget>[
            MetricTile(
              label: 'Orders',
              value: '${customer.orderCount}',
              caption: customer.isRepeatBuyer ? 'Repeat buyer' : 'First-time',
            ),
            MetricTile(
              label: 'Delivered',
              value: '${customer.deliveredCount}',
              caption: customer.successRateLabel,
              tone: customer.successRateBasisPoints == null
                  ? null
                  : (customer.successRateBasisPoints! >= 7000
                        ? Tone.good
                        : Tone.warning),
            ),
            MetricTile(
              label: 'Returned',
              value: '${customer.returnedCount}',
              caption: 'parcels',
              tone: customer.returnedCount > 0 ? Tone.bad : null,
            ),
            MetricTile(
              label: 'Revenue',
              value: customer.realizedRevenue.formatCompact(),
              caption: 'settled only',
            ),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        GlassCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  const Expanded(
                    child: Text('Your note', style: EcomsbdType.bodyStrong),
                  ),
                  TextButton(
                    onPressed: onEditNotes,
                    style: TextButton.styleFrom(
                      foregroundColor: EcomsbdColors.orange,
                      minimumSize: const Size(0, EcomsbdTouch.minTarget),
                      textStyle: EcomsbdType.chip,
                    ),
                    child: Text(customer.notes == null ? 'Add' : 'Edit'),
                  ),
                ],
              ),
              Text(
                customer.notes?.isNotEmpty == true
                    ? customer.notes!
                    : 'Private to your shop. Nobody else sees it.',
                style: EcomsbdType.caption.copyWith(
                  color: customer.notes?.isNotEmpty == true
                      ? EcomsbdColors.ink
                      : EcomsbdColors.muted,
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        Row(
          children: <Widget>[
            Expanded(
              child: FilterToggle(
                label: customer.isStarred ? 'Starred' : 'Star',
                selected: customer.isStarred,
                onChanged: (selected) => onFlag(selected ? 'STARRED' : 'NONE'),
              ),
            ),
            const SizedBox(width: EcomsbdSpacing.sm),
            Expanded(
              child: FilterToggle(
                label: customer.isBlocked ? 'Blocked' : 'Block',
                selected: customer.isBlocked,
                onChanged: (selected) => onFlag(selected ? 'BLOCKED' : 'NONE'),
              ),
            ),
          ],
        ),
        if (customer.addresses.isNotEmpty) ...<Widget>[
          const SectionHeader(title: 'Addresses'),
          for (final address in customer.addresses)
            Padding(
              padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
              child: GlassCard(
                padding: const EdgeInsets.all(EcomsbdSpacing.md),
                borderRadius: EcomsbdRadii.cardMedium,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    // The seller's own words, never a courier's rewrite.
                    Text(address.rawAddress, style: EcomsbdType.body),
                    if (address.district != null || address.area != null)
                      Padding(
                        padding: const EdgeInsets.only(top: 3),
                        child: Text(
                          <String?>[
                            address.area,
                            address.district,
                          ].whereType<String>().join(', '),
                          style: EcomsbdType.caption.copyWith(
                            color: EcomsbdColors.muted,
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
        ],
        const SectionHeader(title: 'Recent orders'),
        history.when(
          loading: () => SkeletonLoader.card(height: 80),
          error: (error, _) => Text(
            'Could not load this customer’s orders.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          data: (orders) {
            if (orders.isEmpty) {
              return Text(
                'No orders yet.',
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              );
            }
            return Column(
              children: <Widget>[
                for (final order in orders)
                  GlassListRow(
                    title: order.orderNumber,
                    subtitle:
                        '${order.statusLabel} · ${formatRelative(order.createdAt)}',
                    trailingTop: order.codAmount.format(),
                    trailingBottom: order.itemSummary,
                  ),
              ],
            );
          },
        ),
      ],
    );
  }
}

/// Asks why the number is being revealed.
///
/// The reason is stored with the audit entry, which is the point: a seller's
/// own access to their customers' numbers is traceable (master spec
/// section 101).
class _RevealReasonDialog extends StatefulWidget {
  const _RevealReasonDialog();

  static Future<String?> show(BuildContext context) => showDialog<String>(
    context: context,
    builder: (_) => const _RevealReasonDialog(),
  );

  @override
  State<_RevealReasonDialog> createState() => _RevealReasonDialogState();
}

class _RevealReasonDialogState extends State<_RevealReasonDialog> {
  final TextEditingController _reason = TextEditingController();

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Why do you need the number?'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            'This is written to your shop’s audit log with your name and the '
            'time.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          LabelledField(
            label: 'Reason',
            controller: _reason,
            hint: 'Calling about a failed delivery',
          ),
        ],
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () {
            final reason = _reason.text.trim();
            // The server requires at least three characters; saying so here
            // beats a round trip that fails.
            if (reason.length < 3) {
              return;
            }
            Navigator.of(context).pop(reason);
          },
          child: const Text('Show number'),
        ),
      ],
    );
  }
}

class _FlagReasonDialog extends StatefulWidget {
  const _FlagReasonDialog();

  static Future<String?> show(BuildContext context) => showDialog<String>(
    context: context,
    builder: (_) => const _FlagReasonDialog(),
  );

  @override
  State<_FlagReasonDialog> createState() => _FlagReasonDialogState();
}

class _FlagReasonDialogState extends State<_FlagReasonDialog> {
  final TextEditingController _reason = TextEditingController();

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Block for your shop'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            'This is a note to yourself. It is private to this shop, it is not '
            'shared with anyone, and it does not stop an order being created.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          LabelledField(
            label: 'Reason',
            controller: _reason,
            hint: 'Three parcels refused at the door',
          ),
        ],
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_reason.text.trim()),
          child: const Text('Block'),
        ),
      ],
    );
  }
}

class _NotesDialog extends StatefulWidget {
  const _NotesDialog({this.initial});

  final String? initial;

  static Future<String?> show(BuildContext context, {String? initial}) =>
      showDialog<String>(
        context: context,
        builder: (_) => _NotesDialog(initial: initial),
      );

  @override
  State<_NotesDialog> createState() => _NotesDialogState();
}

class _NotesDialogState extends State<_NotesDialog> {
  late final TextEditingController _notes = TextEditingController(
    text: widget.initial ?? '',
  );

  @override
  void dispose() {
    _notes.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Your note'),
      content: LabelledField(
        label: 'Note',
        controller: _notes,
        maxLines: 4,
        hint: 'Prefers delivery after 6pm',
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_notes.text.trim()),
          child: const Text('Save'),
        ),
      ],
    );
  }
}
