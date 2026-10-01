import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:sign_in_with_apple/sign_in_with_apple.dart';

import '../../app/providers.dart';
import '../../core/env.dart';
import '../../data/auth/provider_sign_in.dart';
import '../../design/components/pills.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/language_picker.dart';
import '../shared/responsive.dart';
import 'auth_error.dart';

enum EmailAuthMode { login, register, forgotPassword, resetPassword }

class EmailAuthScreen extends ConsumerStatefulWidget {
  const EmailAuthScreen({
    this.mode = EmailAuthMode.login,
    this.resetToken,
    this.onSignIn,
    this.onCreateAccount,
    this.onForgotPassword,
    this.onPhoneLogin,
    super.key,
  });

  final EmailAuthMode mode;
  final String? resetToken;
  final VoidCallback? onSignIn;
  final VoidCallback? onCreateAccount;
  final VoidCallback? onForgotPassword;
  final VoidCallback? onPhoneLogin;

  @override
  ConsumerState<EmailAuthScreen> createState() => _EmailAuthScreenState();
}

class _EmailAuthScreenState extends ConsumerState<EmailAuthScreen> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  bool _obscure = true;
  bool _completed = false;

  bool get _login => widget.mode == EmailAuthMode.login;
  bool get _forgot => widget.mode == EmailAuthMode.forgotPassword;
  bool get _reset => widget.mode == EmailAuthMode.resetPassword;

  String get _title => switch (widget.mode) {
    EmailAuthMode.login => context.tr('auth.welcomeBack'),
    EmailAuthMode.register => context.tr('auth.createAccount'),
    EmailAuthMode.forgotPassword => context.tr('auth.forgotPassword'),
    EmailAuthMode.resetPassword => context.tr('auth.resetPassword'),
  };

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _confirm.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    FocusScope.of(context).unfocus();
    final controller = ref.read(authControllerProvider.notifier);
    final ok = await switch (widget.mode) {
      EmailAuthMode.login => controller.login(_email.text, _password.text),
      EmailAuthMode.register => controller.register(
        _email.text,
        _password.text,
      ),
      EmailAuthMode.forgotPassword => controller.forgotPassword(_email.text),
      EmailAuthMode.resetPassword => controller.resetPassword(
        widget.resetToken!,
        _password.text,
      ),
    };
    if (mounted && ok && (_forgot || _reset)) setState(() => _completed = true);
  }

  Future<void> _provider(SignInProvider provider) async {
    FocusScope.of(context).unfocus();
    await ref
        .read(authControllerProvider.notifier)
        .signInWithProvider(provider);
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(authControllerProvider);
    final invalidResetLink = _reset && (widget.resetToken?.isEmpty ?? true);
    final title = _completed
        ? (_forgot
              ? context.tr('auth.checkYourEmail')
              : context.tr('auth.passwordUpdated'))
        : _title;
    return AuthPage(
      title: title,
      children: [
        if (_completed) ...[
          Text(
            _forgot
                ? context.tr('auth.resetSentBody')
                : context.tr('auth.resetDoneBody'),
          ),
          const SizedBox(height: EcomsbdSpacing.lg),
          TextButton(
            onPressed: widget.onSignIn,
            child: Text(context.tr('auth.backToSignIn')),
          ),
        ] else if (invalidResetLink) ...[
          Text(context.tr('auth.resetLinkIncomplete')),
          TextButton(
            onPressed: widget.onForgotPassword,
            child: Text(context.tr('auth.forgotPassword')),
          ),
        ] else ...[
          if (_login) ...[
            OutlinedButton(
              onPressed: state.isBusy
                  ? null
                  : () => _provider(SignInProvider.google),
              child: Text(context.tr('auth.continueWithGoogle')),
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            if (Env.appleSignInEnabled)
              // Apple's own button: App Review holds Sign in with Apple to
              // its artwork and wording, which a Material icon does not meet.
              SignInWithAppleButton(
                key: const Key('auth-apple'),
                text: context.tr('auth.continueWithApple'),
                style: SignInWithAppleButtonStyle.whiteOutlined,
                onPressed: () {
                  if (!state.isBusy) _provider(SignInProvider.apple);
                },
              ),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: EcomsbdSpacing.md),
              child: Center(child: Text(context.tr('auth.orSignInWithEmail'))),
            ),
          ],
          if (_forgot) ...[
            Text(context.tr('auth.forgotIntro')),
            const SizedBox(height: EcomsbdSpacing.md),
          ],
          AutofillGroup(
            child: Form(
              key: _form,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (!_reset) ...[
                    TextFormField(
                      key: const Key('auth-email'),
                      controller: _email,
                      enabled: !state.isBusy,
                      keyboardType: TextInputType.emailAddress,
                      autofillHints: const [AutofillHints.email],
                      autocorrect: false,
                      textCapitalization: TextCapitalization.none,
                      textInputAction: _forgot
                          ? TextInputAction.done
                          : TextInputAction.next,
                      decoration: InputDecoration(
                        labelText: context.tr('auth.email'),
                      ),
                      validator: (value) =>
                          RegExp(
                            r'^[^@\s]+@[^@\s]+\.[^@\s]+$',
                          ).hasMatch(value?.trim() ?? '')
                          ? null
                          : context.tr('auth.emailInvalid'),
                      onFieldSubmitted: _forgot ? (_) => _submit() : null,
                    ),
                    const SizedBox(height: EcomsbdSpacing.md),
                  ],
                  if (!_forgot) ...[
                    TextFormField(
                      key: const Key('auth-password'),
                      controller: _password,
                      enabled: !state.isBusy,
                      obscureText: _obscure,
                      autocorrect: false,
                      enableSuggestions: false,
                      autofillHints: [
                        _login
                            ? AutofillHints.password
                            : AutofillHints.newPassword,
                      ],
                      textInputAction: _login
                          ? TextInputAction.done
                          : TextInputAction.next,
                      decoration: InputDecoration(
                        labelText: _reset
                            ? context.tr('auth.newPassword')
                            : context.tr('auth.password'),
                        helperText: _login
                            ? null
                            : context.tr('auth.passwordRule'),
                        suffixIcon: IconButton(
                          tooltip: _obscure
                              ? context.tr('auth.showPassword')
                              : context.tr('auth.hidePassword'),
                          onPressed: () => setState(() => _obscure = !_obscure),
                          icon: Icon(
                            _obscure
                                ? Icons.visibility_outlined
                                : Icons.visibility_off_outlined,
                          ),
                        ),
                      ),
                      validator: (value) {
                        if (value == null || value.isEmpty) {
                          return context.tr('auth.enterPassword');
                        }
                        if (!_login &&
                            (value.length < 10 || value.length > 200)) {
                          return context.tr('auth.passwordRule');
                        }
                        return null;
                      },
                      onFieldSubmitted: _login ? (_) => _submit() : null,
                    ),
                    if (!_login) ...[
                      const SizedBox(height: EcomsbdSpacing.md),
                      TextFormField(
                        key: const Key('auth-confirm'),
                        controller: _confirm,
                        enabled: !state.isBusy,
                        obscureText: _obscure,
                        autocorrect: false,
                        enableSuggestions: false,
                        autofillHints: const [AutofillHints.newPassword],
                        textInputAction: TextInputAction.done,
                        decoration: InputDecoration(
                          labelText: context.tr('auth.confirmPassword'),
                        ),
                        validator: (value) => value == _password.text
                            ? null
                            : context.tr('auth.passwordsMismatch'),
                        onFieldSubmitted: (_) => _submit(),
                      ),
                    ],
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
                    if (authErrorDiagnostic(state.error!) case final code?) ...[
                      const SizedBox(height: EcomsbdSpacing.xs),
                      SelectableText(
                        context.tr('autherr.diagnostic', {'code': code}),
                        key: const Key('auth-error-diagnostic'),
                        style: EcomsbdType.caption.copyWith(
                          color: EcomsbdColors.muted,
                        ),
                      ),
                    ],
                  ],
                  const SizedBox(height: EcomsbdSpacing.lg),
                  FilledButton(
                    key: const Key('auth-submit'),
                    onPressed: state.isBusy ? null : _submit,
                    child: state.isBusy
                        ? const SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : Text(
                            _login
                                ? context.tr('auth.signIn')
                                : _forgot
                                ? context.tr('auth.sendResetLink')
                                : _title,
                          ),
                  ),
                ],
              ),
            ),
          ),
          if (_login) ...[
            TextButton(
              onPressed: state.isBusy ? null : widget.onCreateAccount,
              child: Text(context.tr('auth.createAccount')),
            ),
            TextButton(
              onPressed: state.isBusy ? null : widget.onForgotPassword,
              child: Text(context.tr('auth.forgotPassword')),
            ),
            if (Env.phoneOtpLoginEnabled)
              TextButton(
                onPressed: state.isBusy ? null : widget.onPhoneLogin,
                child: Text(context.tr('auth.signInWithPhone')),
              ),
          ] else
            TextButton(
              onPressed: state.isBusy ? null : widget.onSignIn,
              child: Text(context.tr('auth.backToSignIn')),
            ),
        ],
      ],
    );
  }
}

