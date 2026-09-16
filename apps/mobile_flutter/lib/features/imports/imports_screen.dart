import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/responsive.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_locale.dart';

/// Read where no `BuildContext` exists, so the active locale is resolved
/// directly -- the same approach `formatRelative` and `order_status.dart` use.
String _t(String key) => AppStrings(activeAppLocale).t(key);

/// Where the seller is in the import.
enum _Step { choose, map, review, done }

/// A file the seller chose.
class PickedImportFile {
  const PickedImportFile({required this.name, required this.bytes});

  final String name;
  final List<int> bytes;
}

/// Opens the system document picker.
///
/// Injectable so a test can supply a CSV without a platform channel; the
/// default is the real picker.
Future<PickedImportFile?> pickImportFile() async {
  final picked = await FilePicker.pickFiles(
    type: FileType.custom,
    allowedExtensions: <String>['csv', 'txt'],
    withData: true,
  );
  final file = picked?.files.singleOrNull;
  final bytes = file?.bytes;
  if (file == null || bytes == null) {
    return null;
  }
  return PickedImportFile(name: file.name, bytes: bytes);
}

/// Bring a spreadsheet in.
///
/// Three deliberate steps: choose and detect, check the mapping, dry-run — and
/// only then commit. The dry run is not skippable. An import is a bulk write a
/// seller cannot easily undo, so nothing is created before they have seen how
/// many rows are ready, how many are duplicates, and exactly which ones are
/// broken and why (master spec sections 7.3, 98).
class ImportsScreen extends ConsumerStatefulWidget {
  const ImportsScreen({super.key, this.pickFile = pickImportFile});

  /// How a file is chosen. Overridden in tests.
  final Future<PickedImportFile?> Function() pickFile;

  @override
  ConsumerState<ImportsScreen> createState() => _ImportsScreenState();
}

class _ImportsScreenState extends ConsumerState<ImportsScreen> {
  _Step _step = _Step.choose;
  String _template = 'PRODUCTS';

  ImportBatch? _batch;
  List<ImportRowReport> _rows = const <ImportRowReport>[];
  Map<String, String> _mapping = <String, String>{};

  bool _busy = false;
  ApiError? _error;
  int _created = 0;
  int _skipped = 0;

  static const Map<String, List<String>> _fields = <String, List<String>>{
    'PRODUCTS': <String>[
      'name',
      'sku',
      'cost',
      'price',
      'stock',
      'description',
    ],
    'ORDERS': <String>[
      'phone',
      'customer_name',
      'product',
      'quantity',
      'amount',
      'address',
      'district',
      'area',
      'note',
    ],
  };

  static const Map<String, List<String>> _required = <String, List<String>>{
    'PRODUCTS': <String>['name'],
    'ORDERS': <String>['phone', 'product'],
  };

