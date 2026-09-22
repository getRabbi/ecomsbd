import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/team/models.dart';
import '../../data/team/team_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';

/// Team.
///
/// Three things this screen refuses to do:
///
/// * **Show anyone's phone number.** Members are listed by their masked number
///   and invitations by their last four digits, because that is all the server
///   will send. A team screen is not a contact list.
/// * **Enforce anything by hiding a button.** Every rule here — the last owner,
///   the seat limit, who may change a role — is enforced on the server, and
///   this screen surfaces the server's refusal rather than pretending it
///   decided. What it hides is the *self* actions, which is a courtesy against
///   mis-taps, not a control.
/// * **Explain a role by its name.** A role is described by what it unlocks, in
///   the seller's language.
class TeamScreen extends ConsumerWidget {
  const TeamScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final members = ref.watch(teamMembersProvider);

    return DetailScaffold(
      title: context.tr('team.title'),
      children: <Widget>[
        const _PendingForMe(),
        members.when(
          loading: () => SkeletonLoader.card(height: 180),
          error: (error, _) => error is ApiError
              ? ErrorStateCard(
                  error: error,
                  onRetry: () => ref.invalidate(teamMembersProvider),
                )
              : EmptyState(
                  icon: Icons.error_outline,
                  title: context.tr('common.couldNotLoad'),
                  message: '$error',
                ),
          data: (rows) => _Members(members: rows),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        const _Invitations(),
        const SizedBox(height: EcomsbdSpacing.md),
        const _RolesExplained(),
      ],
    );
  }
}

/// Invitations waiting for the person looking at the screen.
///
/// Rendered above everything else because it is the only part of this screen
/// that is about *them* rather than about the shop.
class _PendingForMe extends ConsumerWidget {
  const _PendingForMe();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(myInvitationsProvider);

    return pending.when(
      loading: () => const SizedBox.shrink(),
      error: (_, __) => const SizedBox.shrink(),
      data: (rows) {
        if (rows.isEmpty) {
          return const SizedBox.shrink();
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            for (final invitation in rows) ...<Widget>[
              _PendingInvitationCard(invitation: invitation),
              const SizedBox(height: EcomsbdSpacing.sm),
            ],
            const SizedBox(height: EcomsbdSpacing.xs),
          ],
        );
      },
    );
  }
}

class _PendingInvitationCard extends ConsumerStatefulWidget {
  const _PendingInvitationCard({required this.invitation});

  final PendingInvitation invitation;

  @override
  ConsumerState<_PendingInvitationCard> createState() =>
      _PendingInvitationCardState();
}

class _PendingInvitationCardState
    extends ConsumerState<_PendingInvitationCard> {
  bool _busy = false;

  Future<void> _accept() async {
    setState(() => _busy = true);
    try {
      await ref
          .read(teamRepositoryProvider)
          .acceptInvitation(widget.invitation.invitationId);
      ref.invalidate(myInvitationsProvider);
      ref.invalidate(teamMembersProvider);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              context.tr('team.joined', <String, Object?>{
                'shop': widget.invitation.shopName,
              }),
            ),
          ),
        );
      }
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final invitation = widget.invitation;

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              const Icon(
                Icons.mark_email_unread_outlined,
                size: 18,
                color: EcomsbdColors.orange,
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  context.tr('team.invitedYou', <String, Object?>{
                    'shop': invitation.shopName,
                  }),
                  style: EcomsbdType.bodyStrong,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            // What the role unlocks, not what it is called.
            invitation.role.summary,
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          FilledButton(
            onPressed: _busy ? null : _accept,
            style: _primaryButton,
            child: Text(
              _busy ? context.tr('common.pleaseWait') : context.tr('team.join'),
            ),
          ),
        ],
      ),
    );
  }
}

class _Members extends ConsumerWidget {
  const _Members({required this.members});

  final List<TeamMember> members;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final active = <TeamMember>[
      for (final member in members)
        if (member.isActive) member,
    ];

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  context.tr('team.members'),
                  style: EcomsbdType.sectionTitle,
                ),
              ),
              Text(
                '${active.length}',
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          for (final member in active) _MemberRow(member: member),
          const SizedBox(height: EcomsbdSpacing.sm),
          OutlinedButton.icon(
            onPressed: () => InviteSheet.show(context),
            icon: const Icon(Icons.person_add_alt, size: 16),
            label: Text(context.tr('team.invite')),
          ),
        ],
      ),
    );
  }
}

class _MemberRow extends ConsumerStatefulWidget {
  const _MemberRow({required this.member});

  final TeamMember member;

  @override
  ConsumerState<_MemberRow> createState() => _MemberRowState();
}

class _MemberRowState extends ConsumerState<_MemberRow> {
  bool _busy = false;

