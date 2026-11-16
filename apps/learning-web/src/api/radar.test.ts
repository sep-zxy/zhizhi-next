import {beforeEach, describe, expect, it, vi} from 'vitest';
const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('./client', () => ({apiFetch}));
import {getRadarSuggestions, radarSubmissionLabel, readRadarPractice, submitRadarPractice} from './radar';

describe('Radar real-expression submission', () => {
  beforeEach(() => apiFetch.mockReset());
  it('never accepts an empty expression or invents text', async () => {
    await expect(submitRadarPractice('s1', '  ')).rejects.toThrow('自己的理解');
    expect(apiFetch).not.toHaveBeenCalled();
  });
  it('coalesces double submit and preserves exact original words', async () => {
    let done!: (value: unknown) => void;
    apiFetch.mockImplementationOnce(() => new Promise(resolve => {done = resolve;}));
    const text = ' 我认为事件重放的代价需要单独验证。\n';
    const first = submitRadarPractice('s2', text);
    expect(submitRadarPractice('s2', text)).toBe(first);
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({user_text: text});
    done({state: 'pending', suggestion_id: 's2', published: false});
    await expect(first).resolves.toMatchObject({state: 'pending', published: false});
    expect(radarSubmissionLabel('pending')).toContain('等待');
    expect(radarSubmissionLabel('submitted')).toContain('仍待生成与复核');
  });
  it('does not treat a generic success as published knowledge', async () => {
    apiFetch.mockResolvedValue({ok: true});
    await expect(submitRadarPractice('s3', '真实原话')).rejects.toThrow('未确认');
  });
  it('restores the suggestion using the existing same-origin authenticated API', async () => {
    apiFetch.mockResolvedValue({suggestion_id: 's4', state: 'pending', user_text: '保留的原话'});
    await expect(readRadarPractice('s4')).resolves.toMatchObject({user_text: '保留的原话'});
    expect(apiFetch).toHaveBeenCalledWith('/api/growth/local/knowledge/radar/s4');
  });
  it('handles an empty recommendation set without inventing a practice', async () => {
    apiFetch.mockResolvedValue({suggestions: []});
    await expect(getRadarSuggestions()).resolves.toEqual([]);
  });
});
