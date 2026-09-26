import 'package:flutter/material.dart';

import '../tokens.dart';
import 'badges.dart';
import 'surfaces.dart';

/// Building blocks of the final seller UI prototype
/// (`ecomsbd_final_ui_final.html`) that more than one screen draws.
///
/// Each class names the prototype selector it reproduces. Screens compose
/// these rather than restyling a card of their own, so Home, Orders, Inbox,
/// Money and More read as one system.

/// `.hero` / `.daily-card` — the dark, high-contrast summary panel.
class DarkPanel extends StatelessWidget {
  const DarkPanel({
    required this.child,
    super.key,
    this.colors = EcomsbdColors.heroGradient,
    this.radius = 26,
    this.padding = const EdgeInsets.all(18),
  });

  final Widget child;
  final List<Color> colors;
  final double radius;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: padding,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radius),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: colors,
          stops: const <double>[0, 0.68, 1],
        ),
        boxShadow: EcomsbdShadows.hero,
      ),
      child: child,
    );
  }
}

/// `.mini-stat` — one figure on a translucent tile inside a [DarkPanel].
class DarkMiniStat extends StatelessWidget {
  const DarkMiniStat({
    required this.value,
    required this.label,
    super.key,
    this.valueColor = Colors.white,
  });

  final String value;
  final String label;
  final Color valueColor;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 10),
      decoration: BoxDecoration(
        color: const Color(0x1AFFFFFF),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: const Color(0x1FFFFFFF)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(
              value,
              style: EcomsbdType.money.copyWith(
                fontSize: 16,
                fontWeight: FontWeight.w800,
                color: valueColor,
              ),
            ),
          ),
          const SizedBox(height: 2),
          Text(
            label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: EcomsbdType.caption.copyWith(
              fontSize: 10.5,
              color: const Color(0xFFCBD7E2),
            ),
          ),
        ],
      ),
    );
  }
}

/// `.row-icon` / `.attention-icon` — the rounded square behind a row's icon.
///
/// Peach and orange by default; a [tone] swaps in its semantic colours so a
/// risk or a payout gap is not dressed as a call to action.
class SoftIcon extends StatelessWidget {
  const SoftIcon({
    required this.icon,
    super.key,
    this.tone,
    this.size = 38,
    this.background,
    this.color,
  });

  final IconData icon;
  final Tone? tone;
  final double size;
  final Color? background;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: background ?? tone?.surface ?? EcomsbdColors.peach,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Icon(
        icon,
        size: size * 0.5,
        color: color ?? tone?.ink ?? EcomsbdColors.orange,
      ),
    );
  }
}

/// `.attention-item` — one compact, tappable row that needs the seller.
class AttentionTile extends StatelessWidget {
  const AttentionTile({
    required this.icon,
    required this.title,
    required this.detail,
    super.key,
    this.tone,
    this.onTap,
  });

  final IconData icon;
  final String title;
  final String detail;
  final Tone? tone;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white,
      borderRadius: EcomsbdRadii.row,
      child: InkWell(
        onTap: onTap,
        borderRadius: EcomsbdRadii.row,
        child: Container(
          constraints: const BoxConstraints(minHeight: 60),
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            borderRadius: EcomsbdRadii.row,
            border: Border.all(color: EcomsbdColors.line),
          ),
          child: Row(
            children: <Widget>[
              SoftIcon(icon: icon, tone: tone, size: 36),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(
                      title,
                      style: EcomsbdType.bodyStrong,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 1),
                    Text(
                      detail,
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              if (onTap != null)
                const Icon(
                  Icons.chevron_right_rounded,
                  color: EcomsbdColors.muted2,
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// `.group-title` — the uppercase label above a [ListCard].
class GroupTitle extends StatelessWidget {
  const GroupTitle(this.label, {super.key});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 20, 4, 8),
      child: Text(
        label.toUpperCase(),
        style: trackedFor(
          label,
          EcomsbdType.eyebrowWide.copyWith(color: EcomsbdColors.eyebrowInk),
        ),
      ),
    );
  }
}

/// `.list-card` — rows inside one white card, split by hairlines.
class ListCard extends StatelessWidget {
  const ListCard({required this.children, super.key});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: EdgeInsets.zero,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          for (var i = 0; i < children.length; i++) ...<Widget>[
            if (i > 0)
              const Divider(
                height: 1,
                thickness: 1,
                color: EcomsbdColors.lineSoft,
              ),
            children[i],
          ],
        ],
      ),
    );
  }
}