  Future<void> _guard(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
      ref.invalidate(teamMembersProvider);
    } on ApiError catch (error) {
      if (mounted) {
        // The server's own refusal — "a shop must always have at least one
        // Owner", "your plan has no seats left" — shown as it was given.
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _changeRole() async {
    final chosen = await RolePickerSheet.show(
      context,
      current: widget.member.role,
    );
    if (chosen == null || chosen == widget.member.role) {
      return;
    }
    await _guard(
      () => ref
          .read(teamRepositoryProvider)
          .changeRole(widget.member.userId, chosen),
    );
  }

  Future<void> _remove() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(
          context.tr('team.removeTitle', <String, Object?>{
            'name': widget.member.name,
          }),
        ),
        content: Text(context.tr('team.removeBody')),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(context.tr('common.keepIt')),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(context.tr('team.remove')),
          ),
        ],
      ),
    );
    if (confirmed != true) {
      return;
    }
    await _guard(
      () => ref.read(teamRepositoryProvider).removeMember(widget.member.userId),
    );
  }

  @override
  Widget build(BuildContext context) {
    final member = widget.member;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Flexible(child: Text(member.name, style: EcomsbdType.body)),
                    if (member.isSelf) ...<Widget>[
                      const SizedBox(width: 6),
                      Text(
                        context.tr('team.you'),
                        style: EcomsbdType.caption.copyWith(
                          color: EcomsbdColors.muted2,
                        ),
                      ),
                    ],
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  member.role.label,
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
            ),
          ),
          // Hidden for yourself as a courtesy against mis-taps, not as a
          // control: the server refuses a self-demotion that would leave the
          // shop without an owner whatever this screen shows.
          if (!member.isSelf)
            PopupMenuButton<String>(
              enabled: !_busy,
              icon: const Icon(
                Icons.more_horiz,
                size: 18,
                color: EcomsbdColors.muted,
              ),
              onSelected: (value) {
                if (value == 'role') {
                  _changeRole();
                } else if (value == 'remove') {
                  _remove();
                }
              },
              itemBuilder: (context) => <PopupMenuEntry<String>>[
                PopupMenuItem<String>(
                  value: 'role',
                  child: Text(context.tr('team.changeRole')),
                ),
                PopupMenuItem<String>(
                  value: 'remove',
                  child: Text(context.tr('team.remove')),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

/// Invitations this shop is waiting on.
class _Invitations extends ConsumerWidget {
  const _Invitations();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final invitations = ref.watch(teamInvitationsProvider);

    return invitations.when(
      loading: () => const SizedBox.shrink(),
      error: (_, __) => const SizedBox.shrink(),
      data: (rows) {
        final open = <TeamInvitation>[
          for (final row in rows)
            if (row.state.isOpen) row,
        ];
        if (open.isEmpty) {
          return const SizedBox.shrink();
        }
        return GlassCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(
                context.tr('team.waitingOn'),
                style: EcomsbdType.sectionTitle,
              ),
              const SizedBox(height: 3),
              Text(
                context.tr('team.waitingOnSub'),
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
              const SizedBox(height: EcomsbdSpacing.sm),
              for (final invitation in open)
                _InvitationRow(invitation: invitation),
            ],
          ),
        );
      },
    );
  }
}

class _InvitationRow extends ConsumerStatefulWidget {
  const _InvitationRow({required this.invitation});

  final TeamInvitation invitation;

  @override
  ConsumerState<_InvitationRow> createState() => _InvitationRowState();
}

class _InvitationRowState extends ConsumerState<_InvitationRow> {
  bool _busy = false;

  Future<void> _revoke() async {
    setState(() => _busy = true);
    try {
      await ref
          .read(teamRepositoryProvider)
          .revokeInvitation(widget.invitation.id);
      ref.invalidate(teamInvitationsProvider);
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final invitation = widget.invitation;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(invitation.name, style: EcomsbdType.body),
                const SizedBox(height: 2),
                Text(
                  invitation.role.label,
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
            ),
          ),
          StatusChip(label: invitation.state.label, tone: Tone.warning),
          const SizedBox(width: EcomsbdSpacing.xs),
          TextButton(
            onPressed: _busy ? null : _revoke,
            child: Text(context.tr('team.withdraw')),
          ),
        ],
      ),
    );
  }
}

/// What each role unlocks, from the server's own matrix.
class _RolesExplained extends ConsumerWidget {
  const _RolesExplained();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(context.tr('team.rolesTitle'), style: EcomsbdType.sectionTitle),
          const SizedBox(height: EcomsbdSpacing.sm),
          for (final role in TeamRole.assignable) ...<Widget>[
            Text(role.label, style: EcomsbdType.bodyStrong),
            const SizedBox(height: 2),
            Text(
              role.summary,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.xs),
          ],
        ],
      ),
    );
  }
}

/// Invite someone by phone number.
class InviteSheet extends ConsumerStatefulWidget {
  const InviteSheet({super.key});

  static Future<void> show(BuildContext context) {
    return showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => const InviteSheet(),
    );
  }

  @override
  ConsumerState<InviteSheet> createState() => _InviteSheetState();
}

