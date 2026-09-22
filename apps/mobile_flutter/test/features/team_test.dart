import 'package:ecomsbd/data/team/models.dart';
import 'package:ecomsbd/features/settings/team_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

/// Team and roles.
///
/// The claims under test are the ones that decide who can do what:
///
/// * an invitation is an offer — it is shown as waiting, not as a member;
/// * the roster never shows anyone's full phone number, because the server
///   never sends one;
/// * a role is explained by what it unlocks, not by its name;
/// * the server's refusals — the last owner, the seat limit — are surfaced as
///   given rather than second-guessed by the screen;
/// * V1's role names still resolve, so a member written before the rename does
///   not render as an unknown role with no permissions.
///
/// Copy assertions are in English: the test harness renders with the English
/// locale. Provider and technical terms stay English in both languages anyway,
/// so those assertions hold either way.

Map<String, dynamic> _member(
  String userId, {
  String role = 'ORDER_OPERATOR',
  bool isSelf = false,
  String? name,
  String masked = '01712****78',
}) {
  return <String, dynamic>{
    'user_id': userId,
    'role': role,
    'is_active': true,
    'is_self': isSelf,
    'masked_phone': masked,
    'display_name': name,
    'joined_at': '2026-09-18T05:00:00Z',
    'permissions': <String>['order.view'],
  };
}

Map<String, dynamic> _invitation(
  String id, {
  String role = 'VIEWER',
  String status = 'PENDING',
  String last4 = '4321',
  String? name,
}) {
  return <String, dynamic>{
    'id': id,
    'role': role,
    'status': status,
    'phone_last4': last4,
    'display_name': name,
    'invited_at': '2026-09-18T05:00:00Z',
    'expires_at': '2026-10-02T05:00:00Z',
    'permissions': <String>['order.view'],
  };
}

CommerceHarness _harness({
  List<dynamic> members = const <dynamic>[],
  List<dynamic> invitations = const <dynamic>[],
  List<dynamic> mine = const <dynamic>[],
}) {
  final harness = CommerceHarness();
  harness.adapter.onJson('GET', '/team', members);
  harness.adapter.onJson('GET', '/team/invitations', invitations);
  harness.adapter.onJson('GET', '/team/invitations/mine', mine);
  return harness;
}

