/**
 * The extension's state in `chrome.storage.local` and the calls around it,
 * shared by the service worker and the side panel.
 *
 * Stored: the install id, the pairing (connection id, ingest key, last 4),
 * the rules with their ETag, the last heartbeat and the panel language. No
 * captured text is stored.
 */

import {
  claimPairing,
  ExtensionApiError,
  fetchRules,
  sendHeartbeat,
  unlink,
  type ApiConfig,
  type PairedKey,
} from './api.ts';
import { API_BASE_URL } from './config.ts';
import { parseRules, refreshDue, type StoredRules } from './rules.ts';
import type { Locale } from './strings.ts';

export interface Pairing extends PairedKey {
  paired_at: number;
}

interface State {
  install_id?: string;
  pairing?: Pairing;
  rules?: StoredRules;
  last_heartbeat_at?: number;
  locale?: Locale;
}

/** Heartbeats keep the hub's health green (stale after 24 h, app/intake/jobs.py). */
export const HEARTBEAT_EVERY_MS = 6 * 60 * 60 * 1000;

export const api: ApiConfig = { baseUrl: API_BASE_URL };

export function appVersion(): string {
  return chrome.runtime.getManifest().version;
}

async function read(): Promise<State> {
  return (await chrome.storage.local.get(null)) as State;
}

export async function getPairing(): Promise<Pairing | null> {
  return (await read()).pairing ?? null;
}

export async function getRules(): Promise<StoredRules | null> {
  return (await read()).rules ?? null;
}

export async function getLocale(): Promise<Locale> {
  const stored = (await read()).locale;
  if (stored === 'en' || stored === 'bn') return stored;
  return navigator.language.toLowerCase().startsWith('bn') ? 'bn' : 'en';
}

export async function setLocale(locale: Locale): Promise<void> {
  await chrome.storage.local.set({ locale });
}

async function installId(): Promise<string> {
  const state = await read();
  if (state.install_id) return state.install_id;
  const id = `ext-${crypto.randomUUID()}`;
  await chrome.storage.local.set({ install_id: id });
  return id;
}

/** Forget the link here (the key no longer works, or the seller unpaired). */
export async function forget(): Promise<void> {
  await chrome.storage.local.remove(['pairing', 'rules', 'last_heartbeat_at']);
}

async function guarded<T>(work: (pairing: Pairing) => Promise<T>): Promise<T | null> {
  const pairing = await getPairing();
  if (!pairing) return null;
  try {
    return await work(pairing);
  } catch (error) {
    if (error instanceof ExtensionApiError && error.status === 401) await forget();
    throw error;
  }
}

export async function pair(code: string): Promise<Pairing> {
  const claimed = await claimPairing(api, { code, installId: await installId(), appVersion: appVersion() });
  const pairing: Pairing = { ...claimed, paired_at: Date.now() };
  await chrome.storage.local.set({ pairing });
  await refreshRules(true).catch(() => undefined);
  await heartbeat(true).catch(() => undefined);
  return pairing;
}

export async function unpair(): Promise<void> {
  const pairing = await getPairing();
  if (pairing) await unlink(api, pairing.ingest_key);
  await forget();
}

export async function refreshRules(force = false): Promise<StoredRules | null> {
  return guarded(async (pairing) => {
    const stored = await getRules();
    if (!force && !refreshDue(stored)) return stored;
    const fetched = await fetchRules(api, pairing.ingest_key, stored?.etag ?? null);
    if (!fetched.changed && stored) {
      const touched = { ...stored, fetched_at: Date.now() };
      await chrome.storage.local.set({ rules: touched });
      return touched;
    }
    const body = fetched.changed ? parseRules(fetched.body) : null;
    if (!body || !fetched.changed) return stored; // keep the last good rules
    const rules: StoredRules = { body, etag: fetched.etag, fetched_at: Date.now() };
    await chrome.storage.local.set({ rules });
    return rules;
  });
}

export async function heartbeat(force = false): Promise<void> {
  await guarded(async (pairing) => {
    const last = (await read()).last_heartbeat_at ?? 0;
    if (!force && Date.now() - last < HEARTBEAT_EVERY_MS) return;
    await sendHeartbeat(api, pairing.ingest_key, appVersion());
    await chrome.storage.local.set({ last_heartbeat_at: Date.now() });
  });
}