/// `.list-row` — icon, title, one quiet line, and a chevron or a status.
class ListCardRow extends StatelessWidget {
  const ListCardRow({
    required this.title,
    super.key,
    this.icon,
    this.leading,
    this.subtitle,
    this.trailing,
    this.onTap,
    this.enabled = true,
  });

  final IconData? icon;

  /// Replaces [icon] when the row needs its own mark (a courier logo).
  final Widget? leading;
  final String title;
  final String? subtitle;

  /// A status or a button; a chevron is drawn when this is null and the row
  /// is tappable.
  final Widget? trailing;
  final VoidCallback? onTap;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    final lead = leading ?? (icon == null ? null : SoftIcon(icon: icon!));
    return InkWell(
      onTap: enabled ? onTap : null,
      child: Opacity(
        opacity: enabled ? 1 : 0.55,
        child: Container(
          constraints: const BoxConstraints(minHeight: 62),
          padding: const EdgeInsets.symmetric(horizontal: 15, vertical: 11),
          child: Row(
            children: <Widget>[
              if (lead != null) ...<Widget>[lead, const SizedBox(width: 12)],
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
                      const SizedBox(height: 1),
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
                const SizedBox(width: 8),
                trailing!,
              ] else if (onTap != null && enabled)
                const Icon(
                  Icons.chevron_right_rounded,
                  color: EcomsbdColors.muted2,
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// `.kpi-line` — a label and its figure, one per line, in a flat card.
class KpiLine extends StatelessWidget {
  const KpiLine({
    required this.label,
    required this.value,
    super.key,
    this.valueColor,
    this.labelColor,
    this.isLast = false,
  });

  final String label;
  final String value;
  final Color? valueColor;
  final Color? labelColor;
  final bool isLast;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 10),
      decoration: BoxDecoration(
        border: isLast
            ? null
            : const Border(bottom: BorderSide(color: EcomsbdColors.lineSoft)),
      ),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Text(
              label,
              style: EcomsbdType.bodyStrong.copyWith(color: labelColor),
            ),
          ),
          const SizedBox(width: 8),
          Text(
            value,
            style: EcomsbdType.money.copyWith(
              fontWeight: valueColor == null
                  ? FontWeight.w600
                  : FontWeight.w800,
              color: valueColor ?? EcomsbdColors.muted,
            ),
          ),
        ],
      ),
    );
  }
}

/// `.alert-strip` — a problem with its one action, on a tinted strip.
class AlertStrip extends StatelessWidget {
  const AlertStrip({
    required this.icon,
    required this.title,
    required this.detail,
    super.key,
    this.tone = Tone.bad,
    this.actionLabel,
    this.onAction,
  });

