import { statSync } from 'node:fs';
import { resolve } from 'node:path';

/** Inline HTML imports are strings, so tsc cannot detect a moved source file. */
export function assertHarnessSources(html: string, viewerRoot: string): string[] {
  const paths = [...new Set([...html.matchAll(/['"](\/src\/[^'"?#]+)(?:[?#][^'"]*)?['"]/g)]
    .map(match => match[1]))];
  for (const path of paths) {
    let exists = false;
    try { exists = statSync(resolve(viewerRoot, '.' + path)).isFile(); } catch { /* Report the source URL below. */ }
    if (!exists) throw new Error(`Harness references a missing source: ${path}`);
  }
  return paths;
}
