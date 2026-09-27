import assert from 'node:assert/strict';
import { test } from 'node:test';
import { launchCopy, showPlanUi, showPlanLock } from '../lib/access.ts';

const freeLaunch = { plan: 'free', free_launch_mode: true, billing_enabled: false,
  effective_access: 'full_access', entitlements: { advanced_profit: true } };

test('free launch hides plan navigation and plan overlays but keeps permission locks', () => {
  assert.equal(showPlanUi(freeLaunch), false);
  assert.equal(showPlanLock(freeLaunch, 'PLAN'), false);
  assert.equal(showPlanLock(freeLaunch, 'PERMISSION'), true);
  assert.equal(showPlanLock(freeLaunch, null), false);
  assert.equal(freeLaunch.plan, 'free');
});

test('configuration reversal restores plan UI and locks; loading never flashes purchase UI', () => {
  const paid = { ...freeLaunch, free_launch_mode: false };
  assert.equal(showPlanUi(paid), true);
  assert.equal(showPlanLock(paid, 'PLAN'), true);
  assert.equal(showPlanUi(null), false);
  assert.equal(showPlanLock(null, 'PLAN'), false);
  assert.equal(showPlanLock(null, 'PERMISSION'), true);
});

test('the stale route and settings copy are translated without implying a paid subscription', () => {
  assert.equal(launchCopy.en.fullAccess, 'Full access — Free launch');
  assert.equal(launchCopy.bn.fullAccess, 'সম্পূর্ণ অ্যাক্সেস — ফ্রি লঞ্চ');
  assert.equal(launchCopy.en.message, 'All ecomsbd features are currently available for free.');
  assert.equal(launchCopy.bn.message, 'ecomsbd-এর সব ফিচার বর্তমানে বিনামূল্যে ব্যবহার করা যাচ্ছে।');
});
