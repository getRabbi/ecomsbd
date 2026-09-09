import 'package:flutter/material.dart';

import '../../core/money.dart';
import '../glass.dart';
import '../tokens.dart';
import 'badges.dart';
import 'surfaces.dart';

/// One figure inside [HeroMoneyCard].
class HeroKpi {
  const HeroKpi({
    required this.label,
    required this.value,
    this.caption,
    this.tone,
  });

  final String label;
  final String value;
  final String? caption;

  /// Colours the value. Used for the mismatch figure, which is a loss.
  final Tone? tone;
}

/// `.money-hero` — the dominant money statement on Home, Money and Insights.
///
/// This is the screen's answer to "how much money is out there right now",
/// so the amount is the largest thing on the page and the supporting figures
/// sit beneath it rather than competing.
class HeroMoneyCard extends StatelessWidget {
  const HeroMoneyCard({
    required this.eyebrow,
    required this.amount,
    super.key,
    this.subtitle,
    this.trailing,
    this.kpis = const <HeroKpi>[],
    this.quality = DataQuality.actual,
    this.showQualityDot = true,
  });

  final String eyebrow;
  final Money amount;
  final String? subtitle;

  /// Usually a [StatusChip] carrying the overdue amount.
  final Widget? trailing;

  final List<HeroKpi> kpis;
  final DataQuality quality;
  final bool showQualityDot;

  @override
  Widget build(BuildContext context) {
    return StrongGlassCard(
      padding: const EdgeInsets.all(EcomsbdSpacing.lg),
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
                    Row(
                      children: <Widget>[
                        if (showQualityDot) ...<Widget>[
                          Container(
                            width: 7,
                            height: 7,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: quality == DataQuality.actual
                                  ? EcomsbdColors.green
                                  : EcomsbdColors.amber,
                            ),
                          ),
                          const SizedBox(width: EcomsbdSpacing.xs),
                        ],
                        Flexible(
                          child: Text(
                            eyebrow.toUpperCase(),
                            style: EcomsbdType.eyebrow.copyWith(
                              color: EcomsbdColors.muted2,
                            ),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: EcomsbdSpacing.xs),
                    FittedBox(
                      fit: BoxFit.scaleDown,
                      alignment: Alignment.centerLeft,
                      child: MoneyText(amount, style: EcomsbdType.heroMoney),
                    ),
                    if (subtitle != null) ...<Widget>[
                      const SizedBox(height: 4),
                      Text(
                        subtitle!,
                        style: EcomsbdType.caption.copyWith(
                          color: EcomsbdColors.muted,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              if (trailing != null) ...<Widget>[
                const SizedBox(width: EcomsbdSpacing.sm),
                trailing!,
              ],
            ],
          ),
          if (kpis.isNotEmpty) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.md),
            _HeroKpiRow(kpis: kpis),
          ],
        ],
      ),
    );
  }
}

class _HeroKpiRow extends StatelessWidget {
  const _HeroKpiRow({required this.kpis});

  final List<HeroKpi> kpis;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        // Three across needs room; on a 360dp screen the prototype's own
        // breakpoint drops to two columns with the last spanning both.
        final columns = constraints.maxWidth >= 420 ? 3 : 2;
        return Wrap(
          spacing: EcomsbdSpacing.xs,
          runSpacing: EcomsbdSpacing.xs,
          children: <Widget>[
            for (var i = 0; i < kpis.length; i++)
              SizedBox(
                width: _tileWidth(constraints.maxWidth, columns, i),
                child: _HeroKpiTile(kpi: kpis[i]),
              ),
          ],
        );
      },
    );
  }

  double _tileWidth(double available, int columns, int index) {
    final gaps = EcomsbdSpacing.xs * (columns - 1);
    final unit = (available - gaps) / columns;
    final isLast = index == kpis.length - 1;
    final leftover = kpis.length % columns;
    if (columns == 2 && isLast && leftover == 1) {
      return available;
    }
    return unit;
  }
}

class _HeroKpiTile extends StatelessWidget {
  const _HeroKpiTile({required this.kpi});

  final HeroKpi kpi;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(11),
      decoration: BoxDecoration(
        color: const Color(0xBDF5F8FA),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(
            kpi.label.toUpperCase(),
            style: EcomsbdType.eyebrow.copyWith(color: EcomsbdColors.muted2),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 5),
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(
              kpi.value,
              style: EcomsbdType.metricValue.copyWith(color: kpi.tone?.ink),
            ),
          ),
          if (kpi.caption != null) ...<Widget>[
            const SizedBox(height: 2),
            Text(
              kpi.caption!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ],
      ),
    );
  }
}

