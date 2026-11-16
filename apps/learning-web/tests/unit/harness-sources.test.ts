import { readFileSync, readdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { assertHarnessSources } from '../fixtures/harness-sources';

const unitRoot = dirname(fileURLToPath(import.meta.url));
const viewerRoot = resolve(unitRoot, '../..');
const harnesses = readdirSync(unitRoot).filter(name => /\.test\.tsx?$/.test(name))
  .flatMap(name => {
    const source = readFileSync(resolve(unitRoot, name), 'utf8');
    // The browser fixtures deliberately use literal templates, not evaluated test code.
    const html = source.match(/const HARNESS_HTML = (?:String\.raw)?`([\s\S]*?)`;/)?.[1];
    return html ? [{ name, html }] : [];
  });

describe('inline browser harness source dependencies (no browser required)', () => {
  it('discovers the active-practice fixture and the other browser harnesses', () => {
    expect(harnesses.map(harness => harness.name)).toEqual(expect.arrayContaining([
      'active-exercise.test.ts', 'claim-inspector-fidelity.test.ts', 'graphify-card.test.ts',
      'info-hint.test.ts', 'landing-page.test.ts', 'learn-mode-dialog.test.ts',
    ]));
  });

  it.each(harnesses)('$name only references existing source modules and styles', ({ html }) => {
    expect(assertHarnessSources(html, viewerRoot).length).toBeGreaterThan(0);
  });

  it('fails explicitly for the removed tokens stylesheet instead of waiting for __activeReady', () => {
    expect(() => assertHarnessSources("import '/src/styles/tokens.css';", viewerRoot))
      .toThrow('Harness references a missing source: /src/styles/tokens.css');
  });

  it('active practice uses the same base stylesheet as the real application entry', () => {
    const main = readFileSync(resolve(viewerRoot, 'src/main.tsx'), 'utf8');
    const active = harnesses.find(harness => harness.name === 'active-exercise.test.ts')!;
    expect(main).toContain("import './growth/GrowthBase.css'");
    expect(assertHarnessSources(active.html, viewerRoot)).toContain('/src/growth/GrowthBase.css');
  });
});
