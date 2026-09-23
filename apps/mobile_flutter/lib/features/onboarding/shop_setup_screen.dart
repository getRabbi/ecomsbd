import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/pills.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/language_picker.dart';
import '../shared/responsive.dart';

/// Shop onboarding.
///
/// Master spec section 4: install to shop created to first order in under ten
/// minutes. Only the shop name is required; the pickup address helps but does
/// not block, and courier linking is explicitly skippable — manual courier mode
/// is a first-class path, not a degraded one (master spec section 55).
class ShopSetupScreen extends ConsumerStatefulWidget {
  const ShopSetupScreen({required this.onComplete, super.key});

  final VoidCallback onComplete;

  @override
  ConsumerState<ShopSetupScreen> createState() => _ShopSetupScreenState();
}

class _ShopSetupScreenState extends ConsumerState<ShopSetupScreen> {
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  final TextEditingController _name = TextEditingController();
  final TextEditingController _contact = TextEditingController();
  final TextEditingController _phone = TextEditingController();
  final TextEditingController _address = TextEditingController();
  final TextEditingController _district = TextEditingController();
  final TextEditingController _area = TextEditingController();

  String _category = 'CLOTHING';

  /// The API's own category values. These are contract, not copy: only the
  /// label the seller reads is translated, keyed off the value.
  static const List<String> _categories = <String>[
    'CLOTHING',
    'ELECTRONICS',
    'COSMETICS',
    'FOOD',
    'HOME',
    'JEWELLERY',
    'BABY',
    'BOOKS',
    'OTHER',
  ];

  @override
  void dispose() {
    _name.dispose();
    _contact.dispose();
    _phone.dispose();
    _address.dispose();
    _district.dispose();
    _area.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!(_formKey.currentState?.validate() ?? false)) {
      return;
    }
    final controller = ref.read(authControllerProvider.notifier);
    // A retry after the completion request failed must not create another shop.
    final created =
        ref.read(authControllerProvider).tenantId != null ||
        await controller.createShop(<String, dynamic>{
          'name': _name.text.trim(),
          'business_category': _category,
          if (_contact.text.trim().isNotEmpty)
            'pickup_contact_name': _contact.text.trim(),
          if (_phone.text.trim().isNotEmpty) 'pickup_phone': _phone.text.trim(),
          if (_address.text.trim().isNotEmpty)
            'pickup_address': _address.text.trim(),
          if (_district.text.trim().isNotEmpty)
            'pickup_district': _district.text.trim(),
          if (_area.text.trim().isNotEmpty) 'pickup_area': _area.text.trim(),
        });
    if (!created) {
      return;
    }
    // Courier linking is deliberately not part of this step; the seller lands
    // on the dashboard and can connect a courier whenever they are ready.
    if (await controller.completeOnboarding() && mounted) {
      widget.onComplete();
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(authControllerProvider);

    return EcomsbdScaffold(
      child: ContentWidthLimit(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(
            EcomsbdSpacing.lg,
            EcomsbdSpacing.xl,
            EcomsbdSpacing.lg,
            EcomsbdSpacing.xl,
          ),
          children: <Widget>[
            const Row(
              children: <Widget>[
                BrandPill(),
                Spacer(),
                LanguageTogglePill(),
              ],
            ),
            const SizedBox(height: EcomsbdSpacing.xl),
            Text(context.tr('onboarding.title'), style: EcomsbdType.pageTitle),
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              context.tr('onboarding.subtitle'),
              style: EcomsbdType.body.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.lg),
            StrongGlassCard(
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    _Field(
                      label: context.tr('onboarding.shopName'),
                      controller: _name,
                      hint: 'Noor Fashion',
                      required: true,
                      validator: (value) {
                        if ((value ?? '').trim().length < 2) {
                          return context.tr('onboarding.shopNameRequired');
                        }
                        return null;
                      },
                    ),
                    const SizedBox(height: EcomsbdSpacing.md),
                    Text(
                      context.tr('onboarding.businessType'),
                      style: EcomsbdType.label,
                    ),
                    const SizedBox(height: EcomsbdSpacing.xs),
                    DropdownButtonFormField<String>(
                      initialValue: _category,
                      items: <DropdownMenuItem<String>>[
                        for (final category in _categories)
                          DropdownMenuItem<String>(
                            value: category,
                            child: Text(
                              context.tr('onboarding.category.$category'),
                              style: EcomsbdType.body,
                            ),
                          ),
                      ],
                      onChanged: (value) =>
                          setState(() => _category = value ?? 'OTHER'),
                    ),
                    const SizedBox(height: EcomsbdSpacing.md),
                    _Field(
                      label: context.tr('onboarding.pickupAddress'),
                      controller: _address,
                      hint: 'House 12, Road 3, Mirpur 10, Dhaka',
                      maxLines: 2,
                    ),
                    const SizedBox(height: EcomsbdSpacing.md),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Expanded(
                          child: _Field(
                            label: context.tr('onboarding.district'),
                            controller: _district,
                            hint: 'Dhaka',
                          ),
                        ),
                        const SizedBox(width: EcomsbdSpacing.sm),
                        Expanded(
                          child: _Field(
                            label: context.tr('onboarding.area'),
                            controller: _area,
                            hint: 'Mirpur',
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: EcomsbdSpacing.md),
                    _Field(
                      label: context.tr('onboarding.pickupPhone'),
                      controller: _phone,
                      hint: '01712345678',
                      keyboardType: TextInputType.phone,
                    ),
                    if (state.error != null) ...<Widget>[
                      const SizedBox(height: EcomsbdSpacing.md),
                      Text(
                        state.error!.displayMessage,
                        style: EcomsbdType.caption.copyWith(
                          color: EcomsbdColors.red,
                        ),
                      ),
                    ],
                    const SizedBox(height: EcomsbdSpacing.lg),
                    SizedBox(
                      width: double.infinity,
                      height: EcomsbdTouch.minTarget,
                      child: FilledButton(
                        onPressed: state.isBusy ? null : _submit,
                        style: FilledButton.styleFrom(
                          backgroundColor: EcomsbdColors.orange,
                          shape: const StadiumBorder(),
                          textStyle: EcomsbdType.label,
                        ),
                        child: state.isBusy
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: Colors.white,
                                ),
                              )
                            : Text(context.tr('onboarding.createShop')),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            const _ManualModeNote(),
          ],
        ),
      ),
    );
  }
}

