/**
 * Content script on the three chat hosts only (manifest `matches`).
 *
 * * Reads nothing until the side panel asks (a Capture tap): then the seller's
 *   selection if there is one, else the open conversation through the rule
 *   set's verified selectors. With no verified selectors the panel shows the
 *   "select the text" notice and captures the selection.
 * * Highlights phone numbers inside the open conversation only, with the CSS
 *   Custom Highlight API (the page's own DOM is not changed). Nothing is sent
 *   for a highlight.
 */

import { readConversation, readSelection } from '../shared/conversation.ts';
import { READ, type ReadRequest, type ReadResponse } from '../shared/messages.ts';
import { compilePhoneRules, phoneSpans, type CompiledPhoneRules } from '../shared/phones.ts';
import { hostSelectors, isHost, type Selectors, type StoredRules } from '../shared/rules.ts';

const HIGHLIGHT = 'ecomsbd-phone';
const host = location.hostname;
let selectors: Selectors | null = null;
let phones: CompiledPhoneRules | null = null;
let pending: number | undefined;

async function load(): Promise<void> {
  const { rules } = (await chrome.storage.local.get('rules')) as { rules?: StoredRules };
  selectors = hostSelectors(rules?.body, host);
  phones = rules ? compilePhoneRules(rules.body.rules.phone) : null;
  schedule();
}

function read(): ReadResponse | null {
  if (!isHost(host)) return null;
  const supported = selectors !== null;
  const selection = readSelection(window.getSelection()?.toString());
  if (selection.ok) return { kind: 'SELECTION', host, messages: selection.messages, supported };
  if (selectors) {
    const conversation = readConversation(document, selectors);
    if (conversation.ok) {
      return {
        kind: 'CONVERSATION',
        host,
        conversation_key: conversation.conversation_key,
        sender_display: conversation.sender_display,
        messages: conversation.messages,
      };
    }
  }
  return { kind: 'NOTHING', host, reason: selection.reason, supported: false };
}

function highlight(): void {
  pending = undefined;
  const registry = typeof CSS !== 'undefined' ? CSS.highlights : undefined;
  if (!registry) return;
  registry.delete(HIGHLIGHT);
  if (!selectors || !phones) return;
  let root: Element | null = null;
  try {
    root = document.querySelector(selectors.conversation);
  } catch {
    root = null;
  }
  if (!root) return;
  const ranges: Range[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node !== null && ranges.length < 200; node = walker.nextNode()) {
    const text = node.nodeValue ?? '';
    if (text.length < 10) continue;
    for (const span of phoneSpans(text, phones)) {
      const range = document.createRange();
      range.setStart(node, span.start);
      range.setEnd(node, span.end);
      ranges.push(range);
    }
  }
  if (ranges.length > 0) registry.set(HIGHLIGHT, new Highlight(...ranges));
}

function schedule(): void {
  if (pending !== undefined) window.clearTimeout(pending);
  pending = window.setTimeout(highlight, 400);
}

const style = document.createElement('style');
style.textContent = `::highlight(${HIGHLIGHT}) { background-color: rgba(250, 204, 21, 0.45); }`;
document.documentElement.appendChild(style);

// Re-highlight when the page changes; the work is limited to the open chat.
new MutationObserver(() => {
  if (selectors) schedule();
}).observe(document.documentElement, { childList: true, subtree: true, characterData: true });

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && 'rules' in changes) void load();
});

chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
  if ((message as ReadRequest | null)?.type === READ) sendResponse(read());
  return false;
});

void load();
