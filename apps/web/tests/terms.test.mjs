import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import { termsHeading, termsSections } from '../app/terms/content.ts';

// Launch run 13 D3. Draft, pending legal review (docs/launch/LAUNCH_STATUS.md).

test('every section reads in English and Bangla', () => {
  assert.ok(termsSections.length >= 10);
  const ids = new Set();
  for (const section of termsSections) {
    assert.ok(!ids.has(section.id), `unique id ${section.id}`);
    ids.add(section.id);
    for (const locale of ['en', 'bn']) {
      const part = section[locale];
      assert.ok(part.title.trim(), `${section.id} ${locale} title`);
      assert.ok(part.paragraphs.length > 0, `${section.id} ${locale} text`);
    }
    assert.equal(section.en.paragraphs.length, section.bn.paragraphs.length, `${section.id}: same paragraphs`);
  }
  assert.ok(termsHeading.en.title && termsHeading.bn.title);
});

test('the terms say what the operator asked them to say', () => {
  const en = termsSections.map((section) => [section.en.title, ...section.en.paragraphs].join(' ')).join(' ');
  assert.match(en, /Free Launch Mode/);
  assert.match(en, /billing is switched off/);
  assert.match(en, /belong to you/);
  assert.match(en, /Courier accounts are yours/);
  assert.match(en, /your own account with that courier/);
  assert.match(en, /No guarantee of delivery/);
  assert.match(en, /We do not guarantee that a courier/);
  assert.doesNotMatch(en, /scalemyprints/);
});

test('the terms are linked from the site footer, sign-up and the legal pages', async () => {
  const read = (path) => readFile(new URL(path, import.meta.url), 'utf8');
  assert.match(await read('../app/ecomsbd/home.tsx'), /termsPath\[locale\]/);
  const signIn = await read('../app/sign-in/page.tsx');
  assert.match(signIn, /data-testid="signup-terms"/);
  assert.match(signIn, /'\/terms\/bn' : '\/terms'/);
  for (const page of ['../app/privacy-policy/policy.tsx', '../app/account-deletion/deletion.tsx']) {
    assert.match(await read(page), /'\/terms\/bn' : '\/terms'/, page);
  }
  assert.match(await read('../app/sitemap.ts'), /\/terms\/bn/);
});
