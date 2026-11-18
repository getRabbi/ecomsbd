import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../core/env.dart';
import '../../data/auth/auth_controller.dart';
import '../../design/theme.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import 'intake_native.dart';

/// The quick-order sheet behind **Create** on the heads-up (V4 WP1.4).
///
/// The seller's tap is the consent (rule L4): the screen sends the buffered
/// chat through `POST /v1/intake/captures/manual` as soon as it opens and
/// shows what became of it. WP1.5 turns this into the full order sheet
/// (fields, product match, COD, courier).
///
/// [standalone] is the translucent sheet on its own engine; otherwise the
/// screen is a route inside the app (when the app was already open).
/// The seller's sign-in stage, as this screen sees it (overridable in tests).
final quickOrderAuthStageProvider = Provider<AuthStage>(
  (ref) => ref.watch(authControllerProvider).stage,
);

class QuickOrderScreen extends ConsumerStatefulWidget {
  const QuickOrderScreen({super.key, this.standalone = false});

  final bool standalone;

  @override
  ConsumerState<QuickOrderScreen> createState() => _QuickOrderScreenState();
}

enum _Phase { loading, nothing, expired, sending, created, failed }

class _QuickOrderScreenState extends ConsumerState<QuickOrderScreen> {
  _Phase _phase = _Phase.loading;
  QuickOrderCapture? _capture;
  Map<String, dynamic>? _reply;
  ApiError? _error;
  String? _sentFor;
  StreamSubscription<IntakeEvent>? _events;

  @override
  void initState() {
    super.initState();
    final native = ref.read(intakeNativeProvider);
    // Another Create tap while this screen is open: show the new chat.
    _events = native.events.listen((_) => _load());
    _load();
  }

  @override
  void dispose() {
    _events?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final capture = await ref.read(intakeNativeProvider).takeQuickOrder();
    if (!mounted) return;
    setState(() {
      _capture = capture;
      _reply = null;
      _error = null;
      _phase = capture == null
          ? _Phase.nothing
          : capture.expired
          ? _Phase.expired
          : _Phase.sending;
    });
    _sendWhenSignedIn();
  }

  /// One upload per tap; the key makes a retry of the same buffer a replay.
  void _sendWhenSignedIn() {
    final capture = _capture;
    if (capture == null || capture.expired) return;
    if (ref.read(quickOrderAuthStageProvider) != AuthStage.ready) return;
    final key = 'qo-${capture.conversationId}-${capture.messages.length}';
    if (_sentFor == key && _phase != _Phase.failed) return;
    _sentFor = key;
    unawaited(_send(capture, key));
  }

