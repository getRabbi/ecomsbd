import 'package:flutter/material.dart';

import '../../core/money.dart';
import '../tokens.dart';

/// Semantic tone shared by every status surface.
///
/// Master spec section 122: state is never communicated by colour alone. Every
/// component in this file renders a glyph or a word alongside the tone, so the
/// meaning survives greyscale, low contrast and colour-blindness.
enum Tone { good, warning, bad, info, neutral }

extension ToneStyle on Tone {
  Color get ink => switch (this) {
    Tone.good => EcomsbdColors.green,
    Tone.warning => EcomsbdColors.amber,
    Tone.bad => EcomsbdColors.red,
    Tone.info => EcomsbdColors.blue,
    Tone.neutral => EcomsbdColors.neutralInk,
  };

  Color get surface => switch (this) {
    Tone.good => EcomsbdColors.greenSoft,
    Tone.warning => EcomsbdColors.amberSoft,
    Tone.bad => EcomsbdColors.redSoft,
    Tone.info => EcomsbdColors.blueSoft,
    Tone.neutral => EcomsbdColors.neutralSoft,
  };

  IconData get icon => switch (this) {
    Tone.good => Icons.check_circle_outline,
    Tone.warning => Icons.schedule,
    Tone.bad => Icons.error_outline,
    Tone.info => Icons.info_outline,
    Tone.neutral => Icons.remove_circle_outline,
  };
}

/// `.state` — a compact status pill.
class StatusChip extends StatelessWidget {
  const StatusChip({
    required this.label,
    super.key,
    this.tone = Tone.neutral,
    this.icon,
    this.showIcon = true,
  });

  final String label;
  final Tone tone;
  final IconData? icon;
  final bool showIcon;

  @override
  Widget build(BuildContext context) {
    // One semantics node carrying the label, with the icon and text excluded
    // beneath it. Without `container`/`ExcludeSemantics` a screen reader
    // announces the same word twice.
    return Semantics(
      label: label,
      container: true,
      child: ExcludeSemantics(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: tone.surface,
            borderRadius: EcomsbdRadii.round,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              if (showIcon) ...<Widget>[
                Icon(icon ?? tone.icon, size: 13, color: tone.ink),
                const SizedBox(width: 5),
              ],
              Flexible(
                child: Text(
                  label,
                  style: EcomsbdType.chip.copyWith(color: tone.ink),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Order-risk banding (master spec section 6).
///
/// The wording is about the *order*, never the person: master spec section 130
/// forbids "fraud", "scammer" or any person-level label.
enum RiskLevel { low, medium, high, unknown }

class RiskBadge extends StatelessWidget {
  const RiskBadge({required this.level, super.key, this.compact = false});

  final RiskLevel level;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final (label, tone) = switch (level) {
      RiskLevel.low => ('Low risk', Tone.good),
      RiskLevel.medium => ('Medium risk', Tone.warning),
      RiskLevel.high => ('High risk', Tone.bad),
      RiskLevel.unknown => ('No history', Tone.neutral),
    };
    return StatusChip(
      label: compact ? label.split(' ').first : label,
      tone: tone,
      icon: Icons.shield_outlined,
    );
  }
}

/// A courier provider, shown with its connection state.
///
/// The state comes from the provider's verified capability manifest, so an
/// unverified integration reads as "manual only" rather than as broken
/// (master spec section 75).
enum ProviderState { connected, notConnected, manualOnly, degraded }

class ProviderBadge extends StatelessWidget {
  const ProviderBadge({required this.provider, required this.state, super.key});

  final String provider;
  final ProviderState state;

  @override
  Widget build(BuildContext context) {
    final (label, tone) = switch (state) {
      ProviderState.connected => ('Connected', Tone.good),
      ProviderState.notConnected => ('Not connected', Tone.neutral),
      ProviderState.manualOnly => ('Manual only', Tone.info),
      ProviderState.degraded => ('Degraded', Tone.warning),
    };
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Container(
          width: 34,
          height: 34,
          alignment: Alignment.center,
          decoration: const BoxDecoration(
            color: EcomsbdColors.rowIconBackground,
            borderRadius: EcomsbdRadii.cardSmall,
          ),
          child: Text(
            provider.characters.first.toUpperCase(),
            style: EcomsbdType.bodyStrong.copyWith(
              color: EcomsbdColors.rowIconInk,
            ),
          ),
        ),
        const SizedBox(width: EcomsbdSpacing.sm),
        Text(provider, style: EcomsbdType.bodyStrong),
        const SizedBox(width: EcomsbdSpacing.xs),
        StatusChip(label: label, tone: tone, showIcon: false),
      ],
    );
  }
}

/// Whether a displayed figure is measured or inferred.
///
/// Master spec sections 85 and 135: an estimated profit must never be shown as
/// though it were exact. This badge is how the difference is stated, and the
/// UI is expected to carry it anywhere a value is not fully settled.
enum DataQuality { actual, estimated, missing, unreconciled }

class DataQualityBadge extends StatelessWidget {
  const DataQualityBadge({required this.quality, super.key, this.detail});

  final DataQuality quality;

  /// What is missing, e.g. `ad cost missing`.
  final String? detail;

  @override
  Widget build(BuildContext context) {
    final (label, tone, icon) = switch (quality) {
      DataQuality.actual => ('Actual', Tone.good, Icons.verified_outlined),
      DataQuality.estimated => ('Estimated', Tone.warning, Icons.trending_up),
      DataQuality.missing => ('Missing cost', Tone.bad, Icons.help_outline),
      DataQuality.unreconciled => (
        'Unreconciled',
        Tone.info,
        Icons.sync_problem_outlined,
      ),
    };
    return StatusChip(
      label: detail == null ? label : '$label · $detail',
      tone: tone,
      icon: icon,
    );
  }
}

/// Renders an amount with the right emphasis and sign treatment.
///
/// Uses tabular figures so stacked amounts align, and marks an estimate with a
/// leading `~` rather than presenting it as exact.
class MoneyText extends StatelessWidget {
  const MoneyText(
    this.amount, {
    super.key,
    this.style,
    this.compact = false,
    this.signed = false,
    this.estimated = false,
    this.colorBySign = false,
  });

  final Money amount;
  final TextStyle? style;
  final bool compact;
  final bool signed;
  final bool estimated;

  /// Colour negative amounts red and positive green. Off by default: most
  /// figures in this app are simply balances, not gains or losses.
  final bool colorBySign;

  @override
  Widget build(BuildContext context) {
    final text = compact
        ? amount.formatCompact()
        : amount.format(signed: signed);
    final display = estimated ? '~$text' : text;

    Color? color;
    if (colorBySign) {
      color = amount.isNegative
          ? EcomsbdColors.red
          : (amount.isZero ? null : EcomsbdColors.green);
    }

    return Semantics(
      label: estimated ? '$display, estimated' : display,
      excludeSemantics: true,
      child: Text(
        display,
        style: (style ?? EcomsbdType.money).copyWith(color: color),
      ),
    );
  }
}
