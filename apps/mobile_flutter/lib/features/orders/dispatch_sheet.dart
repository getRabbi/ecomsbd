import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/analytics/models.dart';
import '../../data/money/money_providers.dart';
import '../../design/components/badges.dart';
import '../../design/tokens.dart';
import '../shared/inputs.dart';

/// Hand a parcel to a courier.
///
/// Manual mode. No provider is contacted: booking a real courier arrives with
/// the Steadfast adapter, and inventing its endpoints is forbidden (master spec
/// section 140). What this does is record what the seller already knows, which
/// is what makes the money side real — a COD receivable needs a delivery event
/// to exist.
class DispatchSheet extends ConsumerStatefulWidget {
  const DispatchSheet({required this.orderId, super.key});

  final String orderId;

  static Future<bool> show(
    BuildContext context, {
    required String orderId,
  }) async {
    final result = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => DispatchSheet(orderId: orderId),
    );
    return result ?? false;
  }

  @override
  ConsumerState<DispatchSheet> createState() => _DispatchSheetState();
}

class _DispatchSheetState extends ConsumerState<DispatchSheet> {
  final TextEditingController _tracking = TextEditingController();
  bool _busy = false;
  ApiError? _error;

  @override
  void dispose() {
    _tracking.dispose();
    super.dispose();
  }

  Future<void> _dispatch() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref
          .read(moneyRepositoryProvider)
          .dispatch(widget.orderId, trackingCode: _tracking.text.trim());
      ref.invalidate(moneySummaryProvider);
      if (mounted) {
        Navigator.of(context).pop(true);
      }
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = error;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final insets = MediaQuery.viewInsetsOf(context).bottom;

    return Padding(
      padding: EdgeInsets.only(bottom: insets),
      child: Container(
        decoration: const BoxDecoration(
          color: EcomsbdColors.backgroundLight,
          borderRadius: BorderRadius.vertical(
            top: Radius.circular(EcomsbdRadii.lg),
          ),
        ),
        padding: const EdgeInsets.fromLTRB(
          EcomsbdSpacing.lg,
          EcomsbdSpacing.md,
          EcomsbdSpacing.lg,
          EcomsbdSpacing.xl,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const _SheetGrip(),
            const SizedBox(height: EcomsbdSpacing.md),
            const Text('Hand to a courier', style: EcomsbdType.sectionTitle),
            const SizedBox(height: 3),
            Text(
              'Records that this parcel has gone out and takes the items off '
              'your stock. Nothing is sent to a courier — this is your own '
              'record until a courier account is connected.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            LabelledField(
              label: 'Tracking code',
              controller: _tracking,
              hint: 'Optional — helps match the payment later',
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            const Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Icon(Icons.info_outline, size: 15, color: EcomsbdColors.muted2),
                SizedBox(width: 6),
                Expanded(
                  child: Text(
                    'The money is not owed to you yet. It becomes owed when you '
                    'record the delivery.',
                    style: EcomsbdType.caption,
                  ),
                ),
              ],
            ),
            if (_error != null) ...<Widget>[
              const SizedBox(height: EcomsbdSpacing.sm),
              Text(
                _error!.displayMessage,
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
              ),
            ],
            const SizedBox(height: EcomsbdSpacing.md),
            FilledButton(
              onPressed: _busy ? null : _dispatch,
              style: _primaryButton,
              child: Text(_busy ? 'Recording…' : 'Parcel has gone out'),
            ),
          ],
        ),
      ),
    );
  }
}

/// Record how a parcel ended.
class OutcomeSheet extends ConsumerStatefulWidget {
  const OutcomeSheet({
    required this.consignmentId,
    super.key,
    this.itemCount = 1,
  });

  final String consignmentId;

  /// How many lines the parcel has. With more than one, a partial delivery is
  /// possible and this sheet says where to record it — assuming the original
  /// COD for a partial is the error master spec section 17.8 forbids.
  final int itemCount;

  static Future<bool> show(
    BuildContext context, {
    required String consignmentId,
    int itemCount = 1,
  }) async {
    final result = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) =>
          OutcomeSheet(consignmentId: consignmentId, itemCount: itemCount),
    );
    return result ?? false;
  }

  @override
  ConsumerState<OutcomeSheet> createState() => _OutcomeSheetState();
}

