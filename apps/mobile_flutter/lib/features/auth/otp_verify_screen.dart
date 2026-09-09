import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:uuid/uuid.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../design/components/pills.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/responsive.dart';

/// OTP verification.
///
/// Master spec section 4: six digits, five minutes, five attempts. The screen
/// shows the remaining attempts the server reports rather than counting
/// locally, because the server's count is the one that is enforced.
class OtpVerifyScreen extends ConsumerStatefulWidget {
  const OtpVerifyScreen({
    required this.challengeId,
    required this.maskedPhone,
    required this.onVerified,
    required this.onChangeNumber,
    super.key,
    this.debugCode,
  });

  final String challengeId;
  final String maskedPhone;

  /// Present only when the server ran its development OTP provider.
  final String? debugCode;

  final VoidCallback onVerified;
  final VoidCallback onChangeNumber;

  @override
  ConsumerState<OtpVerifyScreen> createState() => _OtpVerifyScreenState();
}

class _OtpVerifyScreenState extends ConsumerState<OtpVerifyScreen> {
  final TextEditingController _controller = TextEditingController();
  Timer? _resendTimer;
  int _resendIn = 60;

  @override
  void initState() {
    super.initState();
    // Prefilled only in development, where the server echoed the code back.
    if (widget.debugCode != null) {
      _controller.text = widget.debugCode!;
    }
    _startResendCountdown();
  }

  @override
  void dispose() {
    _resendTimer?.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _startResendCountdown() {
    _resendTimer?.cancel();
    setState(() => _resendIn = 60);
    _resendTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      setState(() => _resendIn -= 1);
      if (_resendIn <= 0) {
        timer.cancel();
      }
    });
  }

  Future<Map<String, dynamic>> _describeDevice() async {
    final installId = await ref
        .read(tokenStoreProvider)
        .installId(() => const Uuid().v4());
    String? version;
    try {
      final info = await PackageInfo.fromPlatform();
      version = '${info.version}+${info.buildNumber}';
    } on Object {
      // Package metadata is unavailable in some test environments; the device
      // record is advisory, so sign-in continues without it.
      version = null;
    }
    return <String, dynamic>{
      'install_id': installId,
      'platform': 'ANDROID',
      if (version != null) 'app_version': version,
    };
  }

  Future<void> _verify() async {
    final code = _controller.text.trim();
    if (code.length < 4) {
      return;
    }
    final ok = await ref
        .read(authControllerProvider.notifier)
        .verifyOtp(
          challengeId: widget.challengeId,
          code: code,
          device: await _describeDevice(),
        );
    if (ok && mounted) {
      widget.onVerified();
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(authControllerProvider);
    final error = state.error;

    return EcomsbdScaffold(
      topBar: Padding(
        padding: const EdgeInsets.all(EcomsbdSpacing.md),
        child: Align(
          alignment: Alignment.centerLeft,
          child: GlassBackPill(onPressed: widget.onChangeNumber),
        ),
      ),
      child: ContentWidthLimit(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(
            EcomsbdSpacing.lg,
            EcomsbdTouch.minTarget + EcomsbdSpacing.xl,
            EcomsbdSpacing.lg,
            EcomsbdSpacing.xl,
          ),
          children: <Widget>[
            const Text('কোড দিন', style: EcomsbdType.pageTitle),
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              '${widget.maskedPhone} নম্বরে ৬ সংখ্যার কোড পাঠানো হয়েছে।',
              style: EcomsbdType.body.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.xl),
            StrongGlassCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  TextField(
                    controller: _controller,
                    keyboardType: TextInputType.number,
                    textAlign: TextAlign.center,
                    autofocus: true,
                    maxLength: 6,
                    // Lets Android's SMS one-time-code autofill populate the
                    // field without the app requesting inbox access
                    // (master spec section 125).
                    autofillHints: const <String>[AutofillHints.oneTimeCode],
                    inputFormatters: <TextInputFormatter>[
                      FilteringTextInputFormatter.digitsOnly,
                    ],
                    style: EcomsbdType.heroTitle.copyWith(letterSpacing: 8),
                    onSubmitted: (_) => _verify(),
                    decoration: const InputDecoration(
                      counterText: '',
                      hintText: '——————',
                    ),
                  ),
                  if (error != null) ...<Widget>[
                    const SizedBox(height: EcomsbdSpacing.md),
                    _OtpError(error: error),
                  ],
                  const SizedBox(height: EcomsbdSpacing.lg),
                  SizedBox(
                    width: double.infinity,
                    height: EcomsbdTouch.minTarget,
                    child: FilledButton(
                      onPressed: state.isBusy ? null : _verify,
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
                          : const Text('যাচাই করুন'),
                    ),
                  ),
                  const SizedBox(height: EcomsbdSpacing.sm),
                  Center(
                    child: TextButton(
                      onPressed: _resendIn > 0 ? null : widget.onChangeNumber,
                      style: TextButton.styleFrom(
                        minimumSize: const Size(0, EcomsbdTouch.minTarget),
                        foregroundColor: EcomsbdColors.orange,
                      ),
                      child: Text(
                        _resendIn > 0
                            ? 'আবার কোড চান $_resendIn সেকেন্ড পরে'
                            : 'নম্বর বদলান বা আবার কোড নিন',
                        style: EcomsbdType.label,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            if (widget.debugCode != null) ...<Widget>[
              const SizedBox(height: EcomsbdSpacing.md),
              _DevelopmentCodeNotice(code: widget.debugCode!),
            ],
          ],
        ),
      ),
    );
  }
}

class _OtpError extends StatelessWidget {
  const _OtpError({required this.error});

  final ApiError error;

  @override
  Widget build(BuildContext context) {
    final remaining = error.attemptsRemaining;
    return Container(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      decoration: const BoxDecoration(
        color: EcomsbdColors.redSoft,
        borderRadius: EcomsbdRadii.cardSmall,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              const Icon(
                Icons.error_outline,
                size: 18,
                color: EcomsbdColors.red,
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              Expanded(
                child: Text(
                  error.displayMessage,
                  style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
                ),
              ),
            ],
          ),
          if (remaining != null) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              'আর $remaining বার চেষ্টা করতে পারবেন।',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
            ),
          ],
        ],
      ),
    );
  }
}

/// Visible warning that the server is not sending real messages.
///
/// Shown only when the API echoed a code back, which it does exclusively in
/// local and test environments. Making it loud means a build pointed at a real
/// server can never look like this by accident.
class _DevelopmentCodeNotice extends StatelessWidget {
  const _DevelopmentCodeNotice({required this.code});

  final String code;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      decoration: BoxDecoration(
        color: EcomsbdColors.amberSoft,
        borderRadius: EcomsbdRadii.cardMedium,
        border: Border.all(color: EcomsbdColors.amber.withValues(alpha: 0.35)),
      ),
      child: Row(
        children: <Widget>[
          const Icon(Icons.warning_amber_rounded, color: EcomsbdColors.amber),
          const SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  'Development server: no SMS was sent',
                  style: EcomsbdType.bodyStrong.copyWith(
                    color: EcomsbdColors.amber,
                  ),
                ),
                Text(
                  'Code $code was returned by the API. This cannot happen '
                  'against a production server.',
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.amber,
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
