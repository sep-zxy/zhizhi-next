import { describe, expect, it } from 'vitest';
import { isBackendApiRoute } from '../fixtures/api-routes';

describe('browser fixture backend API boundary', () => {
  it.each(['/api/foo', '/api/locale', '/api/growth/local/knowledge?revision=2', '/api/unknown'])('intercepts real API path %s, including undeclared APIs for strict rejection', path => {
    expect(isBackendApiRoute(new URL(path, 'http://localhost:5173'))).toBe(true);
  });
  it.each(['/src/api/locale.ts', '/src/api/entry-context.ts?t=1', '/node_modules/.vite/deps/api.js', '/node_modules/some/api/client.js', '/assets/index.js?source=/api/foo', '/api%2Ffoo'])('does not hijack a source module or non-API path %s', path => {
    expect(isBackendApiRoute(new URL(path, 'http://localhost:5173'))).toBe(false);
  });
});
