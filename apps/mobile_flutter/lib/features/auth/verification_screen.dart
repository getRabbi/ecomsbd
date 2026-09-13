import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../design/tokens.dart';
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

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _resend(String email) async {
    final ok = await ref
        .read(authControllerProvider.notifier)
        .resendVerification(email);
    if (!mounted || !ok) return;
    setState(() {
      _resent = true;
      _remaining = 30;
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
      title: _verified ? 'Email verified' : 'Verify your email',
      children: [
        if (_verified)
          const Text(
            'Your email address is verified. You can continue to ecomsbd.',
          )
        else if (widget.token != null)
          FilledButton(
            onPressed: state.isBusy ? null : _verify,
            child: const Text('Verify email'),
          )
        else ...[
          Text(
            'Check ${email ?? 'your email address'} for the confirmation link. Check your spam folder too. If it does not arrive, try resending.',
          ),
          if (email != null) ...[
            const SizedBox(height: EcomsbdSpacing.md),
            OutlinedButton(
              onPressed: state.isBusy || _remaining > 0
                  ? null
                  : () => _resend(email),
              child: Text(
                _remaining > 0
                    ? 'Resend in $_remaining seconds'
                    : 'Resend verification email',
              ),
            ),
          ],
          if (_resent)
            const Text(
              'If verification is still pending, a new link will arrive in your inbox.',
            ),
          if (state.isSignedIn)
            const Text(
              'You can continue setting up your shop while you verify your email.',
            ),
        ],
        if (state.error != null) ...[
          const SizedBox(height: EcomsbdSpacing.md),
          Semantics(
            liveRegion: true,
            child: Text(
              authErrorMessage(state.error!),
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
            state.isSignedIn ? 'Continue to shop' : 'Back to Sign In',
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
      title: 'Choose your shop',
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
        if (state.error != null) Text(authErrorMessage(state.error!)),
        TextButton(
          onPressed: state.isBusy
              ? null
              : () async {
                  await ref.read(authControllerProvider.notifier).signOut();
                },
          child: const Text('Sign out'),
        ),
      ],
    );
  }
}
