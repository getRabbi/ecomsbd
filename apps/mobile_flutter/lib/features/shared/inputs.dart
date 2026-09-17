import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../design/glass.dart';
import '../../design/tokens.dart';

/// Form controls shared by the commerce screens.
///
/// Kept together so a text field on the product form and one on the order form
/// are the same control, not two that drifted apart.

/// A labelled text field in the app's style.
class LabelledField extends StatelessWidget {
  const LabelledField({
    required this.label,
    required this.controller,
    super.key,
    this.hint,
    this.keyboardType,
    this.inputFormatters,
    this.validator,
    this.onChanged,
    this.maxLines = 1,
    this.enabled = true,
    this.obscureText = false,
  });

  final String label;
  final TextEditingController controller;
  final String? hint;
  final TextInputType? keyboardType;
  final List<TextInputFormatter>? inputFormatters;
  final String? Function(String?)? validator;
  final ValueChanged<String>? onChanged;
  final int maxLines;
  final bool enabled;

  /// Hide the value as it is typed. For a credential the seller is copying in
  /// from a courier's panel: it is shoulder-surfable otherwise, and it is the
  /// one thing on that form that never comes back once saved.
  final bool obscureText;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Text(
          label.toUpperCase(),
          style: EcomsbdType.eyebrow.copyWith(color: EcomsbdColors.muted2),
        ),
        const SizedBox(height: 4),
        TextFormField(
          controller: controller,
          keyboardType: keyboardType,
          inputFormatters: inputFormatters,
          validator: validator,
          onChanged: onChanged,
          maxLines: maxLines,
          enabled: enabled,
          obscureText: obscureText,
          style: EcomsbdType.body,
          decoration: InputDecoration(
            hintText: hint,
            hintStyle: EcomsbdType.body.copyWith(color: EcomsbdColors.muted2),
            filled: true,
            fillColor: EcomsbdColors.miniTile,
            isDense: true,
            contentPadding: const EdgeInsets.symmetric(
              horizontal: 12,
              vertical: 13,
            ),
            border: const OutlineInputBorder(
              borderRadius: EcomsbdRadii.cardSmall,
              borderSide: BorderSide.none,
            ),
            enabledBorder: const OutlineInputBorder(
              borderRadius: EcomsbdRadii.cardSmall,
              borderSide: BorderSide(color: EcomsbdColors.stroke),
            ),
            focusedBorder: const OutlineInputBorder(
              borderRadius: EcomsbdRadii.cardSmall,
              borderSide: BorderSide(color: EcomsbdColors.orange),
            ),
          ),
        ),
      ],
    );
  }
}

/// Shared search input.
class CommerceSearchField extends StatelessWidget {
  const CommerceSearchField({
    required this.controller,
    required this.hint,
    required this.onChanged,
    super.key,
    this.autofocus = false,
  });

  final TextEditingController controller;
  final String hint;
  final ValueChanged<String> onChanged;

  /// Opens the keyboard on arrival, for a screen that exists only to search.
  final bool autofocus;

  @override
  Widget build(BuildContext context) {
    return GlassSurface(
      borderRadius: EcomsbdRadii.round,
      fill: EcomsbdColors.glassStrong,
      shadows: EcomsbdShadows.soft,
      padding: const EdgeInsets.symmetric(horizontal: EcomsbdSpacing.md),
      child: TextField(
        controller: controller,
        autofocus: autofocus,
        onChanged: onChanged,
        textInputAction: TextInputAction.search,
        style: EcomsbdType.body,
        decoration: InputDecoration(
          hintText: hint,
          hintStyle: EcomsbdType.body.copyWith(color: EcomsbdColors.muted2),
          // The pill is the field. The theme's outline and fill have to be
          // cleared too, or they draw a second box inside the pill.
          border: InputBorder.none,
          enabledBorder: InputBorder.none,
          focusedBorder: InputBorder.none,
          filled: false,
          icon: const Icon(
            Icons.search_rounded,
            size: 19,
            color: EcomsbdColors.muted,
          ),
          isDense: true,
          contentPadding: const EdgeInsets.symmetric(vertical: 14),
        ),
      ),
    );
  }
}

/// A pill toggle matching the prototype's filter chips.
class FilterToggle extends StatelessWidget {
  const FilterToggle({
    required this.label,
    required this.selected,
    required this.onChanged,
    super.key,
  });

  final String label;
  final bool selected;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      selected: selected,
      child: Material(
        color: selected ? EcomsbdColors.orange : Colors.white,
        borderRadius: EcomsbdRadii.round,
        child: InkWell(
          onTap: () => onChanged(!selected),
          borderRadius: EcomsbdRadii.round,
          child: Container(
            constraints: const BoxConstraints(
              minHeight: EcomsbdTouch.minTarget,
            ),
            padding: const EdgeInsets.symmetric(horizontal: 14),
            decoration: BoxDecoration(
              borderRadius: EcomsbdRadii.round,
              border: Border.all(
                color: selected ? EcomsbdColors.orange : EcomsbdColors.stroke,
              ),
            ),
            // widthFactor keeps the chip as wide as its label; Container's
            // own `alignment` expanded it to the full width a Wrap offers.
            child: Align(
              widthFactor: 1,
              child: Text(
                label,
                style: EcomsbdType.chip.copyWith(
                  color: selected ? Colors.white : EcomsbdColors.ink,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
