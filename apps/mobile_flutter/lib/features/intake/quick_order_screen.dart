import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../core/env.dart';
import '../../data/auth/auth_controller.dart';
import '../../data/chat_orders/chat_orders.dart';
import '../../design/theme.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import 'intake_native.dart';
import 'quick_order_form.dart';

/// The quick-order sheet behind **Create** on the heads-up (V4 WP1.4) and
/// the Android share target (WP1.5).
///
/// The seller's tap (or share) is the consent (rule L4): the screen sends the
/// chat through `POST /v1/intake/captures/manual` as soon as it opens, then
/// shows the draft as an order form ([QuickOrderForm]): Create order, Save
/// draft (it stays in Chat orders), or "Aro message lagbe" to paste more of
/// the chat into the same draft.
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

enum _Phase { loading, nothing, expired, sending, review, ordered, failed }

class _QuickOrderScreenState extends ConsumerState<QuickOrderScreen> {
  _Phase _phase = _Phase.loading;
  QuickOrderCapture? _capture;
  Map<String, dynamic>? _reply;
  QuickOrderOutcome? _outcome;
  ApiError? _error;
  String? _sentFor;

  /// "Aro message lagbe" pastes sent from this sheet (part of their key).
  int _more = 0;
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
      _outcome = null;
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
        _phase = _Phase.review;
      });
    } on ApiError catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error;
        _phase = _Phase.failed;
      });
    }
  }

  /// Close the sheet. Once the server holds the chat (a draft, an order or
  /// a follow-up), the phone lets go of its copy.
  Future<void> _close() async {
    final capture = _capture;
    if (capture != null) {
      await ref
          .read(intakeNativeProvider)
          .finishQuickOrder(
            conversationId: capture.conversationId,
            created: _reply != null,
          );
    }
    if (!widget.standalone && mounted) {
      unawaited(Navigator.of(context).maybePop());
    }
  }

  void _ordered(QuickOrderOutcome outcome) => setState(() {
    _outcome = outcome;
    _phase = _Phase.ordered;
  });

  /// "Aro message lagbe": the seller pastes more of the chat (never read from
  /// the clipboard behind their back) and it joins the same draft.
  Future<void> _moreMessages() async {
    final capture = _capture;
    if (capture == null) return;
    final text = await showDialog<String>(
      context: context,
      builder: (_) => const _MoreMessagesDialog(),
    );
    if (text == null || text.trim().isEmpty || !mounted) return;
    _more += 1;
    setState(() => _phase = _Phase.sending);
    try {
      final reply = await ref
          .read(apiClientProvider)
          .post(
            '/intake/captures/manual',
            body: capture.moreMessagesBody(text.trim(), DateTime.now()),
            idempotencyKey: 'qo-more-${capture.conversationId}-$_more',
          );
      if (!mounted) return;
      setState(() {
        _reply = reply;
        _phase = _Phase.review;
      });
    } on ApiError catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error;
        _phase = _reply == null ? _Phase.failed : _Phase.review;
      });
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
      outcome: _outcome,
      standalone: widget.standalone,
      onClose: _close,
      onOrdered: _ordered,
      onMoreMessages: () => unawaited(_moreMessages()),
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
    required this.outcome,
    required this.standalone,
    required this.onClose,
    required this.onOrdered,
    required this.onMoreMessages,
    required this.onRetry,
    required this.onOpenApp,
  });

  final _Phase phase;
  final QuickOrderCapture? capture;
  final Map<String, dynamic>? reply;
  final ApiError? error;
  final AuthStage auth;
  final QuickOrderOutcome? outcome;
  final bool standalone;
  final VoidCallback onClose;
  final ValueChanged<QuickOrderOutcome> onOrdered;
  final VoidCallback onMoreMessages;
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
      case _Phase.review:
      case _Phase.ordered:
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
    final done = FilledButton(
      key: const ValueKey('qo.done'),
      onPressed: onClose,
      child: Text(context.tr('qo.done')),
    );
    final outcome = this.outcome;
    if (phase == _Phase.ordered && outcome != null) {
      final number = outcome.order.orderNumber;
      return [
        Text(
          number.isEmpty
              ? context.tr('qo.orderedNoNumber')
              : context.tr('qo.ordered', {'number': number}),
          key: const ValueKey('qo.ordered'),
          style: theme.textTheme.titleSmall,
        ),
        if (outcome.queued)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(
              context.tr('qo.queued'),
              key: const ValueKey('qo.queued'),
            ),
          ),
        const SizedBox(height: 8),
        done,
      ];
    }
    final json = reply?['draft'];
    final draft = json is Map<String, dynamic>
        ? ChatDraft.fromJson(json)
        : null;
    if (draft == null || !draft.isOpen) {
      return [
        Text(
          context.tr(
            reply?['attention_id'] != null ? 'qo.attention' : 'qo.closed',
          ),
          key: const ValueKey('qo.noDraft'),
        ),
        const SizedBox(height: 8),
        done,
      ];
    }
    final error = this.error;
    final bn = context.strings.locale == AppLocale.bn;
    return [
      if (error != null)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Text(
            bn ? error.messageBn : error.messageEn,
            style: TextStyle(color: theme.colorScheme.error),
          ),
        ),
      QuickOrderForm(
        // A new paste re-reads the draft: start the form from it again.
        key: ValueKey('qo.form.${draft.id}.${draft.messageCount}'),
        draft: draft,
        suggestion: CourierSuggestion.fromJson(reply?['courier_suggestion']),
        onDone: onOrdered,
        onSaveDraft: onClose,
        onMoreMessages: onMoreMessages,
      ),
    ];
  }
}

/// The seller pastes more of the chat here.
class _MoreMessagesDialog extends StatefulWidget {
  const _MoreMessagesDialog();

  @override
  State<_MoreMessagesDialog> createState() => _MoreMessagesDialogState();
}

class _MoreMessagesDialogState extends State<_MoreMessagesDialog> {
  final TextEditingController _text = TextEditingController();
  bool _empty = false;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(context.tr('qo.moreTitle')),
      content: TextField(
        key: const ValueKey('qo.moreText'),
        controller: _text,
        autofocus: true,
        minLines: 3,
        maxLines: 6,
        // The server keeps at most this much of one message.
        maxLength: 4000,
        decoration: InputDecoration(
          hintText: context.tr('qo.moreHint'),
          errorText: _empty ? context.tr('qo.moreEmpty') : null,
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(context.tr('qo.cancel')),
        ),
        FilledButton(
          key: const ValueKey('qo.moreAdd'),
          onPressed: () {
            if (_text.text.trim().isEmpty) {
              setState(() => _empty = true);
              return;
            }
            Navigator.of(context).pop(_text.text);
          },
          child: Text(context.tr('qo.moreAdd')),
        ),
      ],
    );
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
