'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';

import { Card, Chip, Tile, type Tone } from '@/components/ui';
import { ApiError, api, download, upload } from '@/lib/api';
import type { StringKey } from '@/lib/i18n';
import {
  MAX_UPLOAD_BYTES,
  POLL_INTERVAL_MS,
  TEMPLATE_FIELDS,
  describeError,
  fieldLabel,
  hasAcceptedExtension,
  type ImportBatch,
  type ImportCommitResult,
  type ImportRow,
  type ImportRowStatus,
  type ImportTemplate,
  type SavedMapping,
} from '@/lib/imports';
import { formatDate } from '@/lib/money';
import { useSession } from '@/lib/session';

type Step = 'upload' | 'map' | 'review' | 'running' | 'done';

const STEPS: { step: Step; label: StringKey }[] = [
  { step: 'upload', label: 'impw.stepUpload' },
  { step: 'map', label: 'impw.stepMap' },
  { step: 'review', label: 'impw.stepReview' },
  { step: 'running', label: 'impw.stepImport' },
  { step: 'done', label: 'impw.stepDone' },
];

const PREVIEW_ROWS = 8;
const ISSUE_ROWS = 200;
const UNMAPPED = '';

function stepFor(batch: ImportBatch): Step {
  switch (batch.status) {
    case 'UPLOADED':
    case 'MAPPED':
      return 'map';
    case 'VALIDATED':
      return 'review';
    case 'COMMITTING':
      return 'running';
    default:
      return 'done';
  }
}

/**
 * The import wizard: upload → preview and map → validate → import → summary.
 *
 * The browser never parses the file. It sends the bytes, then works from what
 * the API reports back — detected headers, sample rows, per-row errors — so a
 * five-thousand-row spreadsheet costs the tab no more than a fifty-row one, and
 * the rules that decide what gets imported live in exactly one place.
 *
 * Nothing is created until "Start import". Every step before it can be left or
 * gone back from freely: the upload and the dry run only write to the import's
 * own tables, which is why the file just shows in history as not imported.
 *
 * Starting the import is the one step that must not happen twice. The button
 * is disabled while the request is out, and a retry re-reads the import before
 * deciding anything: already committed goes straight to the summary, already
 * running is watched rather than started again. The API locks the batch on
 * commit as well, so even a request that slipped past this cannot double up.
 */