class AuthPage extends StatelessWidget {
  const AuthPage({required this.title, required this.children, super.key});
  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => EcomsbdScaffold(
    child: ContentWidthLimit(
      // LayoutBuilder measures the viewport the scaffold actually left us —
      // inside the status-bar safe area, and shorter again once the keyboard
      // is open. The auth block is centred in that, so it balances on a short
      // screen and on a tall one without a single fixed offset.
      child: LayoutBuilder(
        builder: (context, constraints) {
          const gutter = EcomsbdSpacing.lg;
          return SingleChildScrollView(
            padding: const EdgeInsets.all(gutter),
            child: ConstrainedBox(
              constraints: BoxConstraints(
                minHeight: constraints.maxHeight - gutter * 2,
              ),
              child: Column(
                // Centre the block; it grows past the viewport and scrolls
                // when the copy is long or the keyboard takes the bottom half.
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // Branding stays at the top of the auth content, with the
                  // language control beside it: a seller has to be able to
                  // switch language before they have an account.
                  const Row(
                    children: [BrandPill(), Spacer(), LanguageTogglePill()],
                  ),
                  const SizedBox(height: EcomsbdSpacing.xl),
                  Text(title, style: EcomsbdType.pageTitle),
                  const SizedBox(height: EcomsbdSpacing.lg),
                  StrongGlassCard(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: children,
                    ),
                  ),
                ],
              ),
            ),
          );
        },
      ),
    ),
  );
}
