import { afterEach, describe, expect, it, vi } from 'vitest';
import { exportPreview, resetToken, setToken } from './client';
import { ValidationError } from './schemas';

describe('exportPreview API boundary', () => {
  afterEach(() => {
    resetToken();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('validates the serve response shape before returning it', async () => {
    vi.stubGlobal('window', { location: { origin: 'http://localhost:8765' } });
    setToken('test-token');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          path: 'exports/run-1',
          manifest_digest: '0'.repeat(64),
          file_count: '4',
          total_bytes: 123,
          created_at_utc: '2026-05-12T00:00:00Z',
          privacy_mode: 'strict_local',
          run_id: 'run-1',
          cleared_stale_files: [],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );

    await expect(exportPreview('run-1')).rejects.toBeInstanceOf(ValidationError);
  });
});

describe('explicit understanding submission cannot be replayed on auth failure', () => {
  afterEach(() => { resetToken(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
  it.each([401, 403])('does not bootstrap or repeat POST after HTTP %i', async status => {
    vi.stubGlobal('window', { location: { origin: 'http://localhost:8765' } });
    setToken('fixture-existing-token');
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ error: 'not_authorized' }), { status }));
    const { apiFetch } = await import('./client');
    await expect(apiFetch('/api/growth/local/knowledge/cards/c1/understanding/submissions', {
      method: 'POST', retryAuth: false, body: JSON.stringify({ card_version: 1, note_sha256: 'a'.repeat(64) }),
    })).rejects.toMatchObject({ status });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe('http://localhost:8765/api/growth/local/knowledge/cards/c1/understanding/submissions');
    expect(fetch.mock.calls[0][1]).not.toHaveProperty('retryAuth');
  });
});

describe('existing callers retain auth refresh behavior', () => {
  afterEach(() => { resetToken(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
  it('refreshes once when retryAuth was not explicitly disabled', async () => {
    vi.stubGlobal('window', { location: { origin: 'http://localhost:8765' } });
    setToken('fixture-stale-token');
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response('{}', { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ token: 'fixture-fresh-token' })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [] })));
    const { apiFetch } = await import('./client');
    await expect(apiFetch('/api/growth/local/knowledge')).resolves.toEqual({ items: [] });
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(fetch.mock.calls[1][0]).toBe('http://localhost:8765/api/auth/token');
    expect(fetch.mock.calls[2][0]).toBe('http://localhost:8765/api/growth/local/knowledge');
  });
});
