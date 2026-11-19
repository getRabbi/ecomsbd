import assert from 'node:assert/strict';
import test from 'node:test';

import { durationLabel, introOffer, voiceIntroBn, voiceIntroEn } from '../lib/voice-intro.ts';

const VERSION = {
  version: 2,
  state: 'READY',
  source: 'UPLOAD',
  duration_ms: 11_600,
  previewed: false,
  created_at: '2026-11-20T06:00:00Z',
  approved_at: null,
};

function status(overrides = {}) {
  return {
    shop_name: 'Rupa Fashion',
    script: 'আসসালামু আলাইকুম। আপনি Rupa Fashion থেকে একটি অর্ডার করেছেন।',
    status: 'NONE',
    latest: null,
    live: null,
    tts_available: false,
    calls_on: false,
    calls_off_reason: 'NO_INTRO_AUDIO',
    ...overrides,
  };
}

test('every call-voice string has Bangla', () => {
  assert.deepEqual(Object.keys(voiceIntroBn).sort(), Object.keys(voiceIntroEn).sort());
  for (const [key, value] of Object.entries(voiceIntroBn)) {
    assert.ok(value.trim().length > 0, key);
  }
  assert.match(voiceIntroBn['vi.version'], /\{version\}/);
});

test('with nothing recorded the team records it unless TTS exists', () => {
  const waiting = introOffer(status());
  assert.equal(waiting.waitingForTeam, true);
  assert.equal(waiting.canRequest, false);
  assert.equal(waiting.candidate, null);
  const tts = introOffer(status({ tts_available: true }));
  assert.equal(tts.canRequest, true);
  assert.equal(tts.waitingForTeam, false);
});

test('a recording can be turned on only after it was played', () => {
  const ready = status({ status: 'READY', latest: VERSION });
  assert.equal(introOffer(ready).canTurnOn, false);
  assert.equal(introOffer(ready, new Set([2])).canTurnOn, true);
  assert.equal(introOffer(ready, new Set([1])).canTurnOn, false);
  const heardBefore = status({ status: 'READY', latest: { ...VERSION, previewed: true } });
  assert.equal(introOffer(heardBefore).canTurnOn, true);
});

test('the one on is not offered again; a newer one is', () => {
  const live = { ...VERSION, version: 1, state: 'ON', previewed: true };
  const on = introOffer(status({ status: 'ON', latest: live, live, calls_on: true, calls_off_reason: null }));
  assert.equal(on.candidate, null);
  assert.equal(on.onButIdle, false);
  const newer = introOffer(status({ status: 'ON', latest: VERSION, live }));
  assert.equal(newer.candidate?.version, 2);
  assert.equal(newer.onButIdle, true);
});

test('durations are whole seconds', () => {
  assert.equal(durationLabel(11_600), '12s');
  assert.equal(durationLabel(300), '1s');
  assert.equal(durationLabel(null), null);
  assert.equal(durationLabel(0), null);
});
