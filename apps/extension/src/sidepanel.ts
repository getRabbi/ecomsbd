/**
 * The side panel: link (6-digit code), Capture, what was found, Open in
 * ecomsbd, Unlink. The same fields as the phone's quick-order sheet are listed;
 * their values are checked and the order is made on the web page
 * `/quick-order?draft=<id>`, signed in — the ingest key only captures.
 */

import { failureKind, sendCapture, type CaptureAnswer, type CaptureBody, type FailureKind } from './shared/api.ts';
import { WEB_BASE_URL } from './shared/config.ts';
import { READ, type ReadResponse } from './shared/messages.ts';
import { compilePhoneRules, findPhones, maskPhone } from './shared/phones.ts';
import { api, getLocale, getPairing, getRules, pair, refreshRules, setLocale, unpair } from './shared/session.ts';
import { translate, type Locale, type StringKey } from './shared/strings.ts';

let locale: Locale = 'en';
/** The last capture that did not reach the server: retried with the same key. */
let retry: { key: string; body: CaptureBody } | null = null;

const $ = <T extends HTMLElement>(id: string): T => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`missing #${id}`);
  return element as T;
};
const t = (key: StringKey, vars?: Record<string, string | number>) => translate(locale, key, vars);

function el(tag: string, text?: string, className?: string): HTMLElement {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function notice(key: StringKey | null, vars?: Record<string, string | number>, tone: 'info' | 'warn' = 'info'): void {
  const box = $('notice');
  box.textContent = key ? t(key, vars) : '';
  box.className = key ? `notice notice--${tone}` : 'notice';
}

const FAILURE_COPY: Record<FailureKind, StringKey> = {
  UNLINKED: 'unlinked',
  DISABLED: 'disabled',
  INVALID_CODE: 'failed',
  RATE_LIMITED: 'rateLimited',
  OFFLINE: 'offline',
  FAILED: 'failed',
};

function translateStatic(): void {
  document.documentElement.lang = locale;
  for (const node of Array.from(document.querySelectorAll<HTMLElement>('[data-t]'))) {
    node.textContent = t(node.dataset.t as StringKey);
  }
}

async function render(): Promise<void> {
  locale = await getLocale();
  translateStatic();
  const pairing = await getPairing();
  $('pair-view').hidden = pairing !== null;
  $('capture-view').hidden = pairing === null;
  if (pairing) {
    $('linked').textContent = t('linked', { last4: pairing.key_last4 });
    void refreshRules().catch(() => undefined);
  }
}

async function onPair(event: Event): Promise<void> {
  event.preventDefault();
  const input = $<HTMLInputElement>('code');
  const code = input.value.replace(/[^0-9]/g, '');
  const button = $<HTMLButtonElement>('pair');
  if (code.length !== 6) {
    notice('invalidCode', undefined, 'warn');
    return;
  }
  button.disabled = true;
  button.textContent = t('pairing');
  try {
    await pair(code);
    input.value = '';
    notice(null);
  } catch (error) {
    const kind = failureKind(error);
    notice(kind === 'INVALID_CODE' ? 'invalidCode' : FAILURE_COPY[kind], undefined, 'warn');
  } finally {
    button.disabled = false;
    button.textContent = t('pair');
    await render();
  }
}

async function readActiveTab(): Promise<ReadResponse | null> {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (tab?.id === undefined) return null;
  try {
    return ((await chrome.tabs.sendMessage(tab.id, { type: READ })) as ReadResponse | null) ?? null;
  } catch {
    return null; // not one of the three hosts (no content script there)
  }
}

function bodyFrom(read: Extract<ReadResponse, { kind: 'CONVERSATION' | 'SELECTION' }>): CaptureBody {
  return read.kind === 'CONVERSATION'
    ? {
        host: read.host,
        origin: 'CONVERSATION',
        conversation_key: read.conversation_key,
        sender_display: read.sender_display,
        messages: read.messages,
      }
    : { host: read.host, origin: 'SELECTION', messages: read.messages };
}

function showResult(answer: CaptureAnswer, phones: string[]): void {
  const box = $('result');
  box.replaceChildren();
  box.hidden = false;
  box.appendChild(el('h2', t('fields')));
  const list = el('dl', undefined, 'fields');
  const missing = new Set(answer.missing_fields);
  const rows: [StringKey, string, boolean][] = [
    ['phone', phones.length > 0 ? phones.map(maskPhone).join(', ') : t('missing'), !missing.has('phone')],
    ['address', missing.has('address') ? t('missing') : t('found'), !missing.has('address')],
    ['items', missing.has('items') ? t('missing') : t('found'), !missing.has('items')],
    ['nameCod', t('checkInApp'), true],
  ];
  for (const [label, value, ok] of rows) {
    list.appendChild(el('dt', t(label)));
    list.appendChild(el('dd', value, ok ? 'ok' : 'missing'));
  }
  box.appendChild(list);
  if (answer.open_path) {
    const open = el('button', t('open'), 'primary') as HTMLButtonElement;
    const url = new URL(answer.open_path, WEB_BASE_URL);
    open.addEventListener('click', () => void chrome.tabs.create({ url: url.toString() }));
    box.appendChild(open);
  }
  notice(answer.accepted > 0 ? 'captured' : 'nothingNew', { count: answer.accepted });
}

async function onCapture(again: boolean): Promise<void> {
  const button = $<HTMLButtonElement>('capture');
  $('result').hidden = true;
  $('retry').hidden = true;
  let attempt = again ? retry : null;
  if (!attempt) {
    const read = await readActiveTab();
    if (!read) {
      notice('notAChat', undefined, 'warn');
      return;
    }
    if (read.kind === 'NOTHING') {
      // No selection and no readable chat: the spec's notice (selectors
      // missing or no longer matching the page), or "select less".
      notice(read.reason === 'TOO_LONG' ? 'tooLong' : read.supported ? 'selectText' : 'unsupported', undefined, 'warn');
      return;
    }
    attempt = { key: `ext-${crypto.randomUUID()}`, body: bodyFrom(read) };
  }
  const pairing = await getPairing();
  if (!pairing) return render();
  const rules = await getRules();
  const phones = rules
    ? findPhones(attempt.body.messages.map((line) => line.text).join('\n'), compilePhoneRules(rules.body.rules.phone))
    : [];
  button.disabled = true;
  button.textContent = t('capturing');
  try {
    const answer = await sendCapture(api, pairing.ingest_key, attempt.key, attempt.body);
    retry = null;
    showResult(answer, phones);
  } catch (error) {
    const kind = failureKind(error);
    // Only an unanswered request is retried with the same Idempotency-Key.
    retry = kind === 'OFFLINE' ? attempt : null;
    $('retry').hidden = retry === null;
    notice(FAILURE_COPY[kind], undefined, 'warn');
    if (kind === 'UNLINKED') await render();
  } finally {
    button.disabled = false;
    button.textContent = t('capture');
  }
}

async function onUnpair(): Promise<void> {
  try {
    await unpair();
    notice(null);
  } catch (error) {
    notice(FAILURE_COPY[failureKind(error)], undefined, 'warn');
  }
  retry = null;
  $('result').hidden = true;
  await render();
}

async function onLanguage(): Promise<void> {
  await setLocale(locale === 'bn' ? 'en' : 'bn');
  notice(null);
  await render();
}

$('pair-form').addEventListener('submit', (event) => void onPair(event));
$('capture').addEventListener('click', () => void onCapture(false));
$('retry').addEventListener('click', () => void onCapture(true));
$('unpair').addEventListener('click', () => void onUnpair());
$('language').addEventListener('click', () => void onLanguage());
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && ('pairing' in changes || 'locale' in changes)) void render();
});
void render();
