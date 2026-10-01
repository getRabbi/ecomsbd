import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../data/channels/integration_connect.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_strings_data.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import 'integrations_screen.dart';

/// Connecting a store or chat channel, natively on the phone.
///
/// Every check is the server's: the phone collects what the provider needs
/// (a store address, keys, a Facebook sign-in, a WhatsApp number) and shows the
/// connection's state as the API reports it. Nothing is validated here that
/// the server would not validate again.

/// Open the right setup for [provider]. Returns true when something changed.
Future<bool> startIntegrationSetup(
  BuildContext context,
  String provider, {
  String? connectionId,
  String? initialAddress,
}) async {
  final Widget screen = switch (provider) {
    'CUSTOM_WEBSITE' => const CustomWebsiteSetupScreen(),
    'WHATSAPP' => WhatsAppConnectScreen(connectionId: connectionId),
    _ => ProviderSignInScreen(
      provider: provider,
      connectionId: connectionId,
      initialAddress: initialAddress,
    ),
  };
  final changed = await Navigator.of(
    context,
  ).push<bool>(MaterialPageRoute<bool>(builder: (_) => screen));
  return changed ?? false;
}

enum _Step { address, waiting, keys, pages, done }

/// WooCommerce (store address, then the store's own approval page or keys),
/// Shopify (store domain, then Shopify's approval) and Messenger (Facebook
/// sign-in, then the Page to connect).
class ProviderSignInScreen extends ConsumerStatefulWidget {
  const ProviderSignInScreen({
    required this.provider,
    super.key,
    this.connectionId,
    this.initialAddress,
  });

  final String provider;
  final String? connectionId;
  final String? initialAddress;

  @override
  ConsumerState<ProviderSignInScreen> createState() => _SignInState();
}

class _SignInState extends ConsumerState<ProviderSignInScreen> {
  late final TextEditingController _address = TextEditingController(
    text: widget.initialAddress ?? '',
  );
  final TextEditingController _key = TextEditingController();
  final TextEditingController _secret = TextEditingController();
  StreamSubscription<IntegrationReturn>? _returns;
  AppLifecycleListener? _lifecycle;

  late String? _id = widget.connectionId;
  late _Step _step = widget.provider == 'MESSENGER'
      ? _Step.waiting
      : _Step.address;
  bool _launched = false;
  bool _busy = false;
  String? _error;
  String? _accountName;
  List<Map<String, dynamic>> _pages = const <Map<String, dynamic>>[];

  bool get _woo => widget.provider == 'WOOCOMMERCE';

  @override
  void initState() {
    super.initState();
    _returns = ref.read(integrationReturnsProvider).listen((back) {
      if (back.connectionId != _id || !mounted) return;
      if (back.result == 'ACCESS_DENIED') {
        setState(() => _error = context.tr('ics.declined'));
        return;
      }
      unawaited(_check(hint: back.result));
    });
    _lifecycle = AppLifecycleListener(
      onResume: () {
        if (_launched && _step == _Step.waiting) unawaited(_check());
      },
    );
    if (widget.connectionId != null && widget.provider == 'MESSENGER') {
      // An unfinished Facebook sign-in may already have its Pages listed.
      Future<void>.microtask(() => _check(quiet: true));
    }
  }

