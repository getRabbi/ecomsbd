'use client';

import { useState } from 'react';

import { DataTable, type Column } from '@/components/DataTable';
import { PageHeader } from '@/components/shell';
import { Card, Chip, type Tone } from '@/components/ui';
import { download } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import { describeError, isResumable, type ImportBatch as WizardBatch } from '@/lib/imports';
import { formatDate } from '@/lib/money';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';

import { ImportWizard } from './ImportWizard';

type ImportBatch = WizardBatch;

interface SavedMapping {
  id: string;
  name: string;
  template: string;
  mapping: Record<string, string>;
  use_count: number;
  last_used_at: string | null;
}

/**
 * Imports.
 *
 * The screen the desktop is genuinely better at. Mapping eleven columns from a
 * courier export is miserable on a phone and ordinary at a keyboard, and a
 * failed-row file is something a seller opens in Excel — on the machine they
 * are already sitting at.
 *
 * Two things here that the phone does not give:
 *
 * **History.** Every import this shop has run, including the failed ones, which
 * are the ones a seller comes looking for.
 *
 * **The failed rows as a file.** Their own columns in their own order, plus the
 * row number and the reason — so the download is an input they fix and
 * re-upload, not a report they read and then retype from.
 *
 * **The whole flow at a keyboard.** Upload, preview, map, validate, import and
 * summary run in {@link ImportWizard} against the same endpoints the phone
 * uses. An import left part-way — mapped but never validated, validated but
 * never started, or still running in the background — is picked up from its
 * history row rather than uploaded again.
 */
export default function ImportsPage() {
  const { t, locale } = useSession();
  const [downloading, setDownloading] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // `undefined`: no wizard. `null`: a new import. A batch: resuming that one.
  const [wizard, setWizard] = useState<ImportBatch | null | undefined>(undefined);

  const imports = useApi<ImportBatch[]>('/imports', { limit: 50 });
  const mappings = useApi<SavedMapping[]>('/imports/mappings');

  async function downloadErrors(batch: ImportBatch) {
    setDownloading(batch.id);
    setNotice(null);
    try {
      await download(
        `/imports/${batch.id}/errors.csv`,
        `${batch.original_filename}-errors.csv`,
      );
    } catch (caught) {
      setNotice(describeError(caught, t));
    } finally {
      setDownloading(null);
    }
  }

  function openWizard(batch: ImportBatch | null) {
    setNotice(null);
    setWizard(batch);
    window.scrollTo({ top: 0 });
  }

  function closeWizard(message: string | null) {
    setWizard(undefined);
    setNotice(message);
    imports.reload();
    mappings.reload();
  }

  const columns: Column<ImportBatch>[] = [
    {
      key: 'file',
      header: t('imp.file'),
      render: (row) => (
        <>
          <div className="table__primary">{row.original_filename}</div>
          <div className="table__sub">{row.template.toLowerCase()}</div>
        </>
      ),
    },
    {
      key: 'when',
      header: t('imp.when'),
      render: (row) => formatDate(row.committed_at ?? row.created_at, { locale }),
    },
    { key: 'rows', header: t('imp.rows'), numeric: true, render: (row) => row.row_count },
    {
      key: 'created',
      header: t('imp.created'),
      numeric: true,
      render: (row) => row.created_count,
    },
    {
      key: 'failed',
      header: t('imp.failedRows'),
      numeric: true,
      render: (row) => {
        const failed = row.invalid_count + row.duplicate_count;
        return failed > 0 ? <Chip label={`${failed}`} tone="warn" /> : '—';
      },
    },
    {
      key: 'state',
      header: t('imp.state'),
      render: (row) => (
        <Chip label={t(`impst.${row.status}` as StringKey)} tone={toneForStatus(row.status)} />
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (row) => (
        <span style={{ display: 'inline-flex', gap: 6 }}>
          {isResumable(row) ? (
            <button
              type="button"
              className="btn btn--sm"
              disabled={wizard !== undefined}
              onClick={() => openWizard(row)}
            >
              {row.status === 'COMMITTING' ? t('impw.watch') : t('impw.continue')}
            </button>
          ) : null}
          {row.invalid_count + row.duplicate_count > 0 ? (
            <button
              type="button"
              className="btn btn--sm"
              disabled={downloading === row.id}
              onClick={() => downloadErrors(row)}
            >
              {t('imp.downloadErrors')}
            </button>
          ) : null}
        </span>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title={t('imp.title')}
        subtitle={t('imp.subtitle')}
        actions={
          <button
            type="button"
            className="btn btn--primary"
            disabled={wizard !== undefined}
            onClick={() => openWizard(null)}
          >
            {t('impw.new')}
          </button>
        }
      />

      <div className="content">
        {wizard !== undefined ? (
          <div style={{ marginBottom: 18 }}>
            {/* Keyed so a resumed import never inherits another's state. */}
            <ImportWizard key={wizard?.id ?? 'new'} resume={wizard} onClose={closeWizard} />
          </div>
        ) : null}

        <Card padded={false}>
          <DataTable
            columns={columns}
            rows={imports.data ?? []}
            loading={imports.loading}
            error={imports.error}
            onRetry={imports.reload}
            emptyTitle={t('imp.empty')}
            emptyHint={t('imp.emptyHint')}
            toolbar={notice ? <span className="card__hint">{notice}</span> : undefined}
          />
        </Card>

        <div style={{ marginTop: 18 }}>
          <Card title={t('imp.mappings')} hint={t('imp.mappingsHint')}>
            {mappings.loading ? (
              <p className="card__hint">{t('common.loading')}</p>
            ) : (mappings.data ?? []).length === 0 ? (
              <p className="card__hint">{t('common.nothingHere')}</p>
            ) : (
              (mappings.data ?? []).map((mapping) => (
                <div className="row" key={mapping.id}>
                  <span>
                    <strong>{mapping.name}</strong>
                    <span className="table__sub"> · {mapping.template.toLowerCase()}</span>
                  </span>
                  <span className="row__label">
                    {t('imp.usedTimes', { count: mapping.use_count })}
                  </span>
                </div>
              ))
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

function toneForStatus(status: string): Tone {
  switch (status) {
    case 'COMMITTED':
      return 'good';
    case 'FAILED':
      return 'bad';
    case 'COMMITTING':
      return 'warn';
    default:
      return 'neutral';
  }
}
