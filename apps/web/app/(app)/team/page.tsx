'use client';

import { DataTable, type Column } from '@/components/DataTable';
import { PageHeader } from '@/components/shell';
import { Card, Chip } from '@/components/ui';
import { formatDate } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

interface Member {
  user_id: string;
  role: string;
  is_active: boolean;
  masked_phone: string | null;
  display_name: string | null;
  joined_at: string;
  permissions: string[];
  is_self: boolean;
}

interface Invitation {
  id: string;
  role: string;
  status: string;
  phone_last4: string;
  display_name: string | null;
  invited_at: string;
  expires_at: string;
}

/**
 * Team.
 *
 * Who is in this shop and what each of them may do. Both lists come from the
 * API, which enforces every rule behind them — `TEAM_MANAGE` to see this at
 * all, the last-owner protection, the seat limit. A role shown here is the role
 * the server will act on, not a copy this app keeps.
 *
 * Inviting and removing stay on the phone for now. The invitation flow is
 * built around a Bangladeshi mobile number and an OTP the invitee passes on
 * their own device, and splitting it across two clients would mean two places
 * to get the last-owner and seat rules wrong in the UI. The server would still
 * refuse — but a button that fails is worse than one that is not there.
 */
export default function TeamPage() {
  const { t, locale } = useSession();

  const members = useApi<Member[]>('/team');
  const invitations = useApi<Invitation[]>('/team/invitations');

  const rows = (members.data ?? [])
    .filter((member) => member.is_active)
    .map((member) => ({ ...member, id: member.user_id }));

  const columns: Column<Member & { id: string }>[] = [
    {
      key: 'member',
      header: t('team.member'),
      render: (row) => (
        <>
          <div className="table__primary">
            {row.display_name ?? row.masked_phone ?? '—'}
            {row.is_self ? <span className="table__sub"> · you</span> : null}
          </div>
          {/* Masked, because that is the only form the API sends. */}
          {row.display_name && row.masked_phone ? (
            <div className="table__sub">{row.masked_phone}</div>
          ) : null}
        </>
      ),
    },
    {
      key: 'role',
      header: t('team.role'),
      render: (row) => <Chip label={humaniseRole(row.role)} />,
    },
    {
      key: 'permissions',
      header: t('cour.capabilities'),
      render: (row) => (
        <span className="table__sub">{row.permissions.length} permissions</span>
      ),
    },
    {
      key: 'joined',
      header: t('team.joined'),
      render: (row) => formatDate(row.joined_at, { locale }),
    },
  ];

  const openInvitations = (invitations.data ?? []).filter(
    (invitation) => invitation.status === 'PENDING',
  );

  return (
    <>
      <PageHeader title={t('team.title')} subtitle={t('team.subtitle')} />

      <div className="content">
        <Card padded={false}>
          <DataTable
            columns={columns}
            rows={rows}
            loading={members.loading}
            error={members.error}
            onRetry={members.reload}
            emptyTitle={t('team.empty')}
          />
        </Card>

        {openInvitations.length > 0 ? (
          <div style={{ marginTop: 18 }}>
            <Card title={t('team.invitations')} hint={t('team.manageOnPhone')}>
              {openInvitations.map((invitation) => (
                <div className="row" key={invitation.id}>
                  <span>
                    <strong>
                      {invitation.display_name ?? `•••• ${invitation.phone_last4}`}
                    </strong>
                    <span className="table__sub"> · {humaniseRole(invitation.role)}</span>
                  </span>
                  <span className="row__label">
                    {t('team.expires')} {formatDate(invitation.expires_at, { locale })}
                  </span>
                </div>
              ))}
            </Card>
          </div>
        ) : (
          <div style={{ marginTop: 18 }}>
            <Card>
              <p className="card__hint">{t('team.manageOnPhone')}</p>
            </Card>
          </div>
        )}
      </div>
    </>
  );
}

/**
 * `ORDER_OPERATOR` → `Order operator`.
 *
 * V1's names still arrive from rows written before the rename, and both resolve
 * to the same role on the server — so they are shown rather than treated as
 * unknown.
 */
function humaniseRole(role: string): string {
  const canonical =
    role === 'PACKER' ? 'ORDER_OPERATOR' : role === 'ACCOUNTANT' ? 'FINANCE' : role;
  const words = canonical.replaceAll('_', ' ').toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