void main() {
  group('Team screen', () {
    testWidgets('lists members by their masked number, never in full', (
      tester,
    ) async {
      final harness = _harness(
        members: <dynamic>[
          _member('u1', role: 'OWNER', isSelf: true, name: 'Rabbi'),
          _member('u2', masked: '01812****99'),
        ],
      );

      await pumpCommerceScreen(tester, const TeamScreen(), harness: harness);

      expect(find.text('Rabbi'), findsOneWidget);
      expect(find.text('01812****99'), findsOneWidget);
      // There is no widget on this screen that could hold a full number, and
      // no response that carries one.
      expect(find.textContaining('01812345699'), findsNothing);
    });

    testWidgets('marks the row belonging to the person looking', (
      tester,
    ) async {
      final harness = _harness(
        members: <dynamic>[_member('u1', role: 'OWNER', isSelf: true)],
      );

      await pumpCommerceScreen(tester, const TeamScreen(), harness: harness);

      expect(find.text('(you)'), findsOneWidget);
    });

    testWidgets('offers no actions against yourself', (tester) async {
      final harness = _harness(
        members: <dynamic>[
          _member('u1', role: 'OWNER', isSelf: true),
          _member('u2'),
        ],
      );

      await pumpCommerceScreen(tester, const TeamScreen(), harness: harness);

      // One menu, for the other member. A courtesy against mis-taps — the
      // server refuses a self-demotion that would orphan the shop regardless.
      expect(find.byType(PopupMenuButton<String>), findsOneWidget);
    });

    testWidgets('shows an invitation as waiting, not as a member', (
      tester,
    ) async {
      final harness = _harness(
        members: <dynamic>[_member('u1', role: 'OWNER', isSelf: true)],
        invitations: <dynamic>[_invitation('i1', last4: '4321')],
      );

      await pumpCommerceScreen(tester, const TeamScreen(), harness: harness);

      expect(find.text('Invitations sent'), findsOneWidget);
      expect(find.text('Waiting'), findsOneWidget);
      expect(find.text('•••• 4321'), findsOneWidget);
      // One member, and the invitee is not among them.
      expect(find.text('Members'), findsOneWidget);
      expect(find.text('1'), findsOneWidget);
    });

    testWidgets('a closed invitation is not listed as waiting', (tester) async {
      final harness = _harness(
        members: <dynamic>[_member('u1', role: 'OWNER', isSelf: true)],
        invitations: <dynamic>[_invitation('i1', status: 'REVOKED')],
      );

      await pumpCommerceScreen(tester, const TeamScreen(), harness: harness);

      expect(find.text('Invitations sent'), findsNothing);
    });

    testWidgets('explains every role by what it unlocks', (tester) async {
      final harness = _harness(
        members: <dynamic>[_member('u1', role: 'OWNER', isSelf: true)],
      );

      await pumpCommerceScreen(tester, const TeamScreen(), harness: harness);

      expect(find.text('What each role can do'), findsOneWidget);
      expect(
        find.textContaining('Never sees the money screens or the courier keys'),
        findsOneWidget,
      );
    });

    testWidgets('an invitation addressed to you offers joining', (
      tester,
    ) async {
      final harness = _harness(
        mine: <dynamic>[
          <String, dynamic>{
            'invitation_id': 'i9',
            'shop_name': 'Nusrat Collection',
            'role': 'FINANCE',
            'invited_at': '2026-09-18T05:00:00Z',
            'expires_at': '2026-10-02T05:00:00Z',
            'permissions': <String>['money.view'],
          },
        ],
      );

      await pumpCommerceScreen(tester, const TeamScreen(), harness: harness);

      expect(
        find.text('Nusrat Collection invited you to join'),
        findsOneWidget,
      );
      expect(find.text('Join this shop'), findsOneWidget);
    });

    testWidgets('surfaces the server refusal rather than deciding itself', (
      tester,
    ) async {
      final harness = _harness(
        members: <dynamic>[
          _member('u1', role: 'OWNER', isSelf: true),
          _member('u2', role: 'OWNER', name: 'Second Owner'),
        ],
      );
      harness.adapter.on(
        'DELETE',
        '/team/u2',
        (_) => const FakeReply(<String, dynamic>{
          'code': 'CONFLICT',
          'message_en': 'A shop must always have at least one Owner.',
          'message_bn': 'একটি দোকানে অন্তত একজন মালিক থাকতেই হবে।',
          'retryable': false,
        }, statusCode: 409),
      );

      await pumpCommerceScreen(tester, const TeamScreen(), harness: harness);

      await tester.tap(find.byType(PopupMenuButton<String>));
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));
      await tester.tap(find.text('Remove').last);
      await settle(tester, frames: 6, step: const Duration(milliseconds: 50));
      await tester.tap(find.text('Remove').last);
      await settle(tester, frames: 8, step: const Duration(milliseconds: 60));

      expect(
        find.textContaining('at least one Owner'),
        findsOneWidget,
        reason: 'the rule lives on the server; the screen reports it',
      );
    });
  });

  group('Model safety', () {
    test('V1 role names still resolve to the V2.1 roles', () {
      // A membership row or token written before the rename must keep working:
      // a role that stopped resolving would render as "unknown" with no
      // permissions, which is a lockout in the UI.
      expect(TeamRole.parse('PACKER'), TeamRole.orderOperator);
      expect(TeamRole.parse('ACCOUNTANT'), TeamRole.finance);
      expect(TeamRole.parse('ORDER_OPERATOR'), TeamRole.orderOperator);
      expect(TeamRole.parse('FINANCE'), TeamRole.finance);
    });

    test('a role the app does not know is never sent back as itself', () {
      final unknown = TeamRole.parse('SUPREME_LEADER');

      expect(unknown, TeamRole.unknown);
      // Falls back to the least privileged role rather than echoing a value
      // the server would have to interpret.
      expect(unknown.wireValue, 'VIEWER');
    });

    test('every assignable role round-trips through its wire value', () {
      for (final role in TeamRole.assignable) {
        expect(TeamRole.parse(role.wireValue), role);
      }
    });

    test('an invitation is only open while it is pending', () {
      expect(InvitationState.parse('PENDING').isOpen, isTrue);
      for (final closed in <String>['ACCEPTED', 'REVOKED', 'EXPIRED']) {
        expect(InvitationState.parse(closed).isOpen, isFalse, reason: closed);
      }
    });

    test('an invitation carries only the last four digits', () {
      final invitation = TeamInvitation.fromJson(_invitation('i1'));

      expect(invitation.phoneLast4, '4321');
      expect(invitation.name, '•••• 4321');
    });

    test('a pending invitation exposes the shop name and nothing else', () {
      final pending = PendingInvitation.fromJson(const <String, dynamic>{
        'invitation_id': 'i9',
        'shop_name': 'Nusrat Collection',
        'role': 'VIEWER',
      });

      // Being invited must not become a way to read a shop's data.
      expect(pending.shopName, 'Nusrat Collection');
      expect(pending.role, TeamRole.viewer);
    });
  });
}
