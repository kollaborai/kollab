import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const frontend = resolve(import.meta.dirname, '../..');

test('production plugin runtime entrypoints retain their exports', async () => {
  const root = process.env.KOLLAB_PLUGIN_TEST_DIR || tmpdir();
  const out = mkdtempSync(join(root, 'plugin-runtime-'));
  execFileSync(process.execPath, ['node_modules/vite/bin/vite.js', 'build', '--outDir', out, '--emptyOutDir'], {
    cwd: frontend, env: process.env, stdio: 'pipe', timeout: 120_000,
  });
  const load = name => import(pathToFileURL(join(out, 'assets', `plugin-${name}.js`)).href);
  const [react, dom, jsx, sdk] = await Promise.all(['react', 'react-dom', 'jsx-runtime', 'sdk'].map(load));
  assert.equal(typeof react.useState, 'function');
  assert.equal(typeof react.createElement, 'function');
  assert.equal(typeof dom.createPortal, 'function');
  assert.equal(typeof jsx.jsx, 'function');
  assert.equal(typeof sdk.definePlugin, 'function');
});