/// `.metric-box` — a small labelled figure in a strip.
class MetricTile extends StatelessWidget {
  const MetricTile({
    required this.label,
    required this.value,
    super.key,
    this.caption,
    this.tone,
    this.onTap,
  });

  final String label;
  final String value;
  final String? caption;
  final Tone? tone;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return GlassSurface(
      borderRadius: BorderRadius.circular(17),
      fill: EcomsbdColors.glass,
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(17),
          child: Padding(
            padding: const EdgeInsets.all(EcomsbdSpacing.md),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text(
                  label.toUpperCase(),
                  style: EcomsbdType.eyebrow.copyWith(
                    color: EcomsbdColors.muted2,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 5),
                FittedBox(
                  fit: BoxFit.scaleDown,
                  alignment: Alignment.centerLeft,
                  child: Text(
                    value,
                    style: EcomsbdType.metricValue.copyWith(color: tone?.ink),
                  ),
                ),
                if (caption != null) ...<Widget>[
                  const SizedBox(height: 2),
                  Text(
                    caption!,
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// `.quick` — a large tap target in the quick-actions grid.
class QuickActionTile extends StatelessWidget {
  const QuickActionTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    super.key,
    this.onTap,
    this.enabled = true,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback? onTap;

  /// Disabled actions stay visible and explain themselves rather than
  /// disappearing, so the seller can see what the app can do.
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: enabled ? 1 : 0.55,
      child: GlassSurface(
        borderRadius: BorderRadius.circular(20),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            onTap: enabled ? onTap : null,
            borderRadius: BorderRadius.circular(20),
            child: Padding(
              padding: const EdgeInsets.all(EcomsbdSpacing.md),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Container(
                    width: 36,
                    height: 36,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: EcomsbdColors.orangeSoft,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Icon(icon, size: 19, color: EcomsbdColors.orange),
                  ),
                  const SizedBox(height: EcomsbdSpacing.sm),
                  Text(
                    title,
                    style: EcomsbdType.bodyStrong,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    subtitle,
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted,
                    ),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
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

/// One row inside [AttentionCard].
class AttentionItem {
  const AttentionItem({
    required this.title,
    required this.detail,
    required this.tone,
    this.icon,
    this.onTap,
  });

  final String title;
  final String detail;
  final Tone tone;
  final IconData? icon;
  final VoidCallback? onTap;
}

/// `.premium-panel` with `.attention-grid` — "Needs attention".
///
/// Master spec section 23: only items that cost money or require an action
/// belong here. A tidy list of everything that happened is not this component.
class AttentionCard extends StatelessWidget {
  const AttentionCard({
    required this.items,
    super.key,
    this.title = 'Needs attention',
    this.subtitle = 'Only issues that can cost money or require action',
    this.actionLabel,
    this.onAction,
    this.side,
  });

  final List<AttentionItem> items;
  final String title;
  final String subtitle;
  final String? actionLabel;
  final VoidCallback? onAction;

  /// The dark "protected profit" panel beside the list.
  final Widget? side;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          SectionHeader(
            title: title,
            subtitle: subtitle,
            actionLabel: actionLabel,
            onAction: onAction,
          ),
          for (final item in items) ...<Widget>[
            _AttentionRow(item: item),
            const SizedBox(height: EcomsbdSpacing.xs),
          ],
          if (side != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xxs),
            side!,
          ],
        ],
      ),
    );
  }
}

class _AttentionRow extends StatelessWidget {
  const _AttentionRow({required this.item});

  final AttentionItem item;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: const Color(0xBDF6F8FA),
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        onTap: item.onTap,
        borderRadius: BorderRadius.circular(16),
        child: Padding(
          padding: const EdgeInsets.all(11),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Container(
                width: 31,
                height: 31,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: item.tone.surface,
                  borderRadius: BorderRadius.circular(11),
                ),
                child: Icon(
                  item.icon ?? item.tone.icon,
                  size: 16,
                  color: item.tone.ink,
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(item.title, style: EcomsbdType.bodyStrong),
                    const SizedBox(height: 3),
                    Text(
                      item.detail,
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// `.attention-side` — the dark panel highlighting protected profit.
class DarkHighlightPanel extends StatelessWidget {
  const DarkHighlightPanel({
    required this.label,
    required this.value,
    required this.description,
    super.key,
    this.actionLabel,
    this.onAction,
  });

  final String label;
  final String value;
  final String description;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        gradient: const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomRight,
          colors: <Color>[Color(0xFF101B2A), Color(0xFF07111D)],
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(
            label.toUpperCase(),
            style: EcomsbdType.eyebrow.copyWith(color: const Color(0x8CFFFFFF)),
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            value,
            style: EcomsbdType.heroTitle.copyWith(color: Colors.white),
          ),
          const SizedBox(height: 4),
          Text(
            description,
            style: EcomsbdType.caption.copyWith(color: const Color(0xABFFFFFF)),
          ),
          if (actionLabel != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.md),
            SizedBox(
              width: double.infinity,
              height: 40,
              child: OutlinedButton(
                onPressed: onAction,
                style: OutlinedButton.styleFrom(
                  foregroundColor: Colors.white,
                  side: const BorderSide(color: Color(0x21FFFFFF)),
                  backgroundColor: const Color(0x12FFFFFF),
                  shape: const StadiumBorder(),
                  textStyle: EcomsbdType.chip,
                ),
                child: Text(actionLabel!),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// `.feed-card` — the Reddit-style activity card.
class SellerFeedCard extends StatelessWidget {
  const SellerFeedCard({
    required this.author,
    required this.timestamp,
    required this.headline,
    required this.body,
    super.key,
    this.avatarLabel,
    this.avatarColor,
    this.status,
    this.onTap,
  });

  final String author;
  final String timestamp;
  final String headline;
  final String body;
  final String? avatarLabel;
  final Color? avatarColor;
  final Widget? status;
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
            children: <Widget>[
              Container(
                width: 32,
                height: 32,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: avatarColor == null
                      ? const LinearGradient(
                          begin: Alignment.topLeft,
                          end: Alignment.bottomRight,
                          colors: <Color>[
                            EcomsbdColors.orange,
                            EcomsbdColors.orangeLight,
                          ],
                        )
                      : null,
                  color: avatarColor,
                ),
                child: Text(
                  avatarLabel ?? author.characters.first.toUpperCase(),
                  style: EcomsbdType.chip.copyWith(color: Colors.white),
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(
                      author,
                      style: EcomsbdType.bodyStrong,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    Text(
                      timestamp,
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
              if (status != null) status!,
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Text(headline, style: EcomsbdType.sectionTitle),
          const SizedBox(height: 4),
          Text(
            body,
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ],
      ),
    );
  }
}

/// `.row` — a compact list row used across courier health, products and
/// customers.
class GlassListRow extends StatelessWidget {
  const GlassListRow({
    required this.title,
    super.key,
    this.subtitle,
    this.leading,
    this.trailingTop,
    this.trailingBottom,
    this.trailing,
    this.onTap,
  });

  final String title;
  final String? subtitle;
  final Widget? leading;
  final String? trailingTop;
  final String? trailingBottom;
  final Widget? trailing;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: const Color(0x94FFFFFF),
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(16),
        child: Container(
          padding: const EdgeInsets.all(11),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: const Color(0xF0FFFFFF)),
          ),
          child: Row(
            children: <Widget>[
              if (leading != null) ...<Widget>[
                leading!,
                const SizedBox(width: EcomsbdSpacing.sm),
              ],
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(
                      title,
                      style: EcomsbdType.bodyStrong,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    if (subtitle != null) ...<Widget>[
                      const SizedBox(height: 2),
                      Text(
                        subtitle!,
                        style: EcomsbdType.caption.copyWith(
                          color: EcomsbdColors.muted,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ],
                  ],
                ),
              ),
              if (trailing != null) ...<Widget>[
                const SizedBox(width: EcomsbdSpacing.xs),
                trailing!,
              ] else if (trailingTop != null) ...<Widget>[
                const SizedBox(width: EcomsbdSpacing.xs),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(trailingTop!, style: EcomsbdType.money),
                    if (trailingBottom != null)
                      Text(
                        trailingBottom!,
                        style: EcomsbdType.caption.copyWith(
                          color: EcomsbdColors.muted,
                        ),
                      ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// A rounded icon for [GlassListRow].
class RowIcon extends StatelessWidget {
  const RowIcon({required this.label, super.key, this.background, this.ink});

  final String label;
  final Color? background;
  final Color? ink;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 36,
      height: 36,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: background ?? EcomsbdColors.rowIconBackground,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        label,
        style: EcomsbdType.bodyStrong.copyWith(
          color: ink ?? EcomsbdColors.rowIconInk,
        ),
      ),
    );
  }
}

/// `.finding` — a reconciliation finding with its evidence tags.
///
/// Tags carry the *reason* a line was flagged (exact reference, underpaid,
/// unknown deduction) because master spec section 83 requires a reconciliation
/// result to remain explainable later.
class FindingCard extends StatelessWidget {
  const FindingCard({
    required this.title,
    required this.description,
    super.key,
    this.amount,
    this.status,
    this.tags = const <String>[],
    this.actions = const <Widget>[],
  });

  final String title;
  final String description;
  final Widget? amount;
  final Widget? status;
  final List<String> tags;
  final List<Widget> actions;

  @override
  Widget build(BuildContext context) {
    return StrongGlassCard(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      borderRadius: BorderRadius.circular(18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(child: Text(title, style: EcomsbdType.bodyStrong)),
              const SizedBox(width: EcomsbdSpacing.xs),
              if (amount != null) amount! else if (status != null) status!,
            ],
          ),
          const SizedBox(height: 5),
          Text(
            description,
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          if (tags.isNotEmpty) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            Wrap(
              spacing: 5,
              runSpacing: 5,
              children: <Widget>[
                for (final tag in tags)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 5,
                    ),
                    decoration: const BoxDecoration(
                      color: EcomsbdColors.tagBackground,
                      borderRadius: EcomsbdRadii.round,
                    ),
                    child: Text(
                      tag,
                      style: EcomsbdType.chip.copyWith(
                        color: EcomsbdColors.tagInk,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
              ],
            ),
          ],
          if (actions.isNotEmpty) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            Wrap(spacing: EcomsbdSpacing.xs, children: actions),
          ],
        ],
      ),
    );
  }
}

/// `.aging-row` — one COD aging bucket.
class MoneyAgingRow extends StatelessWidget {
  const MoneyAgingRow({
    required this.label,
    required this.amount,
    required this.fraction,
    super.key,
    this.tone = Tone.info,
  });

  final String label;
  final Money amount;

  /// Share of the largest bucket, 0..1.
  final double fraction;

  final Tone tone;

  @override
  Widget build(BuildContext context) {
    final colors = switch (tone) {
      Tone.bad => <Color>[const Color(0xFFE36A70), EcomsbdColors.red],
      Tone.warning => <Color>[const Color(0xFFD99C31), EcomsbdColors.amber],
      _ => <Color>[const Color(0xFF284F75), EcomsbdColors.blue],
    };

    return Semantics(
      label: '$label ${amount.format()}',
      excludeSemantics: true,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 5),
        child: Row(
          children: <Widget>[
            SizedBox(width: 74, child: Text(label, style: EcomsbdType.label)),
            Expanded(
              child: _BarTrack(fraction: fraction, colors: colors),
            ),
            const SizedBox(width: EcomsbdSpacing.xs),
            SizedBox(
              width: 72,
              child: Text(
                amount.format(),
                style: EcomsbdType.label.copyWith(color: EcomsbdColors.muted),
                textAlign: TextAlign.right,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _BarTrack extends StatelessWidget {
  const _BarTrack({required this.fraction, required this.colors});

  final double fraction;
  final List<Color> colors;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: EcomsbdRadii.round,
      child: SizedBox(
        height: 9,
        child: Stack(
          children: <Widget>[
            const ColoredBox(
              color: EcomsbdColors.trackMuted,
              child: SizedBox.expand(),
            ),
            FractionallySizedBox(
              widthFactor: fraction.clamp(0.0, 1.0),
              child: DecoratedBox(
                decoration: BoxDecoration(
                  borderRadius: EcomsbdRadii.round,
                  gradient: LinearGradient(colors: colors),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// `.chart-card` — the frame every chart is presented in.
class PremiumChartCard extends StatelessWidget {
  const PremiumChartCard({
    required this.title,
    required this.child,
    super.key,
    this.subtitle,
    this.trailing,
    this.minHeight = 238,
  });

  final String title;
  final String? subtitle;
  final Widget? trailing;
  final Widget child;
  final double minHeight;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      child: ConstrainedBox(
        constraints: BoxConstraints(minHeight: minHeight),
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
                      Text(title, style: EcomsbdType.sectionTitle),
                      if (subtitle != null) ...<Widget>[
                        const SizedBox(height: 3),
                        Text(
                          subtitle!,
                          style: EcomsbdType.caption.copyWith(
                            color: EcomsbdColors.muted,
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
                if (trailing != null) ...<Widget>[
                  const SizedBox(width: EcomsbdSpacing.xs),
                  trailing!,
                ],
              ],
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            child,
          ],
        ),
      ),
    );
  }
}