export function ImportWizard({
  resume,
  onClose,
}: {
  resume: ImportBatch | null;
  onClose: (message: string | null) => void;
}) {
  const { t, locale } = useSession();

  const [batch, setBatch] = useState<ImportBatch | null>(resume);
  const [step, setStep] = useState<Step>(resume ? stepFor(resume) : 'upload');

  const current = STEPS.findIndex((entry) => entry.step === step);

  return (
    <Card
      title={t('impw.title')}
      hint={batch ? batch.original_filename : t('impw.hint')}
      actions={
        step === 'running' ? null : (
          <button
            type="button"
            className="btn btn--sm"
            onClick={() =>
              onClose(step === 'done' || !batch ? null : t('impw.cancelledNothingImported'))
            }
          >
            {step === 'done' ? t('common.close') : t('common.cancel')}
          </button>
        )
      }
    >
      <ol className="stepper" aria-label={t('impw.title')}>
        {STEPS.map((entry, index) => (
          <li
            key={entry.step}
            className="stepper__item"
            data-state={index < current ? 'done' : index === current ? 'current' : 'todo'}
            aria-current={index === current ? 'step' : undefined}
          >
            <span className="stepper__index">{index + 1}</span>
            {t(entry.label)}
          </li>
        ))}
      </ol>

      {step === 'upload' ? (
        <UploadStep
          onUploaded={(uploaded) => {
            setBatch(uploaded);
            setStep('map');
          }}
        />
      ) : null}

      {step === 'map' && batch ? (
        <MapStep
          batch={batch}
          onBack={() => {
            // The uploaded file stays in history as not imported; a new
            // upload starts clean rather than inheriting this mapping.
            setBatch(null);
            setStep('upload');
          }}
          onValidated={(validated) => {
            setBatch(validated);
            setStep('review');
          }}
        />
      ) : null}

      {step === 'review' && batch ? (
        <ReviewStep
          batch={batch}
          onBack={() => setStep('map')}
          onStart={() => setStep('running')}
        />
      ) : null}

      {step === 'running' && batch ? (
        <RunStep
          batch={batch}
          onBatch={setBatch}
          onFinished={(finished) => {
            setBatch(finished);
            setStep('done');
          }}
          onBackToReview={(validated) => {
            setBatch(validated);
            setStep('review');
          }}
          onLeave={() => onClose(t('impw.runningInBackground'))}
        />
      ) : null}

      {step === 'done' && batch ? (
        <DoneStep
          batch={batch}
          locale={locale}
          onAnother={() => {
            setBatch(null);
            setStep('upload');
          }}
          onClose={() => onClose(null)}
        />
      ) : null}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* 1. Upload                                                                   */
/* -------------------------------------------------------------------------- */

function UploadStep({ onUploaded }: { onUploaded: (batch: ImportBatch) => void }) {
  const { t, locale } = useSession();
  const [template, setTemplate] = useState<ImportTemplate>('ORDERS');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);

  function pick(picked: File | null) {
    setError(null);
    if (!picked) {
      setFile(null);
      return;
    }
    if (!hasAcceptedExtension(picked.name)) {
      setFile(null);
      setError(t('impw.wrongType'));
      return;
    }
    if (picked.size > MAX_UPLOAD_BYTES) {
      setFile(null);
      setError(t('impw.tooLarge', { mb: MAX_UPLOAD_BYTES / (1024 * 1024) }));
      return;
    }
    setFile(picked);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || busy) {
      return;
    }
    setBusy(true);
    setError(null);
    controller.current = new AbortController();

    const form = new FormData();
    form.append('template', template);
    form.append('file', file, file.name);

    try {
      onUploaded(await upload<ImportBatch>('/imports', form, controller.current.signal));
    } catch (caught) {
      if (controller.current.signal.aborted) {
        return;
      }
      setError(describeUploadError(caught, t, locale));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="wizard__body">
      <fieldset className="wizard__choices" disabled={busy}>
        <legend className="field__label">{t('impw.whatImporting')}</legend>
        {(['ORDERS', 'PRODUCTS'] as const).map((option) => (
          <label key={option} className="choice" data-checked={template === option}>
            <input
              type="radio"
              name="template"
              value={option}
              checked={template === option}
              onChange={() => setTemplate(option)}
            />
            <span>
              <strong>{t(option === 'ORDERS' ? 'impw.orders' : 'impw.products')}</strong>
              <span className="table__sub">
                {t(option === 'ORDERS' ? 'impw.ordersHint' : 'impw.productsHint')}
              </span>
            </span>
          </label>
        ))}
      </fieldset>

      <label className="field">
        <span className="field__label">{t('impw.file')}</span>
        <input
          className="input"
          type="file"
          accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          disabled={busy}
          onChange={(event) => pick(event.target.files?.[0] ?? null)}
        />
        <span className="card__hint">
          {t('impw.fileHint', { mb: MAX_UPLOAD_BYTES / (1024 * 1024) })}
        </span>
      </label>

      {error ? (
        <p className="formerror" role="alert">
          {error}
        </p>
      ) : null}

      <div className="wizard__actions">
        <span />
        <button type="submit" className="btn btn--primary" disabled={!file || busy}>
          {busy ? t('impw.uploading') : t('impw.upload')}
        </button>
      </div>
    </form>
  );
}

function describeUploadError(
  caught: unknown,
  t: ReturnType<typeof useSession>['t'],
  locale: ReturnType<typeof useSession>['locale'],
): string {
  // The same bytes were committed before. Saying when, and how much it made,
  // tells the seller their data is already in rather than just "conflict".
  if (caught instanceof ApiError && caught.status === 409 && caught.details?.import_id) {
    const when = caught.details.committed_at;
    return t('impw.alreadyImported', {
      when: typeof when === 'string' ? formatDate(when, { locale }) : '—',
      count: Number(caught.details.created_count ?? 0),
    });
  }
  return describeError(caught, t);
}

/* -------------------------------------------------------------------------- */
/* 2. Preview and map                                                          */
/* -------------------------------------------------------------------------- */

function MapStep({
  batch,
  onBack,
  onValidated,
}: {
  batch: ImportBatch;
  onBack: () => void;
  onValidated: (batch: ImportBatch) => void;
}) {
  const { t } = useSession();
  const template = batch.template as ImportTemplate;
  const fields = TEMPLATE_FIELDS[template] ?? [];
  const headers = useMemo(
    () => batch.detected_headers.map((header) => String(header)),
    [batch.detected_headers],
  );

  const [mapping, setMapping] = useState<Record<string, string>>(() =>
    cleanMapping(batch.column_mapping, headers),
  );
  const [preview, setPreview] = useState<ImportRow[] | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedMapping[]>([]);
  const [saveName, setSaveName] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<ImportRow[]>(`/imports/${batch.id}/rows`, { limit: PREVIEW_ROWS }, controller.signal)
      .then(setPreview)
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setPreviewError(describeError(caught, t));
        }
      });
    // Only mappings whose columns exist in this file: offering one that does
    // not fit would have every row fail.
    api
      .get<SavedMapping[]>('/imports/mappings', { import_id: batch.id }, controller.signal)
      .then(setSaved)
      .catch(() => {
        // Saved mappings are a shortcut; the form works without them.
      });
    return () => controller.abort();
  }, [batch.id, t]);

  const missing = fields.filter((field) => field.required && !mapping[field.key]);
  const reverse = useMemo(() => {
    const byHeader = new Map<string, string>();
    for (const [key, header] of Object.entries(mapping)) {
      byHeader.set(header, key);
    }
    return byHeader;
  }, [mapping]);
  const reused = new Set(
    Object.values(mapping).filter(
      (header, index, all) => header && all.indexOf(header) !== index,
    ),
  );

  function setField(key: string, header: string) {
    setNotice(null);
    setMapping((previous) => {
      const next = { ...previous };
      if (header === UNMAPPED) {
        delete next[key];
      } else {
        next[key] = header;
      }
      return next;
    });
  }

  async function applySaved(mappingId: string) {
    if (!mappingId) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const applied = await api.post<ImportBatch>(`/imports/${batch.id}/mapping/${mappingId}`);
      setMapping(cleanMapping(applied.column_mapping, headers));
      setNotice(t('impw.mappingApplied'));
    } catch (caught) {
      setError(describeError(caught, t));
    } finally {
      setBusy(false);
    }
  }

  async function saveMapping() {
    const name = saveName.trim();
    if (!name) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<SavedMapping>('/imports/mappings', {
        name,
        template,
        mapping,
        source_headers: headers,
      });
      setSaved((previous) => [created, ...previous.filter((item) => item.id !== created.id)]);
      setSaveName('');
      setNotice(t('impw.mappingSaved'));
    } catch (caught) {
      setError(describeError(caught, t));
    } finally {
      setBusy(false);
    }
  }

  async function validate() {
    setBusy(true);
    setError(null);
    try {
      onValidated(
        await api.post<ImportBatch>(`/imports/${batch.id}/dry-run`, { column_mapping: mapping }),
      );
    } catch (caught) {
      const stillMissing = caught instanceof ApiError ? caught.details?.missing : null;
      setError(
        Array.isArray(stillMissing)
          ? t('impw.missingFields', {
              fields: stillMissing
                .map((key) => {
                  const label = fieldLabel(template, String(key));
                  return label ? t(label) : String(key);
                })
                .join(', '),
            })
          : describeError(caught, t),
      );
      setBusy(false);
    }
  }

  return (
    <div className="wizard__body">
      <section>
        <h3 className="wizard__heading">
          {t('impw.previewTitle', { rows: batch.row_count, columns: headers.length })}
        </h3>
        <p className="card__hint">{t('impw.previewHint')}</p>
        <div className="tablewrap wizard__preview">
          <table className="table">
            <thead>
              <tr>
                <th className="num">#</th>
                {headers.map((header) => {
                  const mapped = reverse.get(header);
                  const label = mapped ? fieldLabel(template, mapped) : null;
                  return (
                    <th key={header}>
                      <div>{header}</div>
                      {label ? <Chip label={`→ ${t(label)}`} tone="good" /> : null}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {preview === null && !previewError ? (
                <tr>
                  <td colSpan={headers.length + 1} className="card__hint">
                    {t('common.loading')}
                  </td>
                </tr>
              ) : null}
              {previewError ? (
                <tr>
                  <td colSpan={headers.length + 1} className="formerror">
                    {previewError}
                  </td>
                </tr>
              ) : null}
              {(preview ?? []).map((row) => (
                <tr key={row.row_number}>
                  <td className="num">{row.row_number}</td>
                  {headers.map((header) => (
                    <td key={header}>{displayCell(row.raw[header])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="wizard__mapping">
        <div>
          <h3 className="wizard__heading">{t('impw.mapTitle')}</h3>
          <p className="card__hint">{t('impw.mapHint')}</p>
          <table className="table">
            <thead>
              <tr>
                <th>{t('impw.ecomsbdField')}</th>
                <th>{t('impw.yourColumn')}</th>
                <th>{t('impw.sample')}</th>
              </tr>
            </thead>
            <tbody>
              {fields.map((field) => {
                const header = mapping[field.key] ?? UNMAPPED;
                const sample = header ? preview?.[0]?.raw[header] : undefined;
                return (
                  <tr key={field.key}>
                    <td>
                      <strong>{t(field.label)}</strong>{' '}
                      {field.required ? (
                        <Chip label={t('impw.required')} tone={header ? 'neutral' : 'bad'} />
                      ) : null}
                    </td>
                    <td>
                      <select
                        className="select"
                        value={header}
                        disabled={busy}
                        aria-label={t(field.label)}
                        onChange={(event) => setField(field.key, event.target.value)}
                      >
                        <option value={UNMAPPED}>{t('impw.notInFile')}</option>
                        {headers.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                      {header && reused.has(header) ? (
                        <div className="table__sub">{t('impw.columnReused')}</div>
                      ) : null}
                    </td>
                    <td className="table__sub">{sample === undefined ? '—' : displayCell(sample)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <aside className="wizard__side">
          <label className="field">
            <span className="field__label">{t('impw.useSaved')}</span>
            <select
              className="select"
              value=""
              disabled={busy || saved.length === 0}
              onChange={(event) => applySaved(event.target.value)}
            >
              <option value="">
                {saved.length === 0 ? t('impw.noSavedFits') : t('impw.pickSaved')}
              </option>
              {saved.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span className="field__label">{t('impw.saveAs')}</span>
            <input
              className="input"
              value={saveName}
              maxLength={80}
              placeholder={t('impw.saveAsPlaceholder')}
              disabled={busy}
              onChange={(event) => setSaveName(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="btn btn--sm"
            disabled={busy || !saveName.trim() || Object.keys(mapping).length === 0}
            onClick={saveMapping}
          >
            {t('impw.saveMapping')}
          </button>
          {notice ? <p className="card__hint">{notice}</p> : null}
        </aside>
      </section>

      {error ? (
        <p className="formerror" role="alert">
          {error}
        </p>
      ) : null}

      <div className="wizard__actions">
        <button type="button" className="btn" disabled={busy} onClick={onBack}>
          {t('impw.backToUpload')}
        </button>
        <span className="wizard__actionsright">
          {missing.length > 0 ? (
            <span className="card__hint">
              {t('impw.missingFields', {
                fields: missing.map((field) => t(field.label)).join(', '),
              })}
            </span>
          ) : null}
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy || missing.length > 0}
            onClick={validate}
          >
            {busy ? t('impw.validating') : t('impw.validate')}
          </button>
        </span>
      </div>
    </div>
  );
}

/** Drop mapped columns this file does not have, so the selects stay honest. */
function cleanMapping(mapping: Record<string, unknown>, headers: string[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, header] of Object.entries(mapping ?? {})) {
    if (typeof header === 'string' && headers.includes(header)) {
      result[key] = header;
    }
  }
  return result;
}

function displayCell(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '—';
  }
  const text = String(value);
  return text.length > 60 ? `${text.slice(0, 57)}…` : text;
}

/* -------------------------------------------------------------------------- */
/* 3. Review the dry run                                                       */
/* -------------------------------------------------------------------------- */

const ISSUE_FILTERS: { status: ImportRowStatus; label: StringKey; tone: Tone }[] = [
  { status: 'INVALID', label: 'impw.invalid', tone: 'bad' },
  { status: 'DUPLICATE', label: 'impw.duplicate', tone: 'warn' },
  { status: 'WARNING', label: 'impw.warning', tone: 'warn' },
];

function ReviewStep({
  batch,
  onBack,
  onStart,
}: {
  batch: ImportBatch;
  onBack: () => void;
  onStart: () => void;
}) {
  const { t } = useSession();
  const importable = batch.ready_count + batch.warning_count;
  const failing = batch.invalid_count + batch.duplicate_count;
  const counts: Record<string, number> = {
    INVALID: batch.invalid_count,
    DUPLICATE: batch.duplicate_count,
    WARNING: batch.warning_count,
  };
  const firstWithRows = ISSUE_FILTERS.find((filter) => (counts[filter.status] ?? 0) > 0)?.status ?? null;

  return (
    <div className="wizard__body">
      <div className="tiles">
        <Tile label={t('imp.rows')} value={String(batch.row_count)} />
        <Tile label={t('impw.ready')} value={String(importable)} hint={t('impw.readyHint')} />
        <Tile label={t('impw.duplicate')} value={String(batch.duplicate_count)} hint={t('impw.duplicateHint')} />
        <Tile label={t('impw.invalid')} value={String(batch.invalid_count)} hint={t('impw.invalidHint')} />
      </div>

      {firstWithRows ? (
        <IssueRows batch={batch} counts={counts} initial={firstWithRows} />
      ) : (
        <p className="card__hint">{t('impw.noIssues')}</p>
      )}

      {importable === 0 ? (
        <p className="formerror" role="alert">
          {t('impw.nothingToImport')}
        </p>
      ) : null}

      <div className="wizard__actions">
        <button type="button" className="btn" onClick={onBack}>
          {t('impw.backToMap')}
        </button>
        <span className="wizard__actionsright">
          {failing > 0 ? <ErrorDownload batch={batch} /> : null}
          <button
            type="button"
            className="btn btn--primary"
            disabled={!batch.can_commit || importable === 0}
            onClick={onStart}
          >
            {t(batch.template === 'PRODUCTS' ? 'impw.startProducts' : 'impw.startOrders', {
              count: importable,
            })}
          </button>
        </span>
      </div>
    </div>
  );
}

function IssueRows({
  batch,
  counts,
  initial,
}: {
  batch: ImportBatch;
  counts: Record<string, number>;
  initial: ImportRowStatus;
}) {
  const { t } = useSession();
  const [filter, setFilter] = useState<ImportRowStatus>(initial);
  const [rows, setRows] = useState<ImportRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const key = `${batch.id}|${batch.dry_run_at}|${filter}`;

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<ImportRow[]>(
        `/imports/${batch.id}/rows`,
        { status: filter, limit: ISSUE_ROWS },
        controller.signal,
      )
      .then((result) => {
        setRows(result);
        setError(null);
        setLoadedKey(key);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setError(describeError(caught, t));
          setLoadedKey(key);
        }
      });
    return () => controller.abort();
  }, [batch.id, filter, key, t]);

  const loading = loadedKey !== key;
  const mappedHeaders = Object.values(batch.column_mapping).map(String);

  return (
    <section>
      <div className="toolbar" role="tablist">
        {ISSUE_FILTERS.filter((entry) => (counts[entry.status] ?? 0) > 0).map((entry) => (
          <button
            key={entry.status}
            type="button"
            role="tab"
            aria-selected={filter === entry.status}
            className={`btn btn--sm${filter === entry.status ? ' btn--primary' : ''}`}
            onClick={() => setFilter(entry.status)}
          >
            {t(entry.label)} · {counts[entry.status]}
          </button>
        ))}
      </div>
      <div className="tablewrap">
        <table className="table">
          <thead>
            <tr>
              <th className="num">{t('impw.rowNumber')}</th>
              <th>{t('impw.problem')}</th>
              <th>{t('impw.values')}</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={3} className="card__hint">
                  {t('common.loading')}
                </td>
              </tr>
            ) : error ? (
              <tr>
                <td colSpan={3} className="formerror">
                  {error}
                </td>
              </tr>
            ) : (
              (rows ?? []).map((row) => {
                const issues = row.errors.length > 0 ? row.errors : row.warnings;
                return (
                  <tr key={row.row_number}>
                    <td className="num">{row.row_number}</td>
                    <td>
                      {issues.length === 0
                        ? '—'
                        : issues.map((issue, index) => {
                            const label = fieldLabel(batch.template, issue.field);
                            return (
                              <div key={index}>
                                <strong>{label ? t(label) : issue.field}</strong>: {issue.message}
                              </div>
                            );
                          })}
                    </td>
                    <td className="table__sub">
                      {mappedHeaders
                        .map((header) => row.raw[header])
                        .filter((value) => value !== null && value !== undefined && value !== '')
                        .map(displayCell)
                        .join(' · ') || '—'}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {(counts[filter] ?? 0) > ISSUE_ROWS ? (
        <p className="card__hint">{t('impw.moreInDownload', { shown: ISSUE_ROWS })}</p>
      ) : null}
    </section>
  );
}

function ErrorDownload({ batch }: { batch: ImportBatch }) {
  const { t } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const base = batch.original_filename.replace(/\.[^.]+$/, '');
      await download(`/imports/${batch.id}/errors.csv`, `${base}-errors.csv`);
    } catch (caught) {
      setError(describeError(caught, t));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {error ? <span className="formerror">{error}</span> : null}
      <button type="button" className="btn" disabled={busy} onClick={run}>
        {t('imp.downloadErrors')}
      </button>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* 4. Import                                                                   */
/* -------------------------------------------------------------------------- */

function RunStep({
  batch,
  onBatch,
  onFinished,
  onBackToReview,
  onLeave,
}: {
  batch: ImportBatch;
  onBatch: (batch: ImportBatch) => void;
  onFinished: (batch: ImportBatch) => void;
  onBackToReview: (batch: ImportBatch) => void;
  onLeave: () => void;
}) {
  const { t } = useSession();
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [started] = useState(() => Date.now());
  const [elapsed, setElapsed] = useState(0);
  const id = batch.id;
  const status = batch.status;

  const settle = useCallback(
    (latest: ImportBatch): boolean => {
      onBatch(latest);
      if (latest.status === 'COMMITTED' || latest.status === 'FAILED' || latest.status === 'CANCELLED') {
        onFinished(latest);
        return true;
      }
      return false;
    },
    [onBatch, onFinished],
  );

  // Start, or pick up, the commit. Always reads first: whatever a previous
  // attempt did on the server decides what happens now, never this tab's guess.
  useEffect(() => {
    let cancelled = false;

    async function begin() {
      try {
        const latest = await api.get<ImportBatch>(`/imports/${id}`);
        if (cancelled || settle(latest)) {
          return;
        }
        if (latest.status === 'COMMITTING') {
          return; // Already running on the server; the poll below watches it.
        }
        if (!latest.can_commit) {
          onBackToReview(latest);
          return;
        }
        const result = await api.post<ImportCommitResult>(`/imports/${id}/commit`);
        if (!cancelled) {
          settle(result.import_batch);
        }
      } catch (caught) {
        if (cancelled) {
          return;
        }
        if (caught instanceof ApiError && caught.status === 409) {
          // Committed or claimed by an earlier attempt. Read what it did.
          try {
            const latest = await api.get<ImportBatch>(`/imports/${id}`);
            if (!cancelled && !settle(latest) && latest.status !== 'COMMITTING') {
              onBackToReview(latest);
            }
            return;
          } catch (again) {
            caught = again;
          }
        }
        if (!cancelled) {
          setError(describeError(caught, t));
        }
      }
    }

    void begin();
    return () => {
      cancelled = true;
    };
    // `attempt` re-runs this on "Try again"; the rest are stable for one run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, attempt]);

  // Large imports run in the worker. Poll until the batch leaves COMMITTING.
  useEffect(() => {
    if (status !== 'COMMITTING' || error) {
      return;
    }
    let cancelled = false;
    const timer = setInterval(() => {
      api
        .get<ImportBatch>(`/imports/${id}`)
        .then((latest) => {
          if (!cancelled) {
            settle(latest);
          }
        })
        .catch(() => {
          // A missed poll is not a failed import; the next tick tries again.
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [id, status, error, settle]);

  useEffect(() => {
    const timer = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [started]);

  const pending = batch.ready_count + batch.warning_count;

  return (
    <div className="wizard__body">
      {error ? (
        <>
          <p className="formerror" role="alert">
            {error}
          </p>
          <p className="card__hint">{t('impw.retrySafe')}</p>
          <div className="wizard__actions">
            <span />
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => {
                setError(null);
                setAttempt((value) => value + 1);
              }}
            >
              {t('common.retry')}
            </button>
          </div>
        </>
      ) : batch.status === 'COMMITTING' && batch.failure_reason ? (
        // The worker stopped part-way. The server's note is not repeated
        // verbatim; the seller gets the part that matters to them.
        <>
          <p className="formerror" role="alert">
            {t('impw.stalled')}
          </p>
          <div className="wizard__actions">
            <span />
            <button type="button" className="btn" onClick={onLeave}>
              {t('impw.backToHistory')}
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="progress" role="progressbar" aria-busy="true" aria-label={t('impw.importing')}>
            <div className="progress__bar" />
          </div>
          <p>
            <strong>{t('impw.importingCount', { count: pending })}</strong>{' '}
            <span className="card__hint">{t('impw.elapsed', { seconds: elapsed })}</span>
          </p>
          {status === 'COMMITTING' ? (
            <>
              <p className="card__hint">{t('impw.backgroundHint')}</p>
              <div className="wizard__actions">
                <span />
                <button type="button" className="btn" onClick={onLeave}>
                  {t('impw.leaveRunning')}
                </button>
              </div>
            </>
          ) : (
            <p className="card__hint">{t('impw.keepOpen')}</p>
          )}
        </>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* 5. Summary                                                                  */
/* -------------------------------------------------------------------------- */

function DoneStep({
  batch,
  locale,
  onAnother,
  onClose,
}: {
  batch: ImportBatch;
  locale: ReturnType<typeof useSession>['locale'];
  onAnother: () => void;
  onClose: () => void;
}) {
  const { t } = useSession();
  const failing = batch.invalid_count + batch.duplicate_count;
  const committed = batch.status === 'COMMITTED';

  return (
    <div className="wizard__body">
      <p>
        <Chip
          label={t(committed ? 'impw.finished' : 'impw.notFinished')}
          tone={committed ? 'good' : 'bad'}
        />{' '}
        {batch.committed_at ? (
          <span className="card__hint">{formatDate(batch.committed_at, { locale })}</span>
        ) : null}
      </p>
      {committed ? null : <p className="card__hint">{t('impw.notFinishedHint')}</p>}

      <div className="tiles">
        <Tile label={t('imp.created')} value={String(batch.created_count)} />
        <Tile label={t('impw.duplicate')} value={String(batch.duplicate_count)} hint={t('impw.skippedHint')} />
        <Tile label={t('impw.invalid')} value={String(batch.invalid_count)} hint={t('impw.skippedHint')} />
      </div>

      {failing > 0 ? <p className="card__hint">{t('impw.fixAndReupload')}</p> : null}

      <div className="wizard__actions">
        <button type="button" className="btn" onClick={onClose}>
          {t('impw.backToHistory')}
        </button>
        <span className="wizard__actionsright">
          {failing > 0 ? <ErrorDownload batch={batch} /> : null}
          <button type="button" className="btn btn--primary" onClick={onAnother}>
            {t('impw.another')}
          </button>
        </span>
      </div>
    </div>
  );
}
