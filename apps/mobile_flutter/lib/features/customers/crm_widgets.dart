import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../data/commerce/crm_repository.dart';
import '../../l10n/app_strings.dart';

String crmDate(BuildContext context, Object? value) {
  final date = DateTime.tryParse('$value')?.toLocal();
  if (date == null) return '—';
  final l = MaterialLocalizations.of(context);
  return '${l.formatCompactDate(date)} ${l.formatTimeOfDay(TimeOfDay.fromDateTime(date))}';
}

String crmActor(BuildContext context, Object? name, Object? id) =>
    name as String? ??
    (id == null
        ? context.tr('crm.unassigned')
        : '${context.tr('crm.member')} ${id.toString().length > 8 ? id.toString().substring(0, 8) : id}');

class CrmDefinitions extends StatelessWidget {
  const CrmDefinitions(this.definitions, {super.key});
  final Map<String, dynamic> definitions;
  @override
  Widget build(BuildContext context) => ExpansionTile(
    title: Text(context.tr('crm.definitions')),
    children: [
      Padding(
        padding: const EdgeInsets.all(16),
        child: Text(
          '${context.tr('crm.rules', definitions)}\n\n${context.tr('crm.highRule', definitions)}',
        ),
      ),
    ],
  );
}

Future<Map<String, dynamic>?> pickCrmItem(
  BuildContext context, {
  String kind = 'tags',
}) => showDialog<Map<String, dynamic>>(
  context: context,
  builder: (_) => _Picker(kind: kind),
);

class _Picker extends ConsumerStatefulWidget {
  const _Picker({required this.kind});
  final String kind;
  @override
  ConsumerState<_Picker> createState() => _PickerState();
}

class _PickerState extends ConsumerState<_Picker> {
  final List<String?> _history = [];
  String? _cursor;
  late Future<Map<String, dynamic>> _page;
  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    _page = ref
        .read(crmRepositoryProvider)
        .page('/customers/crm/${widget.kind}', cursor: _cursor);
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(
      context.tr(widget.kind == 'tags' ? 'crm.tags' : 'crm.assignee'),
    ),
    content: SizedBox(
      width: 360,
      height: 360,
      child: FutureBuilder(
        future: _page,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return TextButton(
              onPressed: () => setState(_load),
              child: Text(context.tr('common.retry')),
            );
          }
          if (!snapshot.hasData) {
            return const Center(child: CircularProgressIndicator());
          }
          final data = snapshot.data!;
          final items = data['items'] as List;
          return Column(
            children: [
              Expanded(
                child: ListView(
                  children: [
                    if (items.isEmpty) Text(context.tr('crm.empty')),
                    for (final item in items)
                      ListTile(
                        title: Text(
                          item['name'] as String? ?? context.tr('crm.member'),
                        ),
                        onTap: () => Navigator.pop(
                          context,
                          item as Map<String, dynamic>,
                        ),
                      ),
                  ],
                ),
              ),
              Row(
                children: [
                  TextButton(
                    onPressed: _history.isEmpty
                        ? null
                        : () => setState(() {
                            _cursor = _history.removeLast();
                            _load();
                          }),
                    child: Text(context.tr('crm.previous')),
                  ),
                  TextButton(
                    onPressed: data['has_more'] == true
                        ? () => setState(() {
                            _history.add(_cursor);
                            _cursor = data['next_cursor'] as String?;
                            _load();
                          })
                        : null,
                    child: Text(context.tr('crm.next')),
                  ),
                ],
              ),
            ],
          );
        },
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: Text(context.tr('crm.close')),
      ),
    ],
  );
}

Future<String?> crmTextDialog(
  BuildContext context,
  String title, {
  int maxLength = 2000,
}) => showDialog<String>(
  context: context,
  builder: (_) => _TextDialog(title: title, maxLength: maxLength),
);

class _TextDialog extends StatefulWidget {
  const _TextDialog({required this.title, required this.maxLength});
  final String title;
  final int maxLength;
  @override
  State<_TextDialog> createState() => _TextDialogState();
}

class _TextDialogState extends State<_TextDialog> {
  final _text = TextEditingController();
  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.title),
    content: TextField(
      controller: _text,
      autofocus: true,
      maxLength: widget.maxLength,
      minLines: 2,
      maxLines: 5,
      onChanged: (_) => setState(() {}),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: Text(context.tr('crm.close')),
      ),
      FilledButton(
        onPressed: _text.text.trim().isEmpty
            ? null
            : () => Navigator.pop(context, _text.text.trim()),
        child: Text(context.tr('crm.save')),
      ),
    ],
  );
}
