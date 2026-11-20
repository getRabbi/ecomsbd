// The phone extractor against the real v1 rules (the same body the Android
// tests use, generated from the backend seed): same inputs, same numbers as
// IntakeClassifierTest.kt.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { compilePhoneRules, findPhones, maskPhone, normalize, phoneSpans, toNational } from '../src/shared/phones.ts';

const body = JSON.parse(readFileSync(new URL('./fixtures/rules_v1.json', import.meta.url), 'utf8'));
const rules = compilePhoneRules(body.rules.phone);
const bangla = (digits) => [...digits].map((d) => String.fromCharCode(0x09e6 + Number(d))).join('');

test('a Banglish order message has its number', () => {
  assert.deepEqual(findPhones('Assalamualaikum, 2ta nibo. 01700000001, Mirpur, Dhaka', rules), ['01700000001']);
  assert.equal(maskPhone('01700000001'), '01700•••01');
});

test('no number, no phone', () => {
  assert.deepEqual(findPhones('order korbo, home delivery, address: Dhaka', rules), []);
});

test('Bangla digits and the country prefix become a national number', () => {
  const text = `অর্ডার করতে চাই। +${bangla('8801700000003')} ঠিকানা: মিরপুর, ঢাকা`;
  assert.deepEqual(findPhones(text, rules), ['01700000003']);
});

test('separators inside a number are allowed', () => {
  assert.deepEqual(findPhones('call 0170-0000-005 please', rules), ['01700000005']);
  assert.deepEqual(findPhones('88 01700 000005', rules), ['01700000005']);
  assert.deepEqual(findPhones(`01700${String.fromCharCode(0xa0)}000005`, rules), ['01700000005']);
});

test('a number inside a longer digit run is not a phone', () => {
  assert.deepEqual(findPhones('Order ID 9901700000004123, delivery Dhaka', rules), []);
});

test('distinct numbers in order of appearance', () => {
  assert.deepEqual(findPhones('01700000011 na hole 01700000012, abar 01700000011', rules), ['01700000011', '01700000012']);
});

test('only Bangladeshi mobile prefixes count', () => {
  assert.deepEqual(findPhones('01200000000 or 01100000000', rules), []);
  assert.equal(toNational('01300000000', rules), '01300000000');
  assert.equal(toNational('8801900000000', rules), '01900000000');
  assert.equal(toNational('0170000000', rules), null, 'ten digits');
});

test('normalising matches the phone: NFC, lower case, one space', () => {
  assert.equal(normalize('  Mirpur\t\n 10 ,  DHAKA  '), 'mirpur 10 , dhaka');
  assert.equal(normalize(`a${String.fromCharCode(0x202f, 0x2007)}b`), 'a b');
  assert.equal(normalize(`e${String.fromCharCode(0x301)}`), String.fromCharCode(0xe9), 'NFC');
});

test('highlight spans point at the number in the page text', () => {
  const text = 'Name: Rahim, phone 0170-0000-021 and +880 1700000022.';
  const spans = phoneSpans(text, rules);
  assert.deepEqual(
    spans.map((span) => [text.slice(span.start, span.end), span.national]),
    [
      ['0170-0000-021', '01700000021'],
      ['+880 1700000022', '01700000022'],
    ],
  );
  assert.deepEqual(phoneSpans('no number here', rules), []);
});

test('a short value is fully masked', () => {
  assert.equal(maskPhone('0170'), '•••');
});
