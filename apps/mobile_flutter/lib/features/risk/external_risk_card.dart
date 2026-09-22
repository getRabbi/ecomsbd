import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

final externalRiskCapabilityProvider =
    FutureProvider.autoDispose<Map<String, dynamic>>(
      (ref) => ref.watch(apiClientProvider).get('/external-risk/capability'),
    );

class ExternalRiskCard extends ConsumerWidget {
  const ExternalRiskCard({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bn = context.strings.locale == AppLocale.bn;
    final result = ref.watch(externalRiskCapabilityProvider);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              bn ? 'বাইরের প্রোভাইডারের তথ্য' : 'External provider facts',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            result.when(
              data: (data) => Text('${data[bn ? 'message_bn' : 'message_en']}'),
              loading: () => const LinearProgressIndicator(),
              error: (error, _) => Text(
                error is ApiError
                    ? (bn ? error.messageBn : error.messageEn)
                    : (bn ? 'লোড করা যায়নি' : 'Could not load'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