class _Field extends StatelessWidget {
  const _Field({
    required this.label,
    required this.controller,
    this.hint,
    this.required = false,
    this.maxLines = 1,
    this.keyboardType,
    this.validator,
  });

  final String label;
  final TextEditingController controller;
  final String? hint;
  final bool required;
  final int maxLines;
  final TextInputType? keyboardType;
  final String? Function(String?)? validator;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Row(
          children: <Widget>[
            Text(label, style: EcomsbdType.label),
            if (!required) ...<Widget>[
              const SizedBox(width: EcomsbdSpacing.xs),
              Text(
                context.tr('common.optional'),
                style: EcomsbdType.caption.copyWith(
                  color: EcomsbdColors.muted2,
                ),
              ),
            ],
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.xs),
        TextFormField(
          controller: controller,
          maxLines: maxLines,
          keyboardType: keyboardType,
          validator: validator,
          style: EcomsbdType.body,
          decoration: InputDecoration(hintText: hint),
        ),
      ],
    );
  }
}

class _ManualModeNote extends StatelessWidget {
  const _ManualModeNote();

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Container(
            width: 34,
            height: 34,
            alignment: Alignment.center,
            decoration: const BoxDecoration(
              color: EcomsbdColors.greenSoft,
              borderRadius: EcomsbdRadii.cardSmall,
            ),
            child: const Icon(
              Icons.local_shipping_outlined,
              size: 18,
              color: EcomsbdColors.green,
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Expanded(
                      child: Text(
                        context.tr('onboarding.manualTitle'),
                        style: EcomsbdType.bodyStrong,
                      ),
                    ),
                    StatusChip(
                      label: context.tr('onboarding.manualMode'),
                      tone: Tone.good,
                    ),
                  ],
                ),
                const SizedBox(height: 3),
                Text(
                  context.tr('onboarding.manualBody'),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
