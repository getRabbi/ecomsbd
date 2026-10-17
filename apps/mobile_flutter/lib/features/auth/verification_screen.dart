import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../data/auth/email_code.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import 'auth_error.dart';
import 'email_auth_screen.dart';

class VerificationScreen extends ConsumerStatefulWidget {
  const VerificationScreen({required this.onContinue, this.token, super.key});
  final VoidCallback onContinue;
  final String? token;

  @override
  ConsumerState<VerificationScreen> createState() => _VerificationScreenState();
}

class _VerificationScreenState extends ConsumerState<VerificationScreen> {
  bool _verified = false;
  bool _resent = false;
  int _remaining = 0;
  Timer? _timer;

  /// The emailed code. Lives in this field only: never stored or logged.
  final _code = TextEditingController();

  @override
  void dispose() {
    _timer?.cancel();
    _code.dispose();
    super.dispose();
  }

  Future<void> _verifyCode() async {
    if (!EmailCode.isComplete(_code.text)) return;
    final ok = await ref
        .read(authControllerProvider.notifier)
        .verifyEmailCode(_code.text);
    // On success the router moves on by itself, as after any sign-in.
    if (mounted && !ok) _code.clear();
  }

  Future<void> _resend(String email) async {
    final ok = await ref
        .read(authControllerProvider.notifier)
        .resendVerification(email);
    if (!mounted || !ok) return;
    setState(() {
      _resent = true;
      _remaining = EmailCode.resendCooldownSeconds;
    });
    _timer?.cancel();
    _timer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      setState(() => _remaining--);
      if (_remaining <= 0) timer.cancel();
    });
  }

  Future<void> _verify() async {
    final ok = await ref
        .read(authControllerProvider.notifier)
        .verifyEmail(widget.token!);
    if (mounted && ok) setState(() => _verified = true);
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(authControllerProvider);
    final email = state.verificationEmail;
    return AuthPage(
      title: _verified
          ? context.tr('auth.verifiedTitle')
          : context.tr('auth.verifyTitle'),
      children: [
        if (_verified)
          Text(context.tr('auth.verifiedBody'))
        else if (widget.token != null)
          FilledButton(
            onPressed: state.isBusy ? null : _verify,
            child: Text(context.tr('auth.verifyButton')),
          )
        else ...[
          Text(
            context.tr('auth.verifyCheckBody', <String, Object?>{
              'email': email ?? context.tr('auth.yourEmailAddress'),
            }),
          ),
          if (email != null) ...[
            const SizedBox(height: EcomsbdSpacing.md),
            AutofillGroup(
              child: TextField(
                controller: _code,
                keyboardType: TextInputType.number,
                autofillHints: const <String>[AutofillHints.oneTimeCode],
                inputFormatters: const <TextInputFormatter>[
                  EmailCodeFormatter(),
                ],
                maxLength: EmailCode.maxLength,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 22, letterSpacing: 6),
                enabled: !state.isBusy,
                decoration: InputDecoration(
                  labelText: context.tr('auth.codeLabel'),
                  helperText: context.tr('auth.codeHelp'),
                  helperMaxLines: 3,
                  counterText: '',
                ),
                onChanged: (_) => setState(() {}),
                onSubmitted: (_) => _verifyCode(),
              ),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            FilledButton(
              onPressed: state.isBusy || !EmailCode.isComplete(_code.text)
                  ? null
                  : _verifyCode,
              child: Text(context.tr('auth.verifyCodeButton')),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            OutlinedButton(
              onPressed: state.isBusy || _remaining > 0
                  ? null
                  : () => _resend(email),
              child: Text(
                _remaining > 0
                    ? context.tr('auth.resendIn', <String, Object?>{
                        'count': _remaining,
                      })
                    : context.tr('auth.resendVerification'),
              ),
            ),
          ],
          if (_resent) Text(context.tr('auth.resentNote')),
          if (state.isSignedIn) Text(context.tr('auth.continueSetupNote')),
        ],
        if (state.error != null) ...[
          const SizedBox(height: EcomsbdSpacing.md),
          Semantics(
            liveRegion: true,
            child: Text(
              authErrorMessage(context, state.error!),
              style: const TextStyle(color: EcomsbdColors.red),
            ),
          ),
        ],
        const SizedBox(height: EcomsbdSpacing.lg),
        TextButton(
          onPressed: state.isBusy
              ? null
              : () {
                  ref
                      .read(authControllerProvider.notifier)
                      .continueAfterVerification();
                  widget.onContinue();
                },
          child: Text(
            state.isSignedIn
                ? context.tr('auth.continueToShop')
                : context.tr('auth.backToSignIn'),
          ),
        ),
      ],
    );
  }
}

class SelectShopScreen extends ConsumerWidget {
  const SelectShopScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(authControllerProvider);
    return AuthPage(
      title: context.tr('auth.chooseShop'),
      children: [
        for (final shop in state.profile?.tenants ?? [])
          ListTile(
            title: Text(shop.name),
            trailing: const Icon(Icons.chevron_right),
            enabled: !state.isBusy,
            onTap: () async {
              await ref
                  .read(authControllerProvider.notifier)
                  .selectShop(shop.id);
            },
          ),
        if (state.error != null) Text(authErrorMessage(context, state.error!)),
        TextButton(
          onPressed: state.isBusy
              ? null
              : () async {
                  await ref.read(authControllerProvider.notifier).signOut();
                },
          child: Text(context.tr('common.signOut')),
        ),
      ],
    );
  }
}
