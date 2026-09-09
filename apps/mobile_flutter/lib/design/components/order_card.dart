import 'package:flutter/material.dart';

import '../../core/money.dart';
import '../tokens.dart';
import 'badges.dart';
import 'surfaces.dart';

/// One `.mini` cell inside an [OrderCard].
class OrderFact {
  const OrderFact({required this.label, required this.value, this.tone});

  final String label;
  final String value;
  final Tone? tone;
}

/// `.order-card` — the order row on the Orders feed.
///
/// The card deliberately shows courier state and money state as **separate**
/// facts. Master spec section 1.4: a parcel can be `DELIVERED` while its COD is
/// still unpaid, and collapsing the two into one "status" is exactly the
/// mistake that hides unpaid money from the seller.
class OrderCard extends StatelessWidget {
  const OrderCard({
    required this.reference,
    required this.customerName,
    required this.status,
    required this.facts,
    super.key,
    this.maskedPhone,
    this.area,
    this.itemSummary,
    this.onTap,
  });

  /// Seller-facing order number, e.g. `CP-20260909-0042`.
  final String reference;

  final String customerName;
  final Widget status;
  final List<OrderFact> facts;

  /// Already masked by the server; the client never receives a full number.
  final String? maskedPhone;

  final String? area;
  final String? itemSummary;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return StrongGlassCard(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      borderRadius: BorderRadius.circular(20),
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(
                      reference,
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted2,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      customerName,
                      style: EcomsbdType.sectionTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              status,
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          _FactGrid(facts: facts),
          if (maskedPhone != null || itemSummary != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    <String?>[
                      maskedPhone,
                      area,
                    ].whereType<String>().join(' · '),
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (itemSummary != null)
                  Flexible(
                    child: Text(
                      itemSummary!,
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                      textAlign: TextAlign.right,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _FactGrid extends StatelessWidget {
  const _FactGrid({required this.facts});

  final List<OrderFact> facts;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        // Four across on a wide screen; two on a 360dp phone, matching the
        // prototype's own breakpoint.
        final columns = constraints.maxWidth >= 420 ? 4 : 2;
        const gap = EcomsbdSpacing.xs;
        final width = (constraints.maxWidth - gap * (columns - 1)) / columns;
        return Wrap(
          spacing: gap,
          runSpacing: gap,
          children: <Widget>[
            for (final fact in facts)
              SizedBox(
                width: width,
                child: _FactTile(fact: fact),
              ),
          ],
        );
      },
    );
  }
}

class _FactTile extends StatelessWidget {
  const _FactTile({required this.fact});

  final OrderFact fact;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(9),
      decoration: BoxDecoration(
        color: EcomsbdColors.miniTile,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(
            fact.label.toUpperCase(),
            style: EcomsbdType.eyebrow.copyWith(color: EcomsbdColors.muted2),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 3),
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(
              fact.value,
              style: EcomsbdType.bodyStrong.copyWith(color: fact.tone?.ink),
            ),
          ),
        ],
      ),
    );
  }
}

/// A step in `.timeline`.
class TimelineEntry {
  const TimelineEntry({
    required this.title,
    required this.detail,
    required this.state,
    this.icon,
  });

  final String title;
  final String detail;
  final TimelineState state;
  final IconData? icon;
}

enum TimelineState { done, current, pending, failed }

/// `.timeline` — the order tracking timeline.
///
/// Courier status and financial state are rendered as separate entries, never
/// merged, so "delivered" never implies "paid".
class Timeline extends StatelessWidget {
  const Timeline({required this.entries, super.key});

  final List<TimelineEntry> entries;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        for (var i = 0; i < entries.length; i++)
          _TimelineRow(entry: entries[i], isLast: i == entries.length - 1),
      ],
    );
  }
}

class _TimelineRow extends StatelessWidget {
  const _TimelineRow({required this.entry, required this.isLast});

  final TimelineEntry entry;
  final bool isLast;

  @override
  Widget build(BuildContext context) {
    final (background, ink, defaultIcon) = switch (entry.state) {
      TimelineState.done => (
        EcomsbdColors.greenSoft,
        EcomsbdColors.green,
        Icons.check_rounded,
      ),
      TimelineState.current => (
        EcomsbdColors.amberSoft,
        EcomsbdColors.amber,
        Icons.autorenew_rounded,
      ),
      TimelineState.failed => (
        EcomsbdColors.redSoft,
        EcomsbdColors.red,
        Icons.close_rounded,
      ),
      TimelineState.pending => (
        const Color(0xFFE9EEF2),
        EcomsbdColors.muted,
        Icons.circle_outlined,
      ),
    };

    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Column(
            children: <Widget>[
              Container(
                width: 28,
                height: 28,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: background,
                ),
                child: Icon(entry.icon ?? defaultIcon, size: 15, color: ink),
              ),
              if (!isLast)
                const Expanded(
                  child: SizedBox(
                    width: 2,
                    child: ColoredBox(color: Color(0xFFE2E8ED)),
                  ),
                ),
            ],
          ),
          const SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: Padding(
              padding: EdgeInsets.only(bottom: isLast ? 0 : EcomsbdSpacing.md),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Text(entry.title, style: EcomsbdType.bodyStrong),
                  const SizedBox(height: 2),
                  Text(
                    entry.detail,
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Convenience: an amount cell for [OrderFact].
OrderFact moneyFact(String label, Money amount, {Tone? tone}) =>
    OrderFact(label: label, value: amount.format(), tone: tone);
