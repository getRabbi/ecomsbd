import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../design/components/surfaces.dart';
import '../../l10n/app_strings.dart';
import 'autopilot_screen.dart';

/// Home "Autopilot today" (V4 Phase 4): how many orders Autopilot made today
/// and how their confirmations stand, the last five decisions with their
/// reason, and "Pause 1 hour" (queued calls hold, nothing is cancelled).
///
/// Hidden while the feature is off for the shop (`FEATURE_DISABLED`), on any
/// load error, and until Autopilot is on or has decided something.
class AutopilotHomeCard extends ConsumerStatefulWidget {
  const AutopilotHomeCard({super.key});

  @override
  ConsumerState<AutopilotHomeCard> createState() => _AutopilotHomeCardState();
}

class _AutopilotHomeCardState extends ConsumerState<AutopilotHomeCard> {
  bool _busy = false;

  Future<void> _press(String action) async {
    setState(() => _busy = true);
    try {
      await ref.read(apiClientProvider).post('/autopilot/$action', body: {});
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(autopilotErrorText(context, error, 'ap.saveFailed')),
          ),
        );
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
        ref
          ..invalidate(autopilotStatusProvider)
          ..invalidate(autopilotTodayProvider);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final status = ref.watch(autopilotStatusProvider).valueOrNull;
    if (status == null) return const SizedBox.shrink();
    final today = ref.watch(autopilotTodayProvider).valueOrNull;
    final recent = (today?['recent'] as List? ?? const <Object?>[])
        .whereType<Map<dynamic, dynamic>>()
        .take(5)
        .toList();
    final on = status['on'] == true;
    if (!on && recent.isEmpty) return const SizedBox.shrink();

    final role = autopilotRole(ref);
    final paused = status['paused_until'] != null;
    final canPause = on && !paused && autopilotCan(role, 'order.write');
    final canResume = paused && autopilotCan(role, 'settings.manage');
    final theme = Theme.of(context);

    Widget count(String label, Object? value, String key) => Column(
      key: ValueKey(key),
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Text('${value ?? 0}', style: theme.textTheme.titleLarge),
        Text(label, style: theme.textTheme.bodySmall),
      ],
    );

    return Column(
      key: const ValueKey('ap.homeCard'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        SectionHeader(title: context.tr('ap.home')),
        GlassCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              if (paused)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text(
                    context.tr('ap.pausedNote'),
                    key: const ValueKey('ap.pausedNote'),
                  ),
                ),
              Wrap(
                alignment: WrapAlignment.spaceAround,
                spacing: 16,
                runSpacing: 8,
                children: <Widget>[
                  count(context.tr('ap.made'), today?['converted'], 'ap.made'),
                  count(
                    context.tr('ap.confirmed'),
                    today?['confirmed'],
                    'ap.confirmed',
                  ),
                  count(
                    context.tr('ap.cancelled'),
                    today?['cancelled'],
                    'ap.cancelled',
                  ),
                  count(
                    context.tr('ap.waiting'),
                    today?['waiting'],
                    'ap.waiting',
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Text(context.tr('ap.recent'), style: theme.textTheme.titleSmall),
              if (recent.isEmpty)
                Text(context.tr('ap.none'))
              else
                for (final decision in recent)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      decision['outcome'] == 'CONVERTED'
                          ? context.tr('ap.madeOrder', {
                              'number': decision['order_number'] ?? '',
                            })
                          : context.tr('ap.heldFor', {
                              'reason': context.tr(
                                autopilotReasonKey(decision['reason']),
                              ),
                            }),
                    ),
                  ),
              if (canPause || canResume) ...<Widget>[
                const SizedBox(height: 12),
                Align(
                  alignment: AlignmentDirectional.centerEnd,
                  child: canPause
                      ? OutlinedButton.icon(
                          key: const ValueKey('ap.pause'),
                          onPressed: _busy ? null : () => _press('pause'),
                          icon: const Icon(Icons.pause_circle_outline),
                          label: Text(context.tr('ap.pause')),
                        )
                      : FilledButton.icon(
                          key: const ValueKey('ap.resume'),
                          onPressed: _busy ? null : () => _press('resume'),
                          icon: const Icon(Icons.play_circle_outline),
                          label: Text(context.tr('ap.resume')),
                        ),
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }
}