  Future<void> _chooseFile() async {
    final file = await widget.pickFile();
    if (file == null) {
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      final batch = await ref
          .read(importsRepositoryProvider)
          .upload(bytes: file.bytes, filename: file.name, template: _template);
      if (mounted) {
        setState(() {
          _busy = false;
          _batch = batch;
          _mapping = Map<String, String>.from(batch.columnMapping);
          _step = _Step.map;
        });
      }
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = error;
        });
      }
    }
  }

  Future<void> _dryRun() async {
    final batch = _batch;
    if (batch == null) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      final repository = ref.read(importsRepositoryProvider);
      final validated = await repository.dryRun(
        batch.id,
        columnMapping: _mapping,
      );
      final rows = await repository.rows(batch.id, limit: 200);
      if (mounted) {
        setState(() {
          _busy = false;
          _batch = validated;
          _rows = rows;
          _step = _Step.review;
        });
      }
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = error;
        });
      }
    }
  }

  Future<void> _commit() async {
    final batch = _batch;
    if (batch == null) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      final result = await ref.read(importsRepositoryProvider).commit(batch.id);
      if (mounted) {
        setState(() {
          _busy = false;
          _batch = result.batch;
          _created = result.created;
          _skipped = result.skipped;
          _step = _Step.done;
        });
      }
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = error;
        });
      }
    }
  }

  void _restart() {
    setState(() {
      _step = _Step.choose;
      _batch = null;
      _rows = const <ImportRowReport>[];
      _mapping = <String, String>{};
      _error = null;
      _created = 0;
      _skipped = 0;
    });
  }

  @override
  Widget build(BuildContext context) {
    return DetailScaffold(
      title: context.tr('imp.title'),
      subtitle: switch (_step) {
        _Step.choose => context.tr('imp.step1'),
        _Step.map => context.tr('imp.step2'),
        _Step.review => context.tr('imp.step3'),
        _Step.done => context.tr('imp.finished'),
      },
      children: <Widget>[
        if (_error != null) ...<Widget>[
          ErrorStateCard(error: _error!),
          const SizedBox(height: EcomsbdSpacing.sm),
        ],
        ...switch (_step) {
          _Step.choose => _chooseStep(),
          _Step.map => _mapStep(),
          _Step.review => _reviewStep(),
          _Step.done => _doneStep(),
        },
      ],
    );
  }

  // --- steps ----------------------------------------------------------------

  List<Widget> _chooseStep() => <Widget>[
    GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(context.tr('imp.whatImporting'), style: EcomsbdType.bodyStrong),
          const SizedBox(height: EcomsbdSpacing.sm),
          Row(
            children: <Widget>[
              Expanded(
                child: _TemplateTile(
                  icon: Icons.inventory_2_outlined,
                  title: 'Products',
                  subtitle: context.tr('imp.productsSub'),
                  selected: _template == 'PRODUCTS',
                  onTap: () => setState(() => _template = 'PRODUCTS'),
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.sm),
              Expanded(
                child: _TemplateTile(
                  icon: Icons.receipt_long_outlined,
                  title: 'Orders',
                  subtitle: context.tr('imp.ordersSub'),
                  selected: _template == 'ORDERS',
                  onTap: () => setState(() => _template = 'ORDERS'),
                ),
              ),
            ],
          ),
        ],
      ),
    ),
    const SizedBox(height: EcomsbdSpacing.sm),
    GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(context.tr('imp.yourFile'), style: EcomsbdType.bodyStrong),
          const SizedBox(height: 3),
          Text(
            context.tr('imp.fileNote'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          FilledButton.icon(
            onPressed: _busy ? null : _chooseFile,
            icon: const Icon(Icons.upload_file_rounded, size: 18),
            label: Text(
              _busy
                  ? context.tr('common.reading')
                  : context.tr('imp.chooseFile'),
            ),
            style: FilledButton.styleFrom(
              backgroundColor: EcomsbdColors.orange,
              minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
              shape: const StadiumBorder(),
              textStyle: EcomsbdType.label,
            ),
          ),
        ],
      ),
    ),
  ];

  List<Widget> _mapStep() {
    final batch = _batch!;
    final fields = _fields[_template]!;
    final required = _required[_template]!;
    final missing = required.where((field) => _mapping[field] == null).toList();

    return <Widget>[
      GlassCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(batch.filename, style: EcomsbdType.bodyStrong),
            const SizedBox(height: 3),
            Text(
              '${batch.detectedHeaders.length} columns found. We guessed which '
              'is which — correct anything that is wrong.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
        ),
      ),
      const SizedBox(height: EcomsbdSpacing.sm),
      for (final field in fields)
        Padding(
          padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
          child: _ColumnMapRow(
            field: field,
            isRequired: required.contains(field),
            headers: batch.detectedHeaders,
            selected: _mapping[field],
            onChanged: (header) => setState(() {
              if (header == null) {
                _mapping.remove(field);
              } else {
                _mapping[field] = header;
              }
            }),
          ),
        ),
      if (missing.isNotEmpty) ...<Widget>[
        const SizedBox(height: EcomsbdSpacing.sm),
        ProviderHealthBanner(
          provider: context.tr('imp.stillNeeded'),
          detail:
              'Point ${missing.join(', ')} at a column before checking the '
              'file.',
          tone: Tone.warning,
        ),
      ],
      const SizedBox(height: EcomsbdSpacing.md),
      FilledButton(
        onPressed: _busy || missing.isNotEmpty ? null : _dryRun,
        style: FilledButton.styleFrom(
          backgroundColor: EcomsbdColors.orange,
          minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
          shape: const StadiumBorder(),
          textStyle: EcomsbdType.label,
        ),
        child: Text(_busy ? 'Checking…' : context.tr('imp.checkEveryRow')),
      ),
      const SizedBox(height: EcomsbdSpacing.xs),
      Text(
        context.tr('imp.readOnlyNote'),
        style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
        textAlign: TextAlign.center,
      ),
    ];
  }

  List<Widget> _reviewStep() {
    final batch = _batch!;
    final problems = _rows
        .where((row) => row.errors.isNotEmpty || row.warnings.isNotEmpty)
        .toList();

    return <Widget>[
      ResponsiveGrid(
        minTileWidth: 150,
        maxColumns: 2,
        spacing: EcomsbdSpacing.xs,
        children: <Widget>[
          MetricTile(
            label: 'Ready',
            value: '${batch.readyCount}',
            caption: context.tr('imp.willBeCreated'),
            tone: Tone.good,
          ),
          MetricTile(
            label: context.tr('imp.withWarning'),
            value: '${batch.warningCount}',
            caption: context.tr('imp.createdButCheck'),
            tone: batch.warningCount > 0 ? Tone.warning : null,
          ),
          MetricTile(
            label: context.tr('imp.alreadyHere'),
            value: '${batch.duplicateCount}',
            caption: 'skipped',
          ),
          MetricTile(
            label: 'Cannot import',
            value: '${batch.invalidCount}',
            caption: context.tr('imp.fixAndReupload'),
            tone: batch.invalidCount > 0 ? Tone.bad : null,
          ),
        ],
      ),
      if (problems.isNotEmpty) ...<Widget>[
        SectionHeader(
          title: context.tr('imp.rowsToLookAt'),
          subtitle: context.tr('imp.rowsToLookAtSub'),
        ),
        for (final row in problems.take(50))
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
            child: _ProblemRow(row: row),
          ),
        if (problems.length > 50)
          Text(
            '${problems.length - 50} more not shown.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
      ],
      const SizedBox(height: EcomsbdSpacing.md),
      FilledButton(
        onPressed: _busy || !batch.canCommit ? null : _commit,
        style: FilledButton.styleFrom(
          backgroundColor: EcomsbdColors.orange,
          minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
          shape: const StadiumBorder(),
          textStyle: EcomsbdType.label,
        ),
        child: Text(
          _busy
              ? context.tr('imp.creating')
              : (batch.canCommit
                    ? 'Create ${batch.importableCount} record'
                          '${batch.importableCount == 1 ? '' : 's'}'
                    : context.tr('imp.nothingToCreate')),
        ),
      ),
      const SizedBox(height: EcomsbdSpacing.sm),
      TextButton(
        onPressed: _busy ? null : () => setState(() => _step = _Step.map),
        style: TextButton.styleFrom(
          minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
          foregroundColor: EcomsbdColors.muted,
          textStyle: EcomsbdType.label,
        ),
        child: Text(context.tr('imp.backToColumns')),
      ),
    ];
  }

  List<Widget> _doneStep() => <Widget>[
    GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              const Icon(
                Icons.check_circle_outline,
                size: 22,
                color: EcomsbdColors.green,
              ),
              const SizedBox(width: EcomsbdSpacing.sm),
              Expanded(
                child: Text(
                  '$_created record${_created == 1 ? '' : 's'} created',
                  style: EcomsbdType.sectionTitle,
                ),
              ),
            ],
          ),
          if (_skipped > 0) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              '$_skipped row${_skipped == 1 ? '' : 's'} skipped — duplicates or '
              'rows that could not be read.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
          const SizedBox(height: EcomsbdSpacing.sm),
          Text(
            context.tr('imp.reuploadNote'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
          ),
        ],
      ),
    ),
    const SizedBox(height: EcomsbdSpacing.md),
    OutlinedButton(
      onPressed: _restart,
      style: OutlinedButton.styleFrom(
        minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
        shape: const StadiumBorder(),
        textStyle: EcomsbdType.label,
      ),
      child: Text(context.tr('imp.importAnother')),
    ),
  ];
}

