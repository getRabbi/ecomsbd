import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { join, relative, sep } from 'node:path';
import { test } from 'node:test';

/**
 * The web and the phone are one account on one backend.
 *
 * Supabase signs the seller in; FastAPI owns every shop, permission and record.
 * These checks keep the web client on that path: Supabase for auth only, the
 * configured API for data, and a sign-out that ends this browser's session
 * rather than the seller's phone as well.
 */

const ROOT = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const SOURCE_DIRS = ['app', 'components', 'lib'];

async function sources() {
  const files = [];
  async function walk(dir) {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) {
        await walk(path);
      } else if (/\.(ts|tsx)$/.test(entry.name)) {
        files.push(path);
      }
    }
  }
  for (const dir of SOURCE_DIRS) {
    await walk(join(ROOT, dir));
  }
  return Promise.all(
    files.map(async (path) => ({
      file: relative(ROOT, path).split(sep).join('/'),
      code: code(await readFile(path, 'utf8')),
    })),
  );
}

/**
 * Source with comments and template literals removed. The developer pages
 * show sellers copy-paste `fetch` examples for their own sites; those are text
 * the page renders, not calls this app makes.
 */
function code(source) {
  return source
    .replace(/`(?:\\[\s\S]|[^`\\])*`/g, '``')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

test('every sign-out ends this browser session only', async () => {
  const calls = [];
  for (const { file, code: source } of await sources()) {
    for (const match of source.matchAll(/\.auth\.signOut\(([^)]*)\)/g)) {
      calls.push({ file, args: match[1].trim() });
    }
  }
  // One place signs out, and it says so explicitly. supabase-js defaults to
  // `global`, which would sign the same account out on the phone too.
  assert.deepEqual(calls, [{ file: 'lib/supabase.ts', args: "{ scope: 'local' }" }]);
});

test('Supabase is used for authentication, never for business data', async () => {
  for (const { file, code: source } of await sources()) {
    assert.doesNotMatch(
      source,
      /(getSupabase\(\)|supabase|client)\s*\.\s*(from|rpc|channel|storage|schema)\b/,
      `${file} reaches Supabase data directly; business data goes through FastAPI`,
    );
    if (/@supabase\//.test(source)) {
      assert.ok(
        ['lib/supabase.ts', 'lib/session.tsx'].includes(file),
        `${file} imports Supabase; only the auth client and session may`,
      );
    }
  }
});

test('the only network calls go to the configured FastAPI', async () => {
  const callers = [];
  for (const { file, code: source } of await sources()) {
    if (/\bfetch\(/.test(source)) {
      callers.push(file);
    }
  }
  // lib/api.ts builds every URL from NEXT_PUBLIC_API_BASE_URL; the WhatsApp
  // signup helper is handed that same base by its caller.
  assert.deepEqual(callers.sort(), ['lib/api.ts', 'lib/whatsapp-signup.ts']);

  const api = await readFile(join(ROOT, 'lib/api.ts'), 'utf8');
  assert.match(api, /const base = config\.apiBaseUrl/);
  assert.equal((api.match(/fetch\(buildUrl\(/g) ?? []).length, 3);
});

test("a shop switch in one tab moves this browser's other tabs too", async () => {
  // The shop is bound to the Supabase session, which every tab shares. A tab
  // left on the old shop would show its name over the new shop's data.
  const shop = code(await readFile(join(ROOT, 'lib/shop.tsx'), 'utf8'));
  assert.match(shop, /api\.post\('\/auth\/select-tenant'[^;]*;\s*writePref\(LAST_SHOP_KEY, tenantId\)/);
  assert.match(shop, /addEventListener\('storage', onStorage\)/);
  assert.match(shop, /event\.key === LAST_SHOP_KEY\)\s*\{\s*reload\(\);/);
});

test('no server-side data path exists beside the API', async () => {
  for (const { file, code: source } of await sources()) {
    assert.ok(!/(^|\/)route\.tsx?$/.test(file), `${file} is a route handler`);
    assert.doesNotMatch(source, /^\s*['"]use server['"]/m, `${file} declares a server action`);
  }
});