class _InviteSheetState extends ConsumerState<InviteSheet> {
  final TextEditingController _phone = TextEditingController();
  final TextEditingController _name = TextEditingController();
  TeamRole _role = TeamRole.orderOperator;
  bool _busy = false;
  ApiError? _error;

  @override
  void dispose() {
    _phone.dispose();
    _name.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref
          .read(teamRepositoryProvider)
          .invite(
            phone: _phone.text.trim(),
            role: _role,
            displayName: _name.text.trim(),
          );
      ref.invalidate(teamInvitationsProvider);
      if (mounted) {
        Navigator.of(context).pop();
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

  @override
  Widget build(BuildContext context) {
    final insets = MediaQuery.viewInsetsOf(context).bottom;
    final canSubmit = _phone.text.trim().isNotEmpty;

    return Padding(
      padding: EdgeInsets.only(bottom: insets),
      child: Container(
        decoration: const BoxDecoration(
          color: EcomsbdColors.backgroundLight,
          borderRadius: BorderRadius.vertical(
            top: Radius.circular(EcomsbdRadii.lg),
          ),
        ),
        padding: const EdgeInsets.fromLTRB(
          EcomsbdSpacing.lg,
          EcomsbdSpacing.md,
          EcomsbdSpacing.lg,
          EcomsbdSpacing.xl,
        ),
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Text(
                context.tr('team.inviteTitle'),
                style: EcomsbdType.sectionTitle,
              ),
              const SizedBox(height: 3),
              Text(
                // Says plainly that this is an offer, not an addition.
                context.tr('team.inviteBody'),
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              LabelledField(
                label: context.tr('common.phone'),
                controller: _phone,
                hint: context.tr('team.phoneHint'),
                keyboardType: TextInputType.phone,
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: EcomsbdSpacing.sm),
              LabelledField(
                label: context.tr('team.nameOptional'),
                controller: _name,
                hint: context.tr('team.nameHint'),
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              Text(context.tr('team.role'), style: EcomsbdType.label),
              const SizedBox(height: EcomsbdSpacing.xs),
              for (final role in TeamRole.assignable)
                _RoleOption(
                  role: role,
                  selected: role == _role,
                  onTap: () => setState(() => _role = role),
                ),
              if (_error != null) ...<Widget>[
                const SizedBox(height: EcomsbdSpacing.sm),
                Text(
                  _error!.displayMessage,
                  style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
                ),
              ],
              const SizedBox(height: EcomsbdSpacing.md),
              FilledButton(
                onPressed: _busy || !canSubmit ? null : _submit,
                style: _primaryButton,
                child: Text(
                  _busy
                      ? context.tr('common.pleaseWait')
                      : context.tr('team.sendInvite'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Pick a role, described by what it unlocks.
class RolePickerSheet extends StatelessWidget {
  const RolePickerSheet({required this.current, super.key});

  final TeamRole current;

  static Future<TeamRole?> show(
    BuildContext context, {
    required TeamRole current,
  }) {
    return showModalBottomSheet<TeamRole>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => RolePickerSheet(current: current),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        color: EcomsbdColors.backgroundLight,
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(EcomsbdRadii.lg),
        ),
      ),
      padding: const EdgeInsets.fromLTRB(
        EcomsbdSpacing.lg,
        EcomsbdSpacing.md,
        EcomsbdSpacing.lg,
        EcomsbdSpacing.xl,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(context.tr('team.chooseRole'), style: EcomsbdType.sectionTitle),
          const SizedBox(height: EcomsbdSpacing.md),
          for (final role in TeamRole.assignable)
            _RoleOption(
              role: role,
              selected: role == current,
              onTap: () => Navigator.of(context).pop(role),
            ),
        ],
      ),
    );
  }
}

class _RoleOption extends StatelessWidget {
  const _RoleOption({
    required this.role,
    required this.selected,
    required this.onTap,
  });

  final TeamRole role;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Material(
        type: MaterialType.transparency,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(EcomsbdRadii.sm),
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.all(EcomsbdSpacing.sm),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(EcomsbdRadii.sm),
              border: Border.all(
                color: selected ? EcomsbdColors.orange : EcomsbdColors.stroke,
                width: selected ? 1.5 : 1,
              ),
              color: selected ? EcomsbdColors.orangeSoft : null,
            ),
            child: Row(
              children: <Widget>[
                Icon(
                  selected
                      ? Icons.radio_button_checked
                      : Icons.radio_button_unchecked,
                  size: 18,
                  color: selected ? EcomsbdColors.orange : EcomsbdColors.muted,
                ),
                const SizedBox(width: EcomsbdSpacing.xs),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(role.label, style: EcomsbdType.body),
                      Text(
                        role.summary,
                        style: EcomsbdType.caption.copyWith(
                          color: EcomsbdColors.muted,
                        ),
                      ),
                    ],
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

final ButtonStyle _primaryButton = FilledButton.styleFrom(
  backgroundColor: EcomsbdColors.orange,
  foregroundColor: Colors.white,
);
