import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

const sketch = fileURLToPath(new URL('../../../../docs/handoff/plugins/step3-states.html', import.meta.url));

describe('plugin recovery state sketch', () => {
  it('shows each recovery state and its recovery action', () => {
    const html = readFileSync(sketch, 'utf8');
    for (const text of [
      'Turn off PR Chain?', 'Stopping', 'PR Chain is off', 'Back to Chat',
      'Failed to Load', 'Failed to Render', 'Retry', 'View Error', 'Turn Off',
      'Stopped After 5 Crashes', 'View Log', 'No plugins installed',
      'Plugins are off for this page', 'Changed on Disk',
      'PR Chain was skipped: it froze the page last time.', 'Turn On Again',
    ]) assert.ok(html.includes(text), `missing sketch copy: ${text}`);
  });

  it('includes responsive and accessible review surfaces', () => {
    const html = readFileSync(sketch, 'utf8');
    assert.match(html, /name="viewport"/);
    assert.match(html, /@media\s*\(max-width:\s*600px\)/);
    assert.match(html, /prefers-color-scheme:\s*light/);
    assert.match(html, /aria-label="Plugin recovery state sketches"/);
    assert.match(html, /type="button"/);
  });
});
