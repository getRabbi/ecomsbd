// Build: `tsc` to dist/, then the static files and the manifest.
//   ECOMSBD_API_BASE_URL  (default https://api.scalemyprints.com)
//   ECOMSBD_WEB_BASE_URL  (default https://app.ecomsbd.com)
// Output: apps/extension/dist — load it unpacked, or zip it for the store.

import { execFileSync } from 'node:child_process';
import { cpSync, existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { baseUrl, buildManifest } from './manifest.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
const DEFAULT_API = 'https://api.scalemyprints.com';
const DEFAULT_WEB = 'https://app.ecomsbd.com';
const api = baseUrl(process.env.ECOMSBD_API_BASE_URL || DEFAULT_API, 'ECOMSBD_API_BASE_URL');
const web = baseUrl(process.env.ECOMSBD_WEB_BASE_URL || DEFAULT_WEB, 'ECOMSBD_WEB_BASE_URL');

rmSync(dist, { recursive: true, force: true });
const tsc = join(root, 'node_modules', 'typescript', 'bin', 'tsc');
if (!existsSync(tsc)) throw new Error('run npm ci first');
execFileSync(process.execPath, [tsc, '-p', join(root, 'tsconfig.json')], { stdio: 'inherit' });

const configPath = join(dist, 'shared', 'config.js');
const config = readFileSync(configPath, 'utf8');
if (!config.includes(`'${DEFAULT_API}'`) || !config.includes(`'${DEFAULT_WEB}'`)) {
  throw new Error('shared/config.js no longer holds the default URLs');
}
writeFileSync(configPath, config.replace(`'${DEFAULT_API}'`, `'${api}'`).replace(`'${DEFAULT_WEB}'`, `'${web}'`));

for (const file of ['sidepanel.html', 'sidepanel.css', 'icon-128.png']) {
  cpSync(join(root, 'static', file), join(dist, file));
}
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const template = JSON.parse(readFileSync(join(root, 'static', 'manifest.json'), 'utf8'));
writeFileSync(
  join(dist, 'manifest.json'),
  `${JSON.stringify(buildManifest(template, { version: pkg.version, apiBaseUrl: api }), null, 2)}\n`,
);
console.log(`built dist/ (api ${api}, web ${web})`);
