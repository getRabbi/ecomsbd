/**
 * The parts of `GET /v1/intake/rules` the extension uses (schema version 1).
 *
 * Selectors are published per host only with `verified_at` (the server refuses
 * unverified ones), and they are used only when the whole set needed to read
 * one conversation is there: the conversation, its messages and the sender's
 * name. Anything less and the extension captures from the text selection.
 */

import type { PhoneRules } from './phones.ts';

export const HOSTS = ['business.facebook.com', 'www.messenger.com', 'web.whatsapp.com'] as const;
export type Host = (typeof HOSTS)[number];

export interface Selectors {
  conversation: string;
  message: string;
  /** Inside a message; omitted = the message's own text. */
  message_text: string | null;
  sender: string;
}

export interface RulesBody {
  version: number;
  schema_version: number;
  rules: {
    phone: PhoneRules;
    extension: {
      hosts: Partial<Record<Host, { selectors: Record<string, string | null>; verified_at: string | null }>>;
    };
  };
  refresh_after_seconds: number;
}

export interface StoredRules {
  body: RulesBody;
  etag: string | null;
  fetched_at: number;
}

export function isHost(value: string): value is Host {
  return (HOSTS as readonly string[]).includes(value);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** The body, if it is a schema-1 rules response this build understands. */
export function parseRules(value: unknown): RulesBody | null {
  if (!isObject(value) || value.schema_version !== 1 || typeof value.version !== 'number') return null;
  const rules = value.rules;
  if (!isObject(rules) || !isObject(rules.phone) || !isObject(rules.extension)) return null;
  const phone = rules.phone;
  if (
    !Array.isArray(phone.patterns) ||
    !phone.patterns.every((p) => typeof p === 'string') ||
    typeof phone.national_pattern !== 'string' ||
    typeof phone.country_prefix !== 'string' ||
    !isObject(rules.extension.hosts)
  ) {
    return null;
  }
  try {
    for (const pattern of [...(phone.patterns as string[]), phone.national_pattern]) new RegExp(pattern);
  } catch {
    return null;
  }
  const refresh = typeof value.refresh_after_seconds === 'number' ? value.refresh_after_seconds : 86_400;
  return { ...(value as unknown as RulesBody), refresh_after_seconds: Math.max(3_600, refresh) };
}

function selector(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null;
}

/** The verified selectors for a host, or null (then: capture the selection). */
export function hostSelectors(body: RulesBody | null | undefined, host: string): Selectors | null {
  if (!body || !isHost(host)) return null;
  const entry = body.rules.extension.hosts[host];
  if (!entry || !entry.verified_at) return null;
  const conversation = selector(entry.selectors.conversation);
  const message = selector(entry.selectors.message);
  const sender = selector(entry.selectors.sender);
  if (!conversation || !message || !sender) return null;
  return { conversation, message, message_text: selector(entry.selectors.message_text), sender };
}

/** Whether the stored rules should be checked again (`If-None-Match`). */
export function refreshDue(stored: StoredRules | null | undefined, now: number = Date.now()): boolean {
  if (!stored) return true;
  return now - stored.fetched_at >= stored.body.refresh_after_seconds * 1000;
}
