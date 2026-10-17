import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

import {
  codeFailure,
  isCodeComplete,
  MAX_CODE_LENGTH,
  normalizeCode,
} from '../lib/email-code.ts';

/**
 * Sign-up is confirmed with a code typed from the email, not a link.
 */

test('a code is digits only; Bangla digits, spaces and dashes are accepted', () => {
  assert.equal(normalizeCode('123456'), '123456');
  assert.equal(normalizeCode('১২৩৪৫৬'), '123456');
  assert.equal(normalizeCode(' 123-456 '), '123456');
  assert.equal(normalizeCode('১২৩ ৪৫৬'), '123456');
  assert.equal(normalizeCode('abc'), '');
  assert.equal(normalizeCode('1'.repeat(20)).length, MAX_CODE_LENGTH);
});

test('only a plausible length is sent for checking', () => {
  assert.equal(isCodeComplete('12345'), false);
  assert.equal(isCodeComplete('123456'), true);
  assert.equal(isCodeComplete('১২৩৪৫৬৭৮'), true);
  assert.equal(isCodeComplete(''), false);
});

test('a wrong and an expired code read the same; throttling reads differently', () => {
  assert.equal(codeFailure({ code: 'otp_expired', status: 403 }), 'invalid');
  assert.equal(codeFailure({ status: 403 }), 'invalid');
  assert.equal(codeFailure({ code: 'over_request_rate_limit', status: 429 }), 'rateLimited');
  assert.equal(codeFailure({ status: 500 }), 'other');
  assert.equal(codeFailure(null), 'other');
});

test('the sign-in page checks the code with Supabase and keeps it out of URLs and storage', async () => {
  const page = await readFile(new URL('../app/sign-in/page.tsx', import.meta.url), 'utf8');
  assert.match(page, /auth\.verifyOtp\(\{\s*email: pendingEmail,\s*token,\s*type: 'email',?\s*\}\)/);
  assert.match(page, /autoComplete="one-time-code"/);
  // The code lives in component state only.
  assert.doesNotMatch(page, /(localStorage|sessionStorage)\.setItem\([^)]*code/i);
  assert.doesNotMatch(page, /searchParams\.set\([^)]*code/i);
  assert.doesNotMatch(page, /console\.(log|info|debug)\(/);
});