class _OutcomeSheetState extends ConsumerState<OutcomeSheet> {
  final TextEditingController _note = TextEditingController();
  String _status = 'DELIVERED';
  ReturnReason? _reason;
  bool _busy = false;
  ApiError? _error;

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref
          .read(moneyRepositoryProvider)
          .recordOutcome(
            widget.consignmentId,
            status: _status,
            note: _note.text.trim(),
            returnReason: _status == 'RETURNED' ? _reason?.wire : null,
          );
      ref.invalidate(moneySummaryProvider);
      // A finished parcel changes today's profit, so the screens that show it
      // are refetched rather than left showing the figure from before.
      ref.invalidate(homeMetricsProvider);
      ref.invalidate(profitReportProvider);
      ref.invalidate(returnReportProvider);
      if (mounted) {
        Navigator.of(context).pop(true);
      }
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = error;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final insets = MediaQuery.viewInsetsOf(context).bottom;

    return Padding(
      padding: EdgeInsets.only(bottom: insets),
      child: Container(
        decoration: const BoxDecoration(
          color: EcomsbdColors.backgroundLight,
          borderRadius: BorderRadius.vertical(
            top: Radius.circular(EcomsbdRadii.lg),
          ),
        ),
        padding: const EdgeInsets.fromLTRB(
          EcomsbdSpacing.lg,
          EcomsbdSpacing.md,
          EcomsbdSpacing.lg,
          EcomsbdSpacing.xl,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const _SheetGrip(),
            const SizedBox(height: EcomsbdSpacing.md),
            const Text('What happened?', style: EcomsbdType.sectionTitle),
            const SizedBox(height: EcomsbdSpacing.md),
            Wrap(
              spacing: EcomsbdSpacing.xs,
              runSpacing: EcomsbdSpacing.xs,
              children: <Widget>[
                FilterToggle(
                  label: 'Delivered',
                  selected: _status == 'DELIVERED',
                  onChanged: (_) => setState(() => _status = 'DELIVERED'),
                ),
                FilterToggle(
                  label: 'Came back',
                  selected: _status == 'RETURNED',
                  onChanged: (_) => setState(() => _status = 'RETURNED'),
                ),
              ],
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            Text(
              _status == 'DELIVERED'
                  ? 'The courier now owes you the COD. It shows on the Money '
                        'screen until a payment is matched to it.'
                  : 'Nothing is owed, and the items go back on your shelf.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            if (_status == 'RETURNED') ...<Widget>[
              const SizedBox(height: EcomsbdSpacing.md),
              const Text('Why did it come back?', style: EcomsbdType.label),
              const SizedBox(height: 3),
              Text(
                'Optional. Skip it rather than guess — the return report is '
                'only worth acting on if the reasons in it are real.',
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
              const SizedBox(height: EcomsbdSpacing.xs),
              Wrap(
                spacing: EcomsbdSpacing.xs,
                runSpacing: EcomsbdSpacing.xs,
                children: <Widget>[
                  for (final reason in ReturnReason.values)
                    FilterToggle(
                      label: reason.label,
                      selected: _reason == reason,
                      onChanged: (selected) =>
                          setState(() => _reason = selected ? reason : null),
                    ),
                ],
              ),
            ],
            if (widget.itemCount > 1) ...<Widget>[
              const SizedBox(height: EcomsbdSpacing.sm),
              const StatusChip(
                label: 'Only part delivered? Record it on the parcel',
                tone: Tone.info,
                showIcon: false,
              ),
            ],
            const SizedBox(height: EcomsbdSpacing.md),
            LabelledField(
              label: 'Note',
              controller: _note,
              hint: 'Optional — what the courier said',
            ),
            if (_error != null) ...<Widget>[
              const SizedBox(height: EcomsbdSpacing.sm),
              Text(
                _error!.displayMessage,
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
              ),
            ],
            const SizedBox(height: EcomsbdSpacing.md),
            FilledButton(
              onPressed: _busy ? null : _save,
              style: _primaryButton,
              child: Text(_busy ? 'Saving…' : 'Record it'),
            ),
          ],
        ),
      ),
    );
  }
}

class _SheetGrip extends StatelessWidget {
  const _SheetGrip();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Container(
        width: 40,
        height: 4,
        decoration: BoxDecoration(
          color: EcomsbdColors.trackLight,
          borderRadius: BorderRadius.circular(2),
        ),
      ),
    );
  }
}

final ButtonStyle _primaryButton = FilledButton.styleFrom(
  backgroundColor: EcomsbdColors.orange,
  minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
  shape: const StadiumBorder(),
  textStyle: EcomsbdType.label,
);
