import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../data/commerce/crm_repository.dart';
import '../../data/commerce/list_controllers.dart';
import '../../l10n/app_strings.dart';
import '../orders/order_detail_screen.dart';
import 'crm_widgets.dart';

class CrmRecordsScreen extends ConsumerStatefulWidget {
  const CrmRecordsScreen({
    required this.customerId,
    required this.kind,
    required this.canWrite,
    super.key,
  });
  final String customerId, kind;
  final bool canWrite;
  @override
  ConsumerState<CrmRecordsScreen> createState() => _CrmRecordsState();
}

class _CrmRecordsState extends ConsumerState<CrmRecordsScreen> {
  final _history = <String?>[];
  String? _cursor;
  bool _all = false, _busy = false;
  late Future<Map<String, dynamic>> _page;
  String get _base => '/customers/${widget.customerId}';
  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    _page = ref
        .read(crmRepositoryProvider)
        .page(
          widget.kind == 'orders'
              ? '/orders'
              : '$_base/${widget.kind == 'notes' ? 'timeline' : widget.kind}',
          cursor: _cursor,
          extra: {
            if (widget.kind == 'orders') 'customer_id': widget.customerId,
            if (widget.kind == 'notes') 'notes_only': true,
            if (widget.kind == 'follow-ups' && !_all) 'completed': false,
          },
        );
  }

  Future<void> _mutate(Future<void> Function() work) async {
    setState(() => _busy = true);
    try {
      await work();
      if (!mounted) return;
      ref.invalidate(crmCustomerProvider(widget.customerId));
      ref.invalidate(customerListProvider);
      setState(_load);
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(context.tr('crm.error'))));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _add() async {
    if (widget.kind == 'notes') {
      final text = await crmTextDialog(context, context.tr('crm.addNote'));
      if (text != null && mounted) {
        await _mutate(() async {
          await ref.read(crmRepositoryProvider).add('$_base/notes', {
            'text': text,
          });
          _cursor = null;
          _history.clear();
        });
      }
    } else {
      final task = await showDialog<Map<String, dynamic>>(
        context: context,
        builder: (_) => const _FollowUpDialog(),
      );
      if (task != null && mounted) {
        await _mutate(() async {
          await ref.read(crmRepositoryProvider).add('$_base/follow-ups', task);
          _cursor = null;
          _history.clear();
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final title = widget.kind == 'follow-ups' ? 'followups' : widget.kind;
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('crm.$title'))),
      body: FutureBuilder(
        future: _page,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return Center(
              child: TextButton(
                onPressed: () => setState(_load),
                child: Text(context.tr('common.retry')),
              ),
            );
          }
          if (!snapshot.hasData ||
              snapshot.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }
          final data = snapshot.data!;
          final items = data['items'] as List;
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              if (widget.kind == 'notes') Text(context.tr('crm.noteHint')),
              if (widget.canWrite &&
                  ['notes', 'follow-ups'].contains(widget.kind))
                FilledButton.icon(
                  onPressed: _busy ? null : _add,
                  icon: const Icon(Icons.add),
                  label: Text(
                    context.tr(
                      widget.kind == 'notes'
                          ? 'crm.addNote'
                          : 'crm.addFollowup',
                    ),
                  ),
                ),
              if (widget.kind == 'follow-ups')
                SwitchListTile(
                  title: Text(context.tr('crm.allTasks')),
                  value: _all,
                  onChanged: (value) => setState(() {
                    _all = value;
                    _cursor = null;
                    _history.clear();
                    _load();
                  }),
                ),
              if (items.isEmpty)
                Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text(context.tr('crm.empty')),
                ),
              for (final raw in items) _record(raw as Map<String, dynamic>),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
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
    );
  }

  Widget _record(Map<String, dynamic> row) {
    if (widget.kind == 'orders') {
      return Card(
        child: ListTile(
          title: Text(row['order_number'] as String),
          subtitle: Text(crmDate(context, row['created_at'])),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => Navigator.push(
            context,
            MaterialPageRoute<void>(
              builder: (_) => OrderDetailScreen(orderId: row['id'] as String),
            ),
          ),
        ),
      );
    }
    if (widget.kind == 'follow-ups') {
      return Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(row['text'] as String),
              Text(
                '${context.tr('crm.${row['state']}')} · ${crmDate(context, row['due_at'])}',
              ),
              Text(
                '${context.tr('crm.assignee')}: ${crmActor(context, row['assignee_name'], row['assignee_id'])}',
              ),
              Text(crmActor(context, row['author_name'], row['created_by'])),
              if (widget.canWrite)
                TextButton(
                  onPressed: _busy
                      ? null
                      : () => _mutate(
                          () => ref
                              .read(crmRepositoryProvider)
                              .complete(
                                widget.customerId,
                                row['id'] as String,
                                row['completed_at'] == null,
                              ),
                        ),
                  child: Text(
                    context.tr(
                      row['completed_at'] == null
                          ? 'crm.complete'
                          : 'crm.reopen',
                    ),
                  ),
                ),
            ],
          ),
        ),
      );
    }
    return Card(
      child: ListTile(
        title: Text(context.tr('crm.${row['kind']}')),
        subtitle: Text(
          [
            if (row['text'] != null) row['text'] as String,
            crmDate(context, row['created_at']),
            if (row['actor_id'] != null)
              crmActor(context, row['actor_name'], row['actor_id']),
          ].join('\n'),
        ),
        isThreeLine: true,
        onTap: row['order_id'] == null
            ? null
            : () => Navigator.push(
                context,
                MaterialPageRoute<void>(
                  builder: (_) =>
                      OrderDetailScreen(orderId: row['order_id'] as String),
                ),
              ),
      ),
    );
  }
}

