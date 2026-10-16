'use client';

import { useState, type FormEvent } from 'react';

import { DataTable, type Column } from '@/components/DataTable';
import { PageHeader } from '@/components/shell';
import { Card, Chip, Drawer, Row } from '@/components/ui';
import { api } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { formatDate } from '@/lib/money';
import { errorMessage } from '@/lib/orders';
import { useSession } from '@/lib/session';
import { useShop } from '@/lib/shop';
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

/** Roles a member can be given. Owner is never offered: ownership is not handed out from a dropdown. */
const ASSIGNABLE = ['MANAGER', 'ORDER_OPERATOR', 'FINANCE', 'VIEWER'] as const;

/**
 * Team — who can do what in this shop.
 *
 * Inviting, revoking an invitation, changing a role and removing someone all
 * go through `/team`, which only the Owner may use (`team.manage`); the API
 * enforces it, this page only hides what the member cannot do. An invitation
 * is accepted by the invited person signing in, on the phone or here.
 */
export default function TeamPage() {
  const { t, locale } = useSession();
  const { can, me } = useShop();
  const manage = can('team.manage');

  const members = useApi<Member[]>('/team');
  const invitations = useApi<Invitation[]>(manage ? '/team/invitations' : null);
  const roles = useApi<Record<string, string[]>>('/team/roles');
  const [inviting, setInviting] = useState(false);
  const [editing, setEditing] = useState<Member | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const rows = (members.data ?? []).filter((member) => member.is_active).map((member) => ({ ...member, id: member.user_id }));

  async function run(work: () => Promise<unknown>, done: string) {
    setFailure(null);
    setNotice(null);
    try {
      await work();
      setNotice(done);
      members.reload();
      invitations.reload();
    } catch (caught) {
      setFailure(errorMessage(caught));
    }
  }

  const columns: Column<Member & { id: string }>[] = [
    {
      key: 'member',
      header: t('team.member'),
      render: (row) => (
        <>
          <div className="table__primary">
            {row.display_name ?? row.masked_phone ?? '—'}
            {row.is_self ? <span className="table__sub"> · {t('team.you')}</span> : null}
          </div>
          {/* Masked, because that is the only form the API sends. */}
          {row.display_name && row.masked_phone ? <div className="table__sub">{row.masked_phone}</div> : null}
        </>
      ),
    },
    { key: 'role', header: t('team.role'), render: (row) => <Chip label={roleLabel(row.role, t)} tone={row.role === 'OWNER' ? 'good' : 'neutral'} /> },
    { key: 'permissions', header: t('cour.capabilities'), render: (row) => <span className="table__sub">{t('team.permissionCount', { count: row.permissions.length })}</span> },
    { key: 'joined', header: t('team.joined'), render: (row) => formatDate(row.joined_at, { locale }) },
    {
      key: 'actions',
      header: '',
      render: (row) =>
        manage && !row.is_self && row.role !== 'OWNER' ? (
          <button type="button" className="btn btn--sm" onClick={(event) => { event.stopPropagation(); setEditing(row); }}>
            {t('team.manage')}
          </button>
        ) : null,
    },
  ];

  const openInvitations = (invitations.data ?? []).filter((invitation) => invitation.status === 'PENDING');

  return (
    <>
      <PageHeader
        title={t('team.title')}
        subtitle={t('team.subtitle')}
        actions={manage ? <button type="button" className="btn btn--primary" onClick={() => setInviting(true)}>{t('team.invite')}</button> : null}
      />

      <div className="content">
        {notice ? <div className="notice notice--good">{notice}</div> : null}
        {failure ? <p className="formerror" role="alert">{failure}</p> : null}

        <Card padded={false}>
          <DataTable columns={columns} rows={rows} loading={members.loading} error={members.error} onRetry={members.reload} emptyTitle={t('team.empty')} />
        </Card>

        <div className="grid2" style={{ marginTop: 18 }}>
          {manage ? (
            <Card title={t('team.invitations')} hint={t('team.inviteHint')}>
              {openInvitations.length === 0 ? <p className="card__hint">{t('team.noInvitations')}</p> : null}
              {openInvitations.map((invitation) => (
                <div className="row" key={invitation.id}>
                  <span>
                    <strong>{invitation.display_name ?? `•••• ${invitation.phone_last4}`}</strong>
                    <span className="table__sub"> · {roleLabel(invitation.role, t)} · {t('team.expires')} {formatDate(invitation.expires_at, { locale })}</span>
                  </span>
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => void run(() => api.delete(`/team/invitations/${invitation.id}`), t('team.revoked'))}>
                    {t('team.revoke')}
                  </button>
                </div>
              ))}
            </Card>
          ) : null}

          <Card title={t('team.yourAccess')}>
            <Row label={t('team.role')} value={me?.role ? roleLabel(me.role, t) : '—'} />
            <Row label={t('cour.capabilities')} value={t('team.permissionCount', { count: me?.permissions.length ?? 0 })} />
            <details>
              <summary className="card__hint">{t('team.showPermissions')}</summary>
              <p className="table__sub" style={{ lineHeight: 1.7 }}>{(me?.permissions ?? []).join(' · ')}</p>
            </details>
            {!manage ? <p className="card__hint">{t('team.ownerManages')}</p> : null}
          </Card>
        </div>

        {roles.data ? (
          <div style={{ marginTop: 18 }}>
            <Card title={t('team.matrix')} hint={t('team.matrixHint')} padded={false}>
              <div className="tablewrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t('team.permission')}</th>
                      {Object.keys(roles.data).map((role) => (
                        <th key={role}>{roleLabel(role, t)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Array.from(new Set(Object.values(roles.data).flat())).sort().map((permission) => (
                      <tr key={permission}>
                        <td className="table__sub">{permission}</td>
                        {Object.entries(roles.data ?? {}).map(([role, granted]) => (
                          <td key={role}>{granted.includes(permission) ? '✓' : ''}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        ) : null}
      </div>

      {inviting ? (
        <InviteDrawer
          onClose={() => setInviting(false)}
          onInvited={() => {
            setInviting(false);
            setNotice(t('team.invitedOk'));
            invitations.reload();
          }}
        />
      ) : null}
      {editing ? (
        <Drawer title={editing.display_name ?? editing.masked_phone ?? t('team.member')} onClose={() => setEditing(null)}>
          <MemberEditor
            member={editing}
            onChangeRole={(role) => {
              const id = editing.user_id;
              setEditing(null);
              void run(() => api.patch(`/team/${id}`, { role }), t('team.roleChanged'));
            }}
            onRemove={() => {
              const id = editing.user_id;
              setEditing(null);
              void run(() => api.delete(`/team/${id}`), t('team.removed'));
            }}
          />
        </Drawer>
      ) : null}
    </>
  );
}

function InviteDrawer({ onClose, onInvited }: { onClose: () => void; onInvited: () => void }) {
  const { t } = useSession();
  const [phone, setPhone] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState<string>('ORDER_OPERATOR');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post('/team/invitations', { phone: phone.trim(), role, display_name: name.trim() || undefined });
      onInvited();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer title={t('team.invite')} onClose={onClose}>
      <form onSubmit={submit}>
        <p className="card__hint">{t('team.inviteHint')}</p>
        <label className="field">
          <span className="field__label">{t('oc.phone')} *</span>
          <input className="input" type="tel" required minLength={6} maxLength={20} autoFocus value={phone} onChange={(event) => setPhone(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">{t('oc.name')}</span>
          <input className="input" maxLength={120} value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <RoleSelect value={role} onChange={setRole} />
        {error ? <p className="formerror">{error}</p> : null}
        <div className="formactions">
          <button type="button" className="btn" onClick={onClose}>{t('common.cancel')}</button>
          <button type="submit" className="btn btn--primary" disabled={busy}>{t('team.sendInvite')}</button>
        </div>
      </form>
    </Drawer>
  );
}

function MemberEditor({ member, onChangeRole, onRemove }: { member: Member; onChangeRole: (role: string) => void; onRemove: () => void }) {
  const { t } = useSession();
  const [role, setRole] = useState(member.role === 'PACKER' ? 'ORDER_OPERATOR' : member.role === 'ACCOUNTANT' ? 'FINANCE' : member.role);
  const [confirm, setConfirm] = useState(false);
  return (
    <>
      <RoleSelect value={role} onChange={setRole} />
      <button type="button" className="btn btn--primary btn--sm" onClick={() => onChangeRole(role)}>{t('team.saveRole')}</button>
      <div className="formsection">
        <div className="formsection__title">{t('team.remove')}</div>
        <p className="card__hint">{t('team.removeHint')}</p>
        {confirm ? (
          <button type="button" className="btn btn--sm" onClick={onRemove}>{t('team.confirmRemove')}</button>
        ) : (
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setConfirm(true)}>{t('team.remove')}</button>
        )}
      </div>
    </>
  );
}

function RoleSelect({ value, onChange }: { value: string; onChange: (role: string) => void }) {
  const { t } = useSession();
  return (
    <label className="field">
      <span className="field__label">{t('team.role')}</span>
      <select className="select" value={value} onChange={(event) => onChange(event.target.value)}>
        {ASSIGNABLE.map((role) => (
          <option key={role} value={role}>{roleLabel(role, t)}</option>
        ))}
      </select>
      <span className="field__hint">{t(`team.roleHint.${value}` as StringKey)}</span>
    </label>
  );
}

/**
 * The role as a seller reads it. V1's names (`PACKER`, `ACCOUNTANT`) still
 * arrive from older rows and resolve to the same role on the server.
 */
function roleLabel(role: string, t: (key: StringKey) => string): string {
  const canonical = role === 'PACKER' ? 'ORDER_OPERATOR' : role === 'ACCOUNTANT' ? 'FINANCE' : role;
  const key = `team.roleName.${canonical}` as StringKey;
  const label = t(key);
  return label === key ? canonical.replaceAll('_', ' ').toLowerCase() : label;
}
