/**
 * Service worker: opens the side panel from the toolbar button and, once an
 * hour, refreshes the rules (when due) and sends the heartbeat (every 6 h).
 * It never reads a page: only the content script does, when asked.
 */

import { heartbeat, refreshRules } from './shared/session.ts';

const ALARM = 'ecomsbd.refresh';

function setUp(): void {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  void chrome.alarms.create(ALARM, { periodInMinutes: 60 });
}

chrome.runtime.onInstalled.addListener(setUp);
chrome.runtime.onStartup.addListener(setUp);

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== ALARM) return;
  void refreshRules().catch(() => undefined);
  void heartbeat().catch(() => undefined);
});