class _FollowUpDialog extends StatefulWidget {
  const _FollowUpDialog();
  @override
  State<_FollowUpDialog> createState() => _FollowUpDialogState();
}

class _FollowUpDialogState extends State<_FollowUpDialog> {
  final _text = TextEditingController();
  DateTime? _due;
  Map<String, dynamic>? _assignee;
  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _pickDue() async {
    final now = DateTime.now();
    final date = await showDatePicker(
      context: context,
      initialDate: now,
      firstDate: now.subtract(const Duration(days: 365)),
      lastDate: now.add(const Duration(days: 3650)),
    );
    if (date == null || !mounted) return;
    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.now(),
    );
    if (time != null && mounted) {
      setState(
        () => _due = DateTime(
          date.year,
          date.month,
          date.day,
          time.hour,
          time.minute,
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(context.tr('crm.addFollowup')),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(
            controller: _text,
            maxLength: 2000,
            maxLines: 3,
            decoration: InputDecoration(labelText: context.tr('crm.task')),
            onChanged: (_) => setState(() {}),
          ),
          TextButton(
            onPressed: _pickDue,
            child: Text(
              _due == null
                  ? context.tr('crm.due')
                  : crmDate(context, _due!.toIso8601String()),
            ),
          ),
          TextButton(
            onPressed: () async {
              final item = await pickCrmItem(context, kind: 'members');
              if (mounted && item != null) setState(() => _assignee = item);
            },
            child: Text(
              _assignee?['name'] as String? ?? context.tr('crm.assignee'),
            ),
          ),
          if (_assignee != null)
            TextButton(
              onPressed: () => setState(() => _assignee = null),
              child: Text(context.tr('crm.unassigned')),
            ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: Text(context.tr('crm.close')),
      ),
      FilledButton(
        onPressed: _text.text.trim().isEmpty || _due == null
            ? null
            : () => Navigator.pop(context, {
                'text': _text.text.trim(),
                'due_at': _due!.toUtc().toIso8601String(),
                'assignee_id': _assignee?['id'],
              }),
        child: Text(context.tr('crm.save')),
      ),
    ],
  );
}
