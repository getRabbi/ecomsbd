import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

final networkBenchmarkProvider =
    FutureProvider.autoDispose<Map<String, dynamic>>(
      (ref) => ref.watch(apiClientProvider).get('/network-intelligence'),
    );

class NetworkScreen extends ConsumerWidget {
  const NetworkScreen({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bn = context.strings.locale == AppLocale.bn;
    final data = ref.watch(networkBenchmarkProvider);
    return Scaffold(
      appBar: AppBar(
        title: Text(
          bn ? 'বেনামি নেটওয়ার্ক বেঞ্চমার্ক' : 'Anonymous benchmarks',
        ),
      ),
      body: data.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
          child: Text(
            e is ApiError
                ? (bn ? e.messageBn : e.messageEn)
                : (bn ? 'লোড করা যায়নি' : 'Could not load'),
          ),
        ),
        data: (value) => ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text('${value[bn ? 'message_bn' : 'message_en']}'),
            const SizedBox(height: 16),
            Text(
              bn
                  ? 'কোনো গ্রাহক, ফোন বা শপ খোঁজা যায় না। সম্মতি প্রত্যাহার করলে ভবিষ্যতের হিসাবে অংশ নেবে না। প্রকাশিত বেনামি ফল থাকবে।'
                  : 'No customer, phone or shop lookup. Opting out stops future contributions; published anonymous results remain.',
            ),
            if (value['status'] != 'COMPLETE')
              Text(
                bn
                    ? 'যথেষ্ট নিরাপদ নমুনা না পাওয়া পর্যন্ত বন্ধ আছে।'
                    : 'Gated until a sufficient privacy-safe sample is available.',
              )
            else ...[
              Text('${value['period']} (UTC)'),
              Text('RTO: ${(value['facts'] as Map)['rto_percent_rounded']}%'),
              Text(
                '${bn ? 'ডেলিভারি' : 'Delivery'}: ${(value['facts'] as Map)['delivery_percent_rounded']}%',
              ),
            ],
            SwitchListTile(
              title: Text(
                bn
                    ? 'বেনামি সমষ্টিতে অংশগ্রহণ'
                    : 'Participate in anonymous aggregates',
              ),
              value: value['opted_in'] == true,
              onChanged: (enabled) async {
                try {
                  await ref
                      .read(apiClientProvider)
                      .patch(
                        '/network-intelligence/preference',
                        body: {'opted_in': enabled},
                      );
                  ref.invalidate(networkBenchmarkProvider);
                } on ApiError catch (e) {
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(content: Text(bn ? e.messageBn : e.messageEn)),
                    );
                  }
                }
              },
            ),
          ],
        ),
      ),
    );
  }
}