  final IconData icon;
  final String title;
  final String detail;
  final Tone tone;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final (fill, border) = switch (tone) {
      Tone.bad => (EcomsbdColors.alertBadFill, EcomsbdColors.alertBadBorder),
      Tone.warning => (const Color(0xFFFFF9F2), const Color(0xFFFFE3C1)),
      _ => (Colors.white, EcomsbdColors.line),
    };
    return Material(
      color: fill,
      borderRadius: EcomsbdRadii.row,
      child: InkWell(
        onTap: onAction,
        borderRadius: EcomsbdRadii.row,
        child: Container(
          padding: const EdgeInsets.all(13),
          decoration: BoxDecoration(
            borderRadius: EcomsbdRadii.row,
            border: Border.all(color: border),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              SoftIcon(
                icon: icon,
                tone: tone,
                background: tone == Tone.bad
                    ? EcomsbdColors.alertBadIcon
                    : null,
              ),
              const SizedBox(width: 11),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(title, style: EcomsbdType.bodyStrong),
                    const SizedBox(height: 2),
                    Text(
                      detail,
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
              if (actionLabel != null) ...<Widget>[
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 8,
                  ),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(11),
                    border: Border.all(color: EcomsbdColors.line),
                  ),
                  child: Text(
                    actionLabel!,
                    style: EcomsbdType.chip.copyWith(
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// The courier's colour for its mark, as the prototype draws them.
Color courierBrandColor(String provider) =>
    switch (provider.toLowerCase().replaceAll(RegExp('[^a-z]'), '')) {
      'steadfast' => const Color(0xFF00A884),
      'pathao' => const Color(0xFFE74B3C),
      'redx' => const Color(0xFFE11D48),
      'paperfly' => const Color(0xFFE98B22),
      _ => EcomsbdColors.ink,
    };

/// `.courier-logo` / `.spend-logo` — the courier's initial on its colour.
class CourierMark extends StatelessWidget {
  const CourierMark({
    required this.provider,
    required this.name,
    super.key,
    this.size = 42,
  });

  final String provider;
  final String name;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: courierBrandColor(provider),
        borderRadius: BorderRadius.circular(size * 0.31),
      ),
      child: Text(
        name.isEmpty ? '?' : name.characters.first.toUpperCase(),
        style: EcomsbdType.bodyStrong.copyWith(
          color: Colors.white,
          fontWeight: FontWeight.w900,
          fontSize: size * 0.4,
        ),
      ),
    );
  }
}

/// `.spend-row` — a courier with what it cost and its outcome.
class SpendRow extends StatelessWidget {
  const SpendRow({
    required this.provider,
    required this.name,
    required this.detail,
    super.key,
    this.amount,
    this.amountCaption,
    this.onTap,
  });

  final String provider;
  final String name;
  final String detail;
  final String? amount;
  final String? amountCaption;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white,
      borderRadius: EcomsbdRadii.row,
      child: InkWell(
        onTap: onTap,
        borderRadius: EcomsbdRadii.row,
        child: Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            borderRadius: EcomsbdRadii.row,
            border: Border.all(color: EcomsbdColors.line),
          ),
          child: Row(
            children: <Widget>[
              CourierMark(provider: provider, name: name, size: 40),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(
                      name,
                      style: EcomsbdType.bodyStrong.copyWith(fontSize: 13.5),
                    ),
                    Text(
                      detail,
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              if (amount != null) ...<Widget>[
                const SizedBox(width: 8),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(amount!, style: EcomsbdType.money),
                    if (amountCaption != null)
                      Text(
                        amountCaption!,
                        style: EcomsbdType.caption.copyWith(
                          fontSize: 10.5,
                          color: EcomsbdColors.muted,
                        ),
                      ),
                  ],
                ),
              ] else if (onTap != null)
                const Icon(
                  Icons.chevron_right_rounded,
                  color: EcomsbdColors.muted2,
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// `.btn.primary` / `.btn.secondary` — the compact in-card buttons.
class CardButton extends StatelessWidget {
  const CardButton({
    required this.label,
    required this.onPressed,
    super.key,
    this.primary = true,
    this.icon,
  });

  final String label;
  final VoidCallback? onPressed;
  final bool primary;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final style = FilledButton.styleFrom(
      backgroundColor: primary
          ? EcomsbdColors.orange
          : EcomsbdColors.buttonSecondary,
      foregroundColor: primary ? Colors.white : EcomsbdColors.ink,
      minimumSize: const Size(0, 44),
      padding: const EdgeInsets.symmetric(horizontal: 14),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      textStyle: EcomsbdType.label.copyWith(fontWeight: FontWeight.w800),
      elevation: 0,
    );
    final text = Text(label, maxLines: 1, overflow: TextOverflow.ellipsis);
    return icon == null
        ? FilledButton(onPressed: onPressed, style: style, child: text)
        : FilledButton.icon(
            onPressed: onPressed,
            style: style,
            icon: Icon(icon, size: 17),
            label: text,
          );
  }
}