class _TemplateTile extends StatelessWidget {
  const _TemplateTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      selected: selected,
      child: Material(
        color: selected ? EcomsbdColors.orangeSoft : Colors.white,
        borderRadius: EcomsbdRadii.cardMedium,
        child: InkWell(
          onTap: onTap,
          borderRadius: EcomsbdRadii.cardMedium,
          child: Container(
            padding: const EdgeInsets.all(EcomsbdSpacing.md),
            decoration: BoxDecoration(
              borderRadius: EcomsbdRadii.cardMedium,
              border: Border.all(
                color: selected ? EcomsbdColors.orange : EcomsbdColors.stroke,
              ),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Icon(
                  icon,
                  size: 20,
                  color: selected
                      ? EcomsbdColors.orange
                      : EcomsbdColors.rowIconInk,
                ),
                const SizedBox(height: EcomsbdSpacing.xs),
                Text(title, style: EcomsbdType.bodyStrong),
                const SizedBox(height: 2),
                Text(
                  subtitle,
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _ColumnMapRow extends StatelessWidget {
  const _ColumnMapRow({
    required this.field,
    required this.isRequired,
    required this.headers,
    required this.selected,
    required this.onChanged,
  });

  final String field;
  final bool isRequired;
  final List<String> headers;
  final String? selected;
  final ValueChanged<String?> onChanged;

  static String _label(String field) => switch (field) {
    'name' => _t('imp.colProductName'),
    'sku' => _t('imp.colSku'),
    'cost' => _t('imp.colCost'),
    'price' => _t('imp.colSellingPrice'),
    'stock' => _t('imp.colOpeningStock'),
    'description' => _t('imp.colDescription'),
    'phone' => _t('imp.colCustomerPhone'),
    'customer_name' => _t('imp.colCustomerName'),
    'product' => _t('imp.colProduct'),
    'quantity' => _t('imp.colQuantity'),
    'amount' => _t('imp.colCodAmount'),
    'address' => _t('common.address'),
    'district' => _t('imp.colDistrict'),
    'area' => _t('imp.colArea'),
    'note' => _t('common.note'),
    _ => field,
  };

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.symmetric(
        horizontal: EcomsbdSpacing.md,
        vertical: EcomsbdSpacing.sm,
      ),
      borderRadius: EcomsbdRadii.cardMedium,
      child: Row(
        children: <Widget>[
          Expanded(
            child: Row(
              children: <Widget>[
                Flexible(child: Text(_label(field), style: EcomsbdType.body)),
                if (isRequired)
                  Text(
                    ' *',
                    style: EcomsbdType.body.copyWith(color: EcomsbdColors.red),
                  ),
              ],
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.xs),
          Expanded(
            child: DropdownButtonHideUnderline(
              child: DropdownButton<String?>(
                value: selected,
                isExpanded: true,
                hint: Text(
                  'Not used',
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted2,
                  ),
                ),
                items: <DropdownMenuItem<String?>>[
                  const DropdownMenuItem<String?>(child: Text('Not used')),
                  for (final header in headers)
                    DropdownMenuItem<String?>(
                      value: header,
                      child: Text(header, overflow: TextOverflow.ellipsis),
                    ),
                ],
                onChanged: onChanged,
                style: EcomsbdType.body.copyWith(color: EcomsbdColors.ink),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ProblemRow extends StatelessWidget {
  const _ProblemRow({required this.row});

  final ImportRowReport row;

  @override
  Widget build(BuildContext context) {
    final isError = row.errors.isNotEmpty;
    final tone = isError ? Tone.bad : Tone.warning;

    return Container(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: EcomsbdRadii.cardMedium,
        border: Border.all(color: tone.ink.withValues(alpha: 0.25)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            children: <Widget>[
              Text('Row ${row.rowNumber}', style: EcomsbdType.bodyStrong),
              const Spacer(),
              StatusChip(
                label: isError ? 'Cannot import' : 'Check this',
                tone: tone,
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          for (final message in <String>[...row.errors, ...row.warnings])
            Padding(
              padding: const EdgeInsets.only(bottom: 2),
              child: Text(
                message,
                style: EcomsbdType.caption.copyWith(color: tone.ink),
              ),
            ),
        ],
      ),
    );
  }
}
