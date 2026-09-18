'use client';

import Link from 'next/link';

import { PageHeader } from '@/components/shell';
import { Card, EmptyState, ErrorState, Tile } from '@/components/ui';
import { formatPaisa } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

/**
 * The seller's overview.
 *
 * Every figure here comes from `/money/summary`, which derives them from the
 * ledger rather than from a maintained counter. None is added up in the
 * browser: this screen shows what the backend computed, so the dashboard and
 * the Money screen cannot disagree about what a shop is owed.
 *
 * It also shows nothing the API does not return. There is no invented
 * "conversion rate", no margin trend and no forecast — a number on a money
 * screen has to be defensible, and one this app made up is not.
 */

interface MoneySummary {
  outstanding_paisa: number;
  settled_paisa: number;
  unpaid_parcel_count: number;
  courier_charge_paisa: number;
  cod_fee_paisa: number;
  return_charge_paisa: number;
  unknown_deduction_paisa: number;
  write_off_paisa: number;
  unexplained_payout_paisa: number;
  open_case_count: number;
  since: string | null;
  until: string | null;
}

export default function DashboardPage() {
  const { t, locale } = useSession();
  const { data, loading, error, reload } = useApi<MoneySummary>('/money/summary');

  return (
    <>
      <PageHeader title={t('dash.title')} subtitle={t('dash.subtitle')} />

      <div className="content">
        {error ? (
          <Card>
            <ErrorState error={error} onRetry={reload} />
          </Card>
        ) : (
          <>
            <div className="tiles">
              <Tile
                label={t('dash.codOutstanding')}
                value={loading ? '…' : formatPaisa(data?.outstanding_paisa, { locale })}
                hint={t('dash.codOutstandingHint')}
              />
              <Tile
                label={t('dash.awaitingPayout')}
                value={
                  loading
                    ? '…'
                    : `${data?.unpaid_parcel_count ?? 0}`
                }
                hint={t('dash.codOutstandingHint')}
              />
              <Tile
                label={t('dash.returns')}
                value={loading ? '…' : formatPaisa(data?.return_charge_paisa, { locale })}
              />
              <Tile
                label={t('dash.profit')}
                value={loading ? '…' : formatPaisa(data?.settled_paisa, { locale })}
              />
            </div>

            <Card title={t('dash.alerts')}>
              {loading ? (
                <p className="card__hint">{t('common.loading')}</p>
              ) : (
                <Alerts summary={data} />
              )}
            </Card>
          </>
        )}
      </div>
    </>
  );
}

/**
 * Alerts, each one derived from a figure the API actually returns.
 *
 * Deliberately few. An alert list that is always full is one nobody reads, so
 * each of these corresponds to money that is genuinely unaccounted for or a
 * case somebody has to open.
 */
function Alerts({ summary }: { summary: MoneySummary | null }) {
  const { t, locale } = useSession();

  if (!summary) {
    return <EmptyState title={t('common.couldNotLoad')} />;
  }

  const alerts: { href: string; label: string }[] = [];

  if (summary.open_case_count > 0) {
    alerts.push({
      href: '/reconciliation',
      label: `${summary.open_case_count} open ${
        summary.open_case_count === 1 ? 'case' : 'cases'
      }`,
    });
  }
  if (summary.unexplained_payout_paisa > 0) {
    alerts.push({
      href: '/reconciliation',
      label: `${formatPaisa(summary.unexplained_payout_paisa, {
        locale,
      })} paid but not matched to a parcel`,
    });
  }
  if (summary.unknown_deduction_paisa > 0) {
    // Kept separate from the known charge buckets rather than folded into
    // them: a deduction nobody can name is exactly what a seller should see.
    alerts.push({
      href: '/money',
      label: `${formatPaisa(summary.unknown_deduction_paisa, {
        locale,
      })} deducted without a reason we recognise`,
    });
  }

  if (alerts.length === 0) {
    return <p className="card__hint">{t('dash.allClear')}</p>;
  }

  return (
    <ul style={{ margin: 0, paddingLeft: 18 }}>
      {alerts.map((alert) => (
        <li key={alert.label} style={{ marginBottom: 6 }}>
          <Link href={alert.href} style={{ color: 'var(--brand-ink)', fontWeight: 600 }}>
            {alert.label}
          </Link>
        </li>
      ))}
    </ul>
  );
}
