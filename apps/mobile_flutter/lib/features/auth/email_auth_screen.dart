import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/env.dart';
import '../../data/auth/provider_sign_in.dart';
import '../../design/components/pills.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
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
    EmailAuthMode.login => 'Welcome back',
    EmailAuthMode.register => 'Create account',
    EmailAuthMode.forgotPassword => 'Forgot password',
    EmailAuthMode.resetPassword => 'Reset password',
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
        ? (_forgot ? 'Check your email' : 'Password updated')
        : _title;
    return AuthPage(
      title: title,
      children: [
        if (_completed) ...[
          Text(
            _forgot
                ? 'If an account uses this email, you’ll receive a password reset link. Open it to choose a new password, then return here to sign in.'
                : 'Your password has been reset. Sign in with your new password.',
          ),
          const SizedBox(height: EcomsbdSpacing.lg),
          TextButton(
            onPressed: widget.onSignIn,
            child: const Text('Back to Sign In'),
          ),
        ] else if (invalidResetLink) ...[
          const Text('This reset link is incomplete. Request a new one.'),
          TextButton(
            onPressed: widget.onForgotPassword,
            child: const Text('Forgot password'),
          ),
        ] else ...[
          if (_login) ...[
            OutlinedButton(
              onPressed: state.isBusy
                  ? null
                  : () => _provider(SignInProvider.google),
              child: const Text('Continue with Google'),
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            OutlinedButton.icon(
              onPressed: state.isBusy
                  ? null
                  : () => _provider(SignInProvider.apple),
              icon: const Icon(Icons.apple),
              label: const Text('Continue with Apple'),
            ),
            const Padding(
              padding: EdgeInsets.symmetric(vertical: EcomsbdSpacing.md),
              child: Center(child: Text('or sign in with email')),
            ),
          ],
          if (_forgot) ...[
            const Text(
              'Enter your account email to request a password reset link.',
            ),
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
                      decoration: const InputDecoration(labelText: 'Email'),
                      validator: (value) =>
                          RegExp(
                            r'^[^@\s]+@[^@\s]+\.[^@\s]+$',
                          ).hasMatch(value?.trim() ?? '')
                          ? null
                          : 'Enter a valid email address.',
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
                        labelText: _reset ? 'New password' : 'Password',
                        helperText: _login ? null : 'Use 10–200 characters.',
                        suffixIcon: IconButton(
                          tooltip: _obscure ? 'Show password' : 'Hide password',
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
                          return 'Enter your password.';
                        }
                        if (!_login &&
                            (value.length < 10 || value.length > 200)) {
                          return 'Use 10–200 characters.';
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
                        decoration: const InputDecoration(
                          labelText: 'Confirm password',
                        ),
                        validator: (value) => value == _password.text
                            ? null
                            : 'Passwords do not match.',
                        onFieldSubmitted: (_) => _submit(),
                      ),
                    ],
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
                                ? 'Sign In'
                                : _forgot
                                ? 'Send reset link'
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
              child: const Text('Create account'),
            ),
            TextButton(
              onPressed: state.isBusy ? null : widget.onForgotPassword,
              child: const Text('Forgot password'),
            ),
            if (Env.phoneOtpLoginEnabled)
              TextButton(
                onPressed: state.isBusy ? null : widget.onPhoneLogin,
                child: const Text('Sign in with phone'),
              ),
          ] else
            TextButton(
              onPressed: state.isBusy ? null : widget.onSignIn,
              child: const Text('Back to Sign In'),
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
      child: ListView(
        padding: const EdgeInsets.all(EcomsbdSpacing.lg),
        children: [
          const Align(
            alignment: Alignment.centerLeft,
            child: BrandPill(showChevron: false),
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
}
