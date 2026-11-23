// The selector reader on a small fake DOM: only the open conversation is read.
import assert from 'node:assert/strict';
import test from 'node:test';

import { MAX_MESSAGES, MAX_TEXT, readConversation, readSelection } from '../src/shared/conversation.ts';

/** A node that answers `querySelector(All)` from a selector → children map. */
function node(text, children = {}) {
  return {
    textContent: text,
    querySelector(selector) {
      if (selector === 'BAD[') throw new SyntaxError('invalid selector');
      return (children[selector] ?? [])[0] ?? null;
    },
    querySelectorAll(selector) {
      if (selector === 'BAD[') throw new SyntaxError('invalid selector');
      return children[selector] ?? [];
    },
  };
}

const SELECTORS = { conversation: '#open-chat', message: '.msg', message_text: '.body', sender: 'header .name' };

function page(lines, { sender = 'Rahim', otherChat = ['01700000099 secret'] } = {}) {
  const messages = lines.map((line) => node(`${line} 10:42`, { '.body': [node(line)] }));
  const open = node(lines.join(' '), { '.msg': messages });
  const other = node(otherChat.join(' '), { '.msg': otherChat.map((line) => node(line, { '.body': [node(line)] })) });
  return node('whole page', {
    '#open-chat': [open],
    'header .name': sender === null ? [] : [node(`  ${sender}  `)],
    '.chat-list': [other],
  });
}

test('the open conversation: sender, then its messages in order', () => {
  const read = readConversation(page(['2ta nibo', '01700000021', 'Mirpur 10, Dhaka']), SELECTORS);
  assert.deepEqual(read, {
    ok: true,
    conversation_key: 'Rahim',
    sender_display: 'Rahim',
    messages: [{ text: '2ta nibo' }, { text: '01700000021' }, { text: 'Mirpur 10, Dhaka' }],
  });
  assert.ok(!JSON.stringify(read).includes('secret'), 'another chat is never read');
});

test('without message_text the message element itself is read', () => {
  const read = readConversation(page(['XL']), { ...SELECTORS, message_text: null });
  assert.deepEqual(read.messages, [{ text: 'XL 10:42' }]);
});

test('only the latest messages, each within the server limit', () => {
  const many = Array.from({ length: MAX_MESSAGES + 5 }, (_, i) => `line ${i}`);
  const read = readConversation(page(many), SELECTORS);
  assert.equal(read.messages.length, MAX_MESSAGES);
  assert.equal(read.messages[0].text, 'line 5');
  const long = readConversation(page(['x'.repeat(MAX_TEXT + 10)]), SELECTORS);
  assert.equal(long.messages[0].text.length, MAX_TEXT);
});

test('a selector that matches nothing, throws or reads no text is a failure', () => {
  const failed = { ok: false, reason: 'SELECTOR_FAILED' };
  assert.deepEqual(readConversation(page(['a']), { ...SELECTORS, conversation: '#gone' }), failed);
  assert.deepEqual(readConversation(page(['a']), { ...SELECTORS, conversation: 'BAD[' }), failed);
  assert.deepEqual(readConversation(page(['a']), { ...SELECTORS, message: 'BAD[' }), failed);
  assert.deepEqual(readConversation(page(['a'], { sender: null }), SELECTORS), failed);
  assert.deepEqual(readConversation(page(['   ']), SELECTORS), failed);
});

test('a selection is one message; empty or too long is refused', () => {
  assert.deepEqual(readSelection('  01700000021\n\n\n\nMirpur  '), { ok: true, messages: [{ text: '01700000021\n\nMirpur' }] });
  assert.deepEqual(readSelection(''), { ok: false, reason: 'EMPTY' });
  assert.deepEqual(readSelection(null), { ok: false, reason: 'EMPTY' });
  assert.deepEqual(readSelection('x'.repeat(MAX_TEXT + 1)), { ok: false, reason: 'TOO_LONG' });
});