  @override
  void dispose() {
    unawaited(_returns?.cancel());
    _lifecycle?.dispose();
    _address.dispose();
    _key.dispose();
    _secret.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() work) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await work();
    } on ApiError catch (error) {
      if (mounted) setState(() => _error = explain(context, error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _defaultName() {
    final host = _address.text
        .trim()
        .replaceFirst(RegExp(r'^https?://'), '')
        .split('/')
        .first;
    if (host.isEmpty) return context.tr('int.provider.${widget.provider}');
    return host.length > 120 ? host.substring(0, 120) : host;
  }

  Future<String> _ensureConnection() async {
    final existing = _id;
    if (existing != null) return existing;
    final created = await ref
        .read(apiClientProvider)
        .post(
          '/integrations',
          body: <String, dynamic>{
            'provider': widget.provider,
            'name': _defaultName(),
          },
        );
    final id = '${(created['connection'] as Map)['id']}';
    setState(() => _id = id);
    return id;
  }

  Future<void> _start() => _run(() async {
    final id = await _ensureConnection();
    final body = <String, dynamic>{
      'return_to': 'app',
      if (_woo) 'store_url': _address.text.trim(),
      if (widget.provider == 'SHOPIFY') 'shop': _address.text.trim(),
    };
    final result = await ref
        .read(apiClientProvider)
        .post('/integrations/$id/connect', body: body);
    if (result['manual'] == true) {
      setState(() => _step = _Step.keys);
      return;
    }
    final url = result['authorize_url'] as String?;
    if (url == null) {
      setState(() => _error = context.tr('ics.noSignIn'));
      return;
    }
    final opened = await ref.read(externalOpenerProvider)(Uri.parse(url));
    if (!mounted) return;
    setState(() {
      _launched = true;
      _step = _Step.waiting;
      if (!opened) _error = context.tr('ics.browserError');
    });
  });

  /// What the server says now. Called on return from the browser; [hint] is
  /// the return page's code, used only to word a failure the server's
  /// connection does not record.
  Future<void> _check({String? hint, bool quiet = false}) => _run(() async {
    final id = _id;
    if (id == null) return;
    final api = ref.read(apiClientProvider);
    final detail = await api.get('/integrations/$id');
    var connection = detail['connection'] as Map<String, dynamic>;
    if (_woo &&
        connection['state'] != 'CONNECTED' &&
        connection['keys_received'] == true) {
      final tested = await api.post('/integrations/$id/test');
      connection = tested['connection'] as Map<String, dynamic>? ?? connection;
      if (connection['state'] != 'CONNECTED') {
        _failedCheck(tested);
        return;
      }
    }
    _show(connection, hint: hint, quiet: quiet);
  });

  void _failedCheck(Map<String, dynamic> result) {
    final checks = (result['checks'] as List<dynamic>? ?? const <dynamic>[])
        .whereType<Map<String, dynamic>>();
    final failed = checks.where((check) => check['ok'] != true);
    final code = failed.isEmpty ? null : failed.first['code'] as String?;
    setState(
      () => _error = code == null
          ? context.tr('int.testFailed')
          : codeLabel(context, code),
    );
  }

  void _show(
    Map<String, dynamic> connection, {
    String? hint,
    bool quiet = false,
  }) {
    final pages = (connection['pages'] as List<dynamic>? ?? const <dynamic>[])
        .whereType<Map<String, dynamic>>()
        .toList();
    setState(() {
      _accountName = connection['account_name'] as String?;
      if (connection['state'] == 'CONNECTED') {
        _step = _Step.done;
      } else if (widget.provider == 'MESSENGER' && pages.isNotEmpty) {
        _pages = pages;
        _step = _Step.pages;
      } else if (quiet) {
        return;
      } else if (connection['last_error_code'] != null) {
        _error = codeLabel(context, '${connection['last_error_code']}');
      } else if (hint != null && englishStrings.containsKey('int.code.$hint')) {
        _error = codeLabel(context, hint);
      } else {
        _error = context.tr('ics.notYet');
      }
    });
  }

  Future<void> _saveKeys() => _run(() async {
    final id = await _ensureConnection();
    if (widget.connectionId == null && _step == _Step.keys) {
      // Manual keys need the store address on the connection first.
      await ref
          .read(apiClientProvider)
          .post(
            '/integrations/$id/connect',
            body: <String, dynamic>{
              'store_url': _address.text.trim(),
              'return_to': 'app',
            },
          );
    }
    final result = await ref
        .read(apiClientProvider)
        .post(
          '/integrations/$id/woocommerce/keys',
          body: <String, dynamic>{
            'consumer_key': _key.text.trim(),
            'consumer_secret': _secret.text.trim(),
          },
        );
    _secret.clear();
    final connection = result['connection'] as Map<String, dynamic>? ?? {};
    if (connection['state'] == 'CONNECTED') {
      _show(connection);
    } else {
      _failedCheck(result);
    }
  });

  Future<void> _choosePage(String pageId) => _run(() async {
    final result = await ref
        .read(apiClientProvider)
        .post(
          '/integrations/$_id/messenger/page',
          body: <String, dynamic>{'page_id': pageId},
        );
    _show(result['connection'] as Map<String, dynamic>);
  });

  @override
  Widget build(BuildContext context) {
    final name = context.tr('int.provider.${widget.provider}');
    return DetailScaffold(
      eyebrow: context.tr('conn.eyebrow'),
      title: context.tr('ics.connectTitle', <String, Object?>{
        'provider': name,
      }),
      subtitle: context.tr('ics.sub.${widget.provider}'),
      children: <Widget>[
        if (_busy) const LinearProgressIndicator(),
        if (_error != null) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.xs),
          _Problem(text: _error!),
        ],
        const SizedBox(height: EcomsbdSpacing.sm),
        ...switch (_step) {
          _Step.address => _addressStep(context),
          _Step.waiting => _waitingStep(context),
          _Step.keys => _keysStep(context),
          _Step.pages => _pagesStep(context),
          _Step.done => _doneStep(context),
        },
      ],
    );
  }

  List<Widget> _addressStep(BuildContext context) => <Widget>[
    GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          LabelledField(
            key: const Key('ics-address'),
            label: context.tr(_woo ? 'ics.storeUrl' : 'ics.shopDomain'),
            controller: _address,
            hint: _woo ? 'https://mystore.com' : 'mystore.myshopify.com',
            keyboardType: TextInputType.url,
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            context.tr(_woo ? 'ics.storeUrlNote' : 'ics.shopDomainNote'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ],
      ),
    ),
    const SizedBox(height: EcomsbdSpacing.md),
    _Primary(
      key: const Key('ics-continue'),
      label: context.tr('ics.continue'),
      onPressed: _busy ? null : _start,
    ),
    if (_woo) ...<Widget>[
      const SizedBox(height: EcomsbdSpacing.xs),
      TextButton(
        key: const Key('ics-manual'),
        onPressed: _busy ? null : () => setState(() => _step = _Step.keys),
        child: Text(context.tr('ics.manualInstead')),
      ),
    ],
  ];

  List<Widget> _waitingStep(BuildContext context) => <Widget>[
    GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            context.tr(
              _launched ? 'ics.waitTitle' : 'ics.signInTitle',
              <String, Object?>{
                'provider': context.tr('int.provider.${widget.provider}'),
              },
            ),
            style: EcomsbdType.bodyStrong,
          ),
          const SizedBox(height: 4),
          Text(
            context.tr(
              _launched ? 'ics.waitBody' : 'ics.signInBody.${widget.provider}',
            ),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ],
      ),
    ),
    const SizedBox(height: EcomsbdSpacing.md),
    if (!_launched)
      _Primary(
        key: const Key('ics-sign-in'),
        label: context.tr('ics.signInButton.${widget.provider}'),
        onPressed: _busy ? null : _start,
      )
    else ...<Widget>[
      _Primary(
        key: const Key('ics-check'),
        label: context.tr('ics.checkNow'),
        onPressed: _busy ? null : _check,
      ),
      TextButton(
        onPressed: _busy ? null : _start,
        child: Text(context.tr('ics.openAgain')),
      ),
    ],
    if (_woo)
      TextButton(
        onPressed: _busy ? null : () => setState(() => _step = _Step.keys),
        child: Text(context.tr('ics.manualInstead')),
      ),
  ];

  List<Widget> _keysStep(BuildContext context) => <Widget>[
    GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Text(context.tr('ics.keysTitle'), style: EcomsbdType.bodyStrong),
          const SizedBox(height: 4),
          Text(
            context.tr('ics.keysHelp'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          if (widget.connectionId == null) ...<Widget>[
            LabelledField(
              label: context.tr('ics.storeUrl'),
              controller: _address,
              hint: 'https://mystore.com',
              keyboardType: TextInputType.url,
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
          ],
          LabelledField(
            key: const Key('ics-key'),
            label: context.tr('int.consumerKey'),
            controller: _key,
            hint: 'ck_…',
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          LabelledField(
            key: const Key('ics-secret'),
            label: context.tr('int.consumerSecret'),
            controller: _secret,
            hint: 'cs_…',
            obscureText: true,
          ),
        ],
      ),
    ),
    const SizedBox(height: EcomsbdSpacing.md),
    _Primary(
      key: const Key('ics-save-keys'),
      label: context.tr('ics.saveKeys'),
      onPressed: _busy ? null : _saveKeys,
    ),
  ];

  List<Widget> _pagesStep(BuildContext context) => <Widget>[
    Text(context.tr('ics.choosePage'), style: EcomsbdType.bodyStrong),
    const SizedBox(height: EcomsbdSpacing.xs),
    for (final page in _pages)
      Card(
        child: ListTile(
          key: ValueKey('ics-page-${page['id']}'),
          leading: const Icon(Icons.flag_outlined),
          title: Text('${page['name'] ?? page['id']}'),
          onTap: _busy ? null : () => _choosePage('${page['id']}'),
        ),
      ),
  ];

  List<Widget> _doneStep(BuildContext context) => <Widget>[
    GlassCard(
      child: Column(
        children: <Widget>[
          const Icon(
            Icons.check_circle_rounded,
            color: EcomsbdColors.green,
            size: 44,
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            context.tr('ics.connected'),
            key: const Key('ics-connected'),
            style: EcomsbdType.bodyStrong,
          ),
          if (_accountName != null)
            Text(
              _accountName!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          if (widget.provider == 'MESSENGER') ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              context.tr('ics.chatNote'),
              textAlign: TextAlign.center,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
        ],
      ),
    ),
    const SizedBox(height: EcomsbdSpacing.md),
    _Primary(
      key: const Key('ics-done'),
      label: context.tr('common.done'),
      onPressed: () => Navigator.of(context).pop(true),
    ),
  ];
}

// ------------------------------------------------------------ WhatsApp --

/// Link a WhatsApp Business number the shop owns: its phone number id, its
/// WhatsApp Business Account id and an access token, from Meta's dashboard.
/// The server proves the token with Meta before saving it.
class WhatsAppConnectScreen extends ConsumerStatefulWidget {
  const WhatsAppConnectScreen({super.key, this.connectionId});

  final String? connectionId;

  @override
  ConsumerState<WhatsAppConnectScreen> createState() => _WhatsAppState();
}

class _WhatsAppState extends ConsumerState<WhatsAppConnectScreen> {
  final TextEditingController _number = TextEditingController();
  final TextEditingController _waba = TextEditingController();
  final TextEditingController _token = TextEditingController();
  bool _busy = false;
  String? _error;
  String? _connected;

  @override
  void dispose() {
    _number.dispose();
    _waba.dispose();
    _token.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final api = ref.read(apiClientProvider);
    try {
      var id = widget.connectionId;
      if (id == null) {
        final created = await api.post(
          '/integrations',
          body: <String, dynamic>{'provider': 'WHATSAPP', 'name': 'WhatsApp'},
        );
        id = '${(created['connection'] as Map)['id']}';
      }
      final result = await api.post(
        '/integrations/$id/whatsapp',
        body: <String, dynamic>{
          'phone_number_id': _number.text.trim(),
          'waba_id': _waba.text.trim(),
          'access_token': _token.text.trim(),
        },
      );
      _token.clear();
      final connection = result['connection'] as Map<String, dynamic>;
      if (mounted) {
        setState(() => _connected = '${connection['account_name'] ?? ''}');
      }
    } on ApiError catch (error) {
      if (mounted) setState(() => _error = explain(context, error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final connected = _connected;
    return DetailScaffold(
      eyebrow: context.tr('conn.eyebrow'),
      title: context.tr('ics.connectTitle', <String, Object?>{
        'provider': 'WhatsApp',
      }),
      subtitle: context.tr('ics.sub.WHATSAPP'),
      children: <Widget>[
        if (_busy) const LinearProgressIndicator(),
        if (_error != null) _Problem(text: _error!),
        const SizedBox(height: EcomsbdSpacing.sm),
        if (connected != null) ...<Widget>[
          GlassCard(
            child: Column(
              children: <Widget>[
                const Icon(
                  Icons.check_circle_rounded,
                  color: EcomsbdColors.green,
                  size: 44,
                ),
                Text(
                  context.tr('ics.connected'),
                  key: const Key('ics-connected'),
                  style: EcomsbdType.bodyStrong,
                ),
                Text(connected, style: EcomsbdType.caption),
                const SizedBox(height: EcomsbdSpacing.xs),
                Text(
                  context.tr('ics.chatNote'),
                  textAlign: TextAlign.center,
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          _Primary(
            label: context.tr('common.done'),
            onPressed: () => Navigator.of(context).pop(true),
          ),
        ] else ...<Widget>[
          GlassCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Text(
                  context.tr('ics.waHelp'),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
                const SizedBox(height: EcomsbdSpacing.md),
                LabelledField(
                  key: const Key('ics-wa-number'),
                  label: context.tr('ics.waNumberId'),
                  controller: _number,
                  keyboardType: TextInputType.number,
                ),
                const SizedBox(height: EcomsbdSpacing.sm),
                LabelledField(
                  key: const Key('ics-wa-waba'),
                  label: context.tr('ics.waWabaId'),
                  controller: _waba,
                  keyboardType: TextInputType.number,
                ),
                const SizedBox(height: EcomsbdSpacing.sm),
                LabelledField(
                  key: const Key('ics-wa-token'),
                  label: context.tr('ics.waToken'),
                  controller: _token,
                  obscureText: true,
                ),
              ],
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          _Primary(
            key: const Key('ics-wa-connect'),
            label: context.tr('common.connect'),
            onPressed: _busy ? null : _connect,
          ),
        ],
      ],
    );
  }
}

// ------------------------------------------------------- Custom website --

/// Name the website, then show its API key once.
class CustomWebsiteSetupScreen extends ConsumerStatefulWidget {
  const CustomWebsiteSetupScreen({super.key});

  @override
  ConsumerState<CustomWebsiteSetupScreen> createState() => _CustomState();
}

class _CustomState extends ConsumerState<CustomWebsiteSetupScreen> {
  final TextEditingController _name = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    final name = _name.text.trim();
    if (name.isEmpty) {
      setState(() => _error = context.tr('ics.nameRequired'));
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final created = await ref
          .read(apiClientProvider)
          .post(
            '/integrations',
            body: <String, dynamic>{'provider': 'CUSTOM_WEBSITE', 'name': name},
          );
      final id = '${(created['connection'] as Map)['id']}';
      final key = '${created['api_key']}';
      if (!mounted) return;
      await Navigator.of(context).pushReplacement(
        MaterialPageRoute<bool>(
          builder: (_) => SecretOnceScreen(
            title: context.tr('ics.keyTitle'),
            secret: key,
            next: () => IntegrationDetailScreen(id: id),
          ),
        ),
        result: true,
      );
    } on ApiError catch (error) {
      if (mounted) setState(() => _error = explain(context, error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return DetailScaffold(
      eyebrow: context.tr('conn.eyebrow'),
      title: context.tr('ics.connectTitle', <String, Object?>{
        'provider': context.tr('int.provider.CUSTOM_WEBSITE'),
      }),
      subtitle: context.tr('ics.sub.CUSTOM_WEBSITE'),
      children: <Widget>[
        if (_busy) const LinearProgressIndicator(),
        if (_error != null) _Problem(text: _error!),
        const SizedBox(height: EcomsbdSpacing.sm),
        GlassCard(
          child: LabelledField(
            key: const Key('ics-site-name'),
            label: context.tr('ics.siteName'),
            controller: _name,
            hint: 'mystore.com',
          ),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        _Primary(
          key: const Key('ics-site-create'),
          label: context.tr('ics.createKey'),
          onPressed: _busy ? null : _create,
        ),
      ],
    );
  }
}

/// A secret the server returns exactly once: an API key or a webhook signing
/// secret. It lives only in this screen's memory and is gone when it closes.
class SecretOnceScreen extends StatelessWidget {
  const SecretOnceScreen({
    required this.title,
    required this.secret,
    super.key,
    this.next,
  });

  final String title;
  final String secret;

  /// Where "I saved it" leads; null returns to the previous screen.
  final Widget Function()? next;

  Future<bool> _confirmLeave(BuildContext context) async {
    final leave = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(context.tr('ics.leaveTitle')),
        content: Text(context.tr('ics.leaveBody')),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: Text(context.tr('common.cancel')),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: Text(context.tr('ics.leaveConfirm')),
          ),
        ],
      ),
    );
    return leave ?? false;
  }

  void _finish(BuildContext context) {
    final following = next;
    if (following == null) {
      Navigator.of(context).pop(true);
    } else {
      unawaited(
        Navigator.of(
          context,
        ).pushReplacement(MaterialPageRoute<void>(builder: (_) => following())),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop) return;
        if (await _confirmLeave(context) && context.mounted) {
          _finish(context);
        }
      },
      child: DetailScaffold(
        title: title,
        children: <Widget>[
          GlassCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                StatusChip(
                  label: context.tr('ics.onceWarning'),
                  tone: Tone.warning,
                ),
                const SizedBox(height: EcomsbdSpacing.sm),
                SelectableText(
                  secret,
                  key: const Key('ics-secret-value'),
                  style: EcomsbdType.bodyStrong.copyWith(
                    fontFamily: 'monospace',
                  ),
                ),
                const SizedBox(height: EcomsbdSpacing.sm),
                OutlinedButton.icon(
                  key: const Key('ics-copy'),
                  icon: const Icon(Icons.copy_rounded, size: 18),
                  label: Text(context.tr('ics.copy')),
                  onPressed: () async {
                    await Clipboard.setData(ClipboardData(text: secret));
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text(context.tr('ics.copied'))),
                      );
                    }
                  },
                ),
                const SizedBox(height: EcomsbdSpacing.xs),
                Text(
                  context.tr('ics.onceBody'),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          _Primary(
            key: const Key('ics-saved'),
            label: context.tr('ics.saved'),
            onPressed: () => _finish(context),
          ),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------- pieces --

class _Primary extends StatelessWidget {
  const _Primary({required this.label, required this.onPressed, super.key});

  final String label;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) => FilledButton(
    onPressed: onPressed,
    style: FilledButton.styleFrom(
      backgroundColor: EcomsbdColors.orange,
      minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
      shape: const StadiumBorder(),
      textStyle: EcomsbdType.label,
    ),
    child: Text(label),
  );
}

class _Problem extends StatelessWidget {
  const _Problem({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) => Container(
    key: const Key('ics-problem'),
    padding: const EdgeInsets.all(EcomsbdSpacing.sm),
    decoration: BoxDecoration(
      color: Tone.bad.surface,
      borderRadius: EcomsbdRadii.cardSmall,
    ),
    child: Text(text, style: EcomsbdType.caption.copyWith(color: Tone.bad.ink)),
  );
}
