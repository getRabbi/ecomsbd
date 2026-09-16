import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../design/components/surfaces.dart';
import '../design/tokens.dart';
import 'app_locale.dart';
import 'app_strings.dart';

/// The language control, as a settings row.
///
/// Both languages are always written in their own script, so a seller who
/// cannot read the other one can still find theirs.
class LanguageSettingRow extends ConsumerWidget {
  const LanguageSettingRow({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final selected = ref.watch(localeProvider);

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              const Icon(
                Icons.translate_rounded,
                size: 20,
                color: EcomsbdColors.muted2,
              ),
              const SizedBox(width: EcomsbdSpacing.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      context.tr('language.title'),
                      style: EcomsbdType.bodyStrong,
                    ),
                    const SizedBox(height: 2),
                    Text(
                      context.tr('language.subtitle'),
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Row(
            children: <Widget>[
              for (final option in AppLocale.values) ...<Widget>[
                Expanded(
                  child: _LanguageOption(
                    option: option,
                    isSelected: option == selected,
                    onTap: () =>
                        ref.read(localeProvider.notifier).select(option),
                  ),
                ),
                if (option != AppLocale.values.last)
                  const SizedBox(width: EcomsbdSpacing.xs),
              ],
            ],
          ),
        ],
      ),
    );
  }
}

class _LanguageOption extends StatelessWidget {
  const _LanguageOption({
    required this.option,
    required this.isSelected,
    required this.onTap,
  });

  final AppLocale option;
  final bool isSelected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      selected: isSelected,
      label: option.nativeLabel,
      child: Material(
        color: isSelected ? EcomsbdColors.orange : Colors.white,
        borderRadius: EcomsbdRadii.round,
        child: InkWell(
          onTap: onTap,
          borderRadius: EcomsbdRadii.round,
          child: Container(
            constraints: const BoxConstraints(
              minHeight: EcomsbdTouch.minTarget,
            ),
            decoration: BoxDecoration(
              borderRadius: EcomsbdRadii.round,
              border: Border.all(
                color: isSelected
                    ? EcomsbdColors.orange
                    : EcomsbdColors.stroke,
              ),
            ),
            alignment: Alignment.center,
            child: Text(
              option.nativeLabel,
              style: EcomsbdType.label.copyWith(
                color: isSelected ? Colors.white : EcomsbdColors.ink,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// The language control as a small pill, for the auth screens.
///
/// A seller has to be able to pick their language *before* signing in —
/// otherwise the first screens they ever see are in a language they may not
/// read, and the choice is buried behind an account they have not made yet.
class LanguageTogglePill extends ConsumerWidget {
  const LanguageTogglePill({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final selected = ref.watch(localeProvider);
    final other = selected == AppLocale.bn ? AppLocale.en : AppLocale.bn;

    return Semantics(
      button: true,
      label: other.nativeLabel,
      child: Material(
        color: const Color(0xB8FAFCFD),
        borderRadius: EcomsbdRadii.round,
        child: InkWell(
          onTap: () => ref.read(localeProvider.notifier).select(other),
          borderRadius: EcomsbdRadii.round,
          child: Container(
            constraints: const BoxConstraints(
              minHeight: EcomsbdTouch.minTarget,
            ),
            padding: const EdgeInsets.symmetric(horizontal: 14),
            decoration: BoxDecoration(
              borderRadius: EcomsbdRadii.round,
              border: Border.all(color: EcomsbdColors.stroke),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                const Icon(
                  Icons.translate_rounded,
                  size: 17,
                  color: EcomsbdColors.muted,
                ),
                const SizedBox(width: 6),
                Text(other.nativeLabel, style: EcomsbdType.chip),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