  Future<void> _send(QuickOrderCapture capture, String key) async {
    setState(() => _phase = _Phase.sending);
    try {
      final reply = await ref
          .read(apiClientProvider)
          .post(
            '/intake/captures/manual',
            body: capture.manualCaptureBody(),
            idempotencyKey: key,
          );
      if (!mounted) return;
      setState(() {
        _reply = reply;
        _phase = _Phase.created;
      });
    } on ApiError catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error;
        _phase = _Phase.failed;
      });
    }
  }

  Future<void> _close() async {
    final capture = _capture;
    if (capture != null) {
      await ref
          .read(intakeNativeProvider)
          .finishQuickOrder(
            conversationId: capture.conversationId,
            created: _phase == _Phase.created,
          );
    }
    if (!widget.standalone && mounted) {
      unawaited(Navigator.of(context).maybePop());
    }
  }

  @override
  Widget build(BuildContext context) {
    ref.listen<AuthStage>(quickOrderAuthStageProvider, (_, next) {
      if (next == AuthStage.ready) _sendWhenSignedIn();
    });
    final content = _SheetBody(
      phase: _phase,
      capture: _capture,
      reply: _reply,
      error: _error,
      auth: ref.watch(quickOrderAuthStageProvider),
      standalone: widget.standalone,
      onClose: _close,
      onRetry: () {
        final capture = _capture;
        if (capture != null && _sentFor != null) {
          unawaited(_send(capture, _sentFor!));
        }
      },
      onOpenApp: () => ref.read(intakeNativeProvider).openApp(),
    );
    if (!widget.standalone) {
      return Scaffold(
        appBar: AppBar(title: Text(context.tr('intake.title'))),
        body: SafeArea(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(16),
            child: content,
          ),
        ),
      );
    }
    // Translucent: the chat app stays visible behind a scrim; tap it to close.
    return Material(
      type: MaterialType.transparency,
      child: Stack(
        children: [
          Positioned.fill(
            child: GestureDetector(
              key: const ValueKey('qo.scrim'),
              onTap: _close,
              child: const ColoredBox(color: Color(0x66000000)),
            ),
          ),
          Align(
            alignment: Alignment.bottomCenter,
            child: SafeArea(
              child: ConstrainedBox(
                constraints: BoxConstraints(
                  maxHeight: MediaQuery.sizeOf(context).height * 0.8,
                ),
                child: Card(
                  margin: const EdgeInsets.all(8),
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.all(16),
                    child: content,
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _SheetBody extends StatelessWidget {
  const _SheetBody({
    required this.phase,
    required this.capture,
    required this.reply,
    required this.error,
    required this.auth,
    required this.standalone,
    required this.onClose,
    required this.onRetry,
    required this.onOpenApp,
  });

  final _Phase phase;
  final QuickOrderCapture? capture;
  final Map<String, dynamic>? reply;
  final ApiError? error;
  final AuthStage auth;
  final bool standalone;
  final VoidCallback onClose;
  final VoidCallback onRetry;
  final VoidCallback onOpenApp;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final capture = this.capture;
    final sender = capture?.senderDisplay ?? capture?.conversationKey ?? '';
    final summary = [?capture?.maskedPhone, ?capture?.district].join(' · ');
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (capture != null && !capture.expired) ...[
          Text(
            context.tr('qo.title', {'sender': sender}),
            key: const ValueKey('qo.title'),
            style: theme.textTheme.titleMedium,
          ),
          if (summary.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(summary, style: theme.textTheme.bodySmall),
            ),
          const SizedBox(height: 12),
          Text(context.tr('qo.messages'), style: theme.textTheme.labelLarge),
          for (final message in capture.messages)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(message.text),
            ),
          const SizedBox(height: 16),
        ],
        ..._status(context, theme),
      ],
    );
  }

  List<Widget> _status(BuildContext context, ThemeData theme) {
    final close = TextButton(
      key: const ValueKey('qo.close'),
      onPressed: onClose,
      child: Text(context.tr('qo.close')),
    );
    switch (phase) {
      case _Phase.loading:
        return const [Center(child: CircularProgressIndicator())];
      case _Phase.nothing:
        return [Text(context.tr('qo.nothing')), close];
      case _Phase.expired:
        return [
          Text(context.tr('qo.expired'), key: const ValueKey('qo.expired')),
          close,
        ];
      case _Phase.sending:
      case _Phase.failed:
      case _Phase.created:
        break;
    }
    if (auth == AuthStage.restoring) {
      return const [Center(child: CircularProgressIndicator())];
    }
    if (auth != AuthStage.ready) {
      return [
        Text(context.tr('qo.signIn'), key: const ValueKey('qo.signIn')),
        if (standalone)
          FilledButton(
            onPressed: onOpenApp,
            child: Text(context.tr('qo.openApp')),
          ),
        close,
      ];
    }
    if (phase == _Phase.sending) {
      return [
        const Center(child: CircularProgressIndicator()),
        const SizedBox(height: 8),
        Text(context.tr('qo.sending'), textAlign: TextAlign.center),
      ];
    }
    if (phase == _Phase.failed) {
      final bn = context.strings.locale == AppLocale.bn;
      final error = this.error;
      return [
        Text(
          error == null
              ? context.tr('qo.failed')
              : (bn ? error.messageBn : error.messageEn),
          key: const ValueKey('qo.failed'),
          style: TextStyle(color: theme.colorScheme.error),
        ),
        if (error == null || error.retryable)
          FilledButton(
            key: const ValueKey('qo.retry'),
            onPressed: onRetry,
            child: Text(context.tr('qo.retry')),
          ),
        close,
      ];
    }
    final draft = reply?['draft'];
    final warnings = draft is Map
        ? (draft['warnings'] as List<dynamic>? ?? const [])
        : const [];
    return [
      Text(context.tr('qo.created'), key: const ValueKey('qo.created')),
      if (warnings.contains('DUPLICATE_ORDER'))
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Text(
            context.tr('qo.duplicate'),
            key: const ValueKey('qo.duplicate'),
          ),
        ),
      if (reply?['attention_id'] != null)
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Text(context.tr('qo.attention')),
        ),
      const SizedBox(height: 8),
      FilledButton(
        key: const ValueKey('qo.done'),
        onPressed: onClose,
        child: Text(context.tr('qo.done')),
      ),
    ];
  }
}

/// The app on the quick-order sheet's own engine (entry point `quickOrderMain`).
class QuickOrderApp extends ConsumerWidget {
  const QuickOrderApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final locale = ref.watch(localeProvider);
    return MaterialApp(
      title: Env.appName,
      debugShowCheckedModeBanner: false,
      color: Colors.transparent,
      theme: buildEcomsbdTheme().copyWith(
        scaffoldBackgroundColor: Colors.transparent,
      ),
      themeMode: ThemeMode.light,
      locale: locale.locale,
      supportedLocales: const <Locale>[Locale('bn'), Locale('en')],
      localizationsDelegates: const <LocalizationsDelegate<Object>>[
        AppStrings.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      // Native sets the initial route `/quick-order`; there is only one screen.
      onGenerateInitialRoutes: (_) => <Route<void>>[
        PageRouteBuilder<void>(
          opaque: false,
          pageBuilder: (_, _, _) => const QuickOrderScreen(standalone: true),
        ),
      ],
      onGenerateRoute: (_) => PageRouteBuilder<void>(
        opaque: false,
        pageBuilder: (_, _, _) => const QuickOrderScreen(standalone: true),
      ),
    );
  }
}
