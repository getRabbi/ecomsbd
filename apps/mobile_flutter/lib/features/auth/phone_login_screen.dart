import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/money.dart';
import '../../design/components/pills.dart';
import '../../design/components/surfaces.dart';
import '../../design/theme.dart';
import '../../design/tokens.dart';
import '../shared/responsive.dart';

/// Phone sign-in.
///
/// Master spec section 4: a seller never needs an email or a password. The
/// field accepts every shape section 8 lists — `01712345678`,
/// `+8801712345678`, Bangla digits, separators — and normalises before sending,
/// so a seller typing on a Bangla keyboard reaches the same account.
class PhoneLoginScreen extends ConsumerStatefulWidget {
  const PhoneLoginScreen({required this.onCodeSent, super.key});

  /// Called with the challenge id and the masked phone once a code is sent.
  final void Function(String challengeId, String maskedPhone, String? debugCode)
  onCodeSent;

  @override
  ConsumerState<PhoneLoginScreen> createState() => _PhoneLoginScreenState();
}

class _PhoneLoginScreenState extends ConsumerState<PhoneLoginScreen> {
  final TextEditingController _controller = TextEditingController();
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  /// Client-side check mirroring the server's rule. Deliberately a duplicate:
  /// it saves a round trip on an obvious typo, and the server remains the
  /// authority (master spec section 64).
  String? _validate(String? value) {
    final digits = normalizeDigits(value ?? '').replaceAll(RegExp(r'\D'), '');
    final national = digits.startsWith('880')
        ? '0${digits.substring(3)}'
        : digits;
    if (national.isEmpty) {
      return 'মোবাইল নম্বর দিন';
    }
    if (!RegExp(r'^01[3-9]\d{8}$').hasMatch(national)) {
      return 'সঠিক বাংলাদেশি নম্বর দিন। যেমন: 01712345678';
    }
    return null;
  }

  Future<void> _submit() async {
    if (!(_formKey.currentState?.validate() ?? false)) {
      return;
    }
    final challenge = await ref
        .read(authControllerProvider.notifier)
        .requestOtp(_controller.text);
    if (challenge != null && mounted) {
      widget.onCodeSent(
        challenge.challengeId,
        challenge.maskedPhone,
        challenge.debugCode,
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(authControllerProvider);

    return EcomsbdScaffold(
      overlayStyle: ecomsbdLightOverlay,
      child: ContentWidthLimit(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(
            EcomsbdSpacing.lg,
            EcomsbdSpacing.xxl,
            EcomsbdSpacing.lg,
            EcomsbdSpacing.xl,
          ),
          children: <Widget>[
            const Align(
              alignment: Alignment.centerLeft,
              child: BrandPill(showChevron: false),
            ),
            const SizedBox(height: EcomsbdSpacing.xxl),
            const Text(
              'আপনার COD-এর টাকা\nএক জায়গায়',
              style: EcomsbdType.pageTitle,
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            Text(
              'কুরিয়ারের কাছে কত টাকা আছে, কোনটা মেলেনি, আর প্রতিটি অর্ডারে '
              'আসলে কত লাভ — সব এক অ্যাপে।',
              style: EcomsbdType.body.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.xl),
            StrongGlassCard(
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    const Text('মোবাইল নম্বর', style: EcomsbdType.label),
                    const SizedBox(height: EcomsbdSpacing.xs),
                    TextFormField(
                      controller: _controller,
                      // Numeric keypad, per master spec section 52.
                      keyboardType: TextInputType.phone,
                      autofillHints: const <String>[
                        AutofillHints.telephoneNumber,
                      ],
                      textInputAction: TextInputAction.done,
                      validator: _validate,
                      onFieldSubmitted: (_) => _submit(),
                      style: EcomsbdType.bodyStrong,
                      inputFormatters: <TextInputFormatter>[
                        LengthLimitingTextInputFormatter(20),
                      ],
                      decoration: const InputDecoration(
                        hintText: '01712345678',
                        prefixIcon: Icon(Icons.phone_android_rounded, size: 20),
                      ),
                    ),
                    const SizedBox(height: EcomsbdSpacing.sm),
                    Text(
                      'বাংলা সংখ্যাও চলবে — ০১৭১২৩৪৫৬৭৮',
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                    if (state.error != null) ...<Widget>[
                      const SizedBox(height: EcomsbdSpacing.md),
                      _ErrorText(message: state.error!.displayMessage),
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
                            : const Text('কোড পাঠান'),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: EcomsbdSpacing.lg),
            Text(
              'কোড পাঠাতে আপনার নম্বর ব্যবহার করা হবে। কোনো পাসওয়ার্ড লাগবে না।',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}

class _ErrorText extends StatelessWidget {
  const _ErrorText({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      decoration: const BoxDecoration(
        color: EcomsbdColors.redSoft,
        borderRadius: EcomsbdRadii.cardSmall,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Icon(Icons.error_outline, size: 18, color: EcomsbdColors.red),
          const SizedBox(width: EcomsbdSpacing.xs),
          Expanded(
            child: Text(
              message,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
            ),
          ),
        ],
      ),
    );
  }
}
