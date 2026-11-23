/**
 * The extension's calls to the ecomsbd API.
 *
 * The ingest key (scope `intake.capture`) is sent on intake routes only:
 * rules, heartbeat, capture and unlink. Everything that needs the seller —
 * reading the draft, the customer, making the order — happens on the web
 * quick-order page under the seller's own session (spec §7).
 */

import type { Host } from './rules.ts';

export interface ApiConfig {
  baseUrl: string;
  fetch?: typeof fetch;
}

export interface PairedKey {
  connection_id: string;
  ingest_key: string;
  key_last4: string;
}

export interface CaptureBody {
  host: Host;
  origin: 'CONVERSATION' | 'SELECTION';
  conversation_key?: string;
  sender_display?: string;
  messages: { text: string }[];
}

export interface CaptureAnswer {
  draft_id: string | null;
  status: string | null;
  missing_fields: string[];
  accepted: number;
  duplicates: number;
  open_path: string | null;
  replayed: boolean;
}

export type RulesFetch = { changed: false } | { changed: true; etag: string | null; body: unknown };

/** What the panel says about a failure. */
export type FailureKind = 'UNLINKED' | 'DISABLED' | 'INVALID_CODE' | 'RATE_LIMITED' | 'OFFLINE' | 'FAILED';

export class ExtensionApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly reason: string | null;
  readonly retryable: boolean;

  constructor(status: number, code: string, reason: string | null, retryable: boolean) {
    super(`${status} ${code}`);
    this.name = 'ExtensionApiError';
    this.status = status;
    this.code = code;
    this.reason = reason;
    this.retryable = retryable;
  }
}

/** Status 0: the request never reached the server. */
const offline = () => new ExtensionApiError(0, 'NETWORK', null, true);

async function call(
  config: ApiConfig,
  method: 'GET' | 'POST',
  path: string,
  options: { key?: string; body?: unknown; headers?: Record<string, string> } = {},
): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers };
  if (options.key) headers.Authorization = `Bearer ${options.key}`;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  let response: Response;
  try {
    response = await (config.fetch ?? fetch)(`${config.baseUrl.replace(/\/+$/, '')}/v1${path}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: 'omit',
      cache: 'no-store',
    });
  } catch {
    throw offline();
  }
  if (response.ok || response.status === 304) return response;
  let code = 'HTTP_ERROR';
  let reason: string | null = null;
  let retryable = response.status >= 500 || response.status === 429;
  try {
    const body = (await response.json()) as { code?: unknown; retryable?: unknown; details?: { reason?: unknown } };
    if (typeof body.code === 'string') code = body.code;
    if (typeof body.retryable === 'boolean') retryable = body.retryable;
    if (typeof body.details?.reason === 'string') reason = body.details.reason;
  } catch {
    // not JSON: keep the status
  }
  throw new ExtensionApiError(response.status, code, reason, retryable);
}

export async function claimPairing(
  config: ApiConfig,
  input: { code: string; installId: string; appVersion: string },
): Promise<PairedKey> {
  const response = await call(config, 'POST', '/devices/pair/claim', {
    body: {
      code: input.code,
      install_id: input.installId,
      platform: 'extension',
      app_version: input.appVersion,
      covered_packages: [],
    },
  });
  return (await response.json()) as PairedKey;
}

export async function fetchRules(config: ApiConfig, key: string, etag: string | null): Promise<RulesFetch> {
  const response = await call(config, 'GET', '/intake/rules', {
    key,
    headers: etag ? { 'If-None-Match': etag } : {},
  });
  if (response.status === 304) return { changed: false };
  return { changed: true, etag: response.headers.get('ETag'), body: await response.json() };
}

export async function sendHeartbeat(config: ApiConfig, key: string, appVersion: string): Promise<void> {
  await call(config, 'POST', '/intake/heartbeat', {
    key,
    body: { app_version: appVersion, listener_state: 'ON' },
  });
}

export async function sendCapture(
  config: ApiConfig,
  key: string,
  idempotencyKey: string,
  body: CaptureBody,
): Promise<CaptureAnswer> {
  const response = await call(config, 'POST', '/intake/captures/extension', {
    key,
    body,
    headers: { 'Idempotency-Key': idempotencyKey },
  });
  return (await response.json()) as CaptureAnswer;
}

/** Unpair: the server disconnects this link and revokes its key. */
export async function unlink(config: ApiConfig, key: string): Promise<void> {
  try {
    await call(config, 'POST', '/intake/unlink', { key });
  } catch (error) {
    // Already unlinked (or revoked in the hub): nothing is left to undo.
    if (!(error instanceof ExtensionApiError && error.status === 401)) throw error;
  }
}

export function failureKind(error: unknown): FailureKind {
  if (!(error instanceof ExtensionApiError)) return 'FAILED';
  if (error.status === 0) return 'OFFLINE';
  if (error.status === 401) return 'UNLINKED';
  if (error.code === 'FEATURE_DISABLED') return 'DISABLED';
  if (error.status === 429) return 'RATE_LIMITED';
  if (error.status === 404 || error.status === 422) return 'INVALID_CODE';
  return 'FAILED';
}
