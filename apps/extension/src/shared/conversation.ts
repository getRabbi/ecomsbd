/**
 * Reading the one open conversation with the rule set's selectors.
 *
 * Only the element the `conversation` selector names is read: never the chat
 * list, never another conversation, never anything in the background — the
 * content script calls this when the seller taps Capture (and to highlight
 * numbers inside the open chat). A selector that matches nothing, throws, or
 * yields no text is a selector failure: the panel then asks for a selection.
 */

import type { Selectors } from './rules.ts';

/** The DOM surface used here (a real `Element`/`Document` satisfies it). */
export interface NodeLike {
  querySelector(selectors: string): NodeLike | null;
  querySelectorAll(selectors: string): ArrayLike<NodeLike>;
  readonly textContent: string | null;
}

export interface CapturedLine {
  text: string;
}

export type ConversationRead =
  | { ok: true; conversation_key: string; sender_display: string; messages: CapturedLine[] }
  | { ok: false; reason: 'SELECTOR_FAILED' };

export type SelectionRead =
  | { ok: true; messages: CapturedLine[] }
  | { ok: false; reason: 'EMPTY' | 'TOO_LONG' };

/** The server's limits (`MAX_BATCH`, `MAX_TEXT` in app/api/v1/intake.py). */
export const MAX_TEXT = 4000;
/** The most recent messages on screen that one capture sends. */
export const MAX_MESSAGES = 20;
/** The longest chat name the server keeps. */
export const MAX_KEY = 200;

const FAILED = { ok: false, reason: 'SELECTOR_FAILED' } as const;

function clean(text: string | null | undefined): string {
  return (text ?? '').replace(/[ \t\r\f\v]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
}

function find(root: NodeLike, selector: string): NodeLike | null {
  try {
    return root.querySelector(selector);
  } catch {
    return null; // an invalid selector from the server is a failure, not a crash
  }
}

function findAll(root: NodeLike, selector: string): NodeLike[] {
  try {
    return Array.from(root.querySelectorAll(selector));
  } catch {
    return [];
  }
}

/** The open conversation, or a selector failure. */
export function readConversation(root: NodeLike, selectors: Selectors): ConversationRead {
  const conversation = find(root, selectors.conversation);
  if (conversation === null) return FAILED;
  const sender = clean(find(root, selectors.sender)?.textContent).slice(0, 120);
  if (!sender) return FAILED;
  const lines: CapturedLine[] = [];
  for (const message of findAll(conversation, selectors.message)) {
    const source = selectors.message_text ? find(message, selectors.message_text) : message;
    const text = clean(source?.textContent);
    if (text) lines.push({ text: text.slice(0, MAX_TEXT) });
  }
  if (lines.length === 0) return FAILED;
  return {
    ok: true,
    conversation_key: sender.slice(0, MAX_KEY),
    sender_display: sender,
    messages: lines.slice(-MAX_MESSAGES),
  };
}

/** The seller's selection as one message. */
export function readSelection(selection: string | null | undefined): SelectionRead {
  const text = clean(selection);
  if (!text) return { ok: false, reason: 'EMPTY' };
  if (text.length > MAX_TEXT) return { ok: false, reason: 'TOO_LONG' };
  return { ok: true, messages: [{ text }] };
}
