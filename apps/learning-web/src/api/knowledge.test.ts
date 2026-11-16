import { beforeEach, describe, expect, it, vi } from 'vitest';
const apiFetch = vi.hoisted(()=>vi.fn());
vi.mock('./client', ()=>({apiFetch, apiFetchResponse:vi.fn(), ApiError:class extends Error {}}));
import { focusLabel, submitWikiDraft } from './knowledge';

describe('Wiki common change submission', ()=>{
  beforeEach(()=>apiFetch.mockReset());
  it('awaits acknowledgment and does not mistake 202 for publication', async()=>{
    let finish!:(value:unknown)=>void;
    apiFetch.mockResolvedValueOnce({}).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    let settled=false;
    const pending=submitWikiDraft('pending-test','architecture').then(value=>{settled=true;return value;});
    await Promise.resolve();
    expect(settled).toBe(false);
    finish({state:'submitted',published:false,change_id:'change-1'});
    expect(await pending).toMatchObject({state:'submitted',change_id:'change-1'});
  });
  it('surfaces an error and reuses the request identity on retry', async()=>{
    apiFetch.mockResolvedValueOnce({}).mockRejectedValueOnce(new Error('offline'));
    await expect(submitWikiDraft('retry-test','same draft')).rejects.toThrow('offline');
    const first=JSON.parse(apiFetch.mock.calls[1][1].body).request_id;
    apiFetch.mockResolvedValueOnce({}).mockResolvedValueOnce({state:'submitted',published:false});
    await expect(submitWikiDraft('retry-test','same draft')).resolves.toMatchObject({state:'submitted'});
    expect(JSON.parse(apiFetch.mock.calls[3][1].body).request_id).toBe(first);
  });
  it('coalesces duplicate clicks into one save and one proposal', async()=>{
    let save!:(value:unknown)=>void;
    apiFetch.mockImplementationOnce(()=>new Promise(resolve=>{save=resolve;})).mockResolvedValueOnce({state:'submitted',published:false});
    const first=submitWikiDraft('click-test','one');
    const second=submitWikiDraft('click-test','one');
    expect(second).toBe(first);
    expect(apiFetch).toHaveBeenCalledTimes(1);
    save({});await first;
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });
  it('requires explicit persisted article evidence for local publication', async()=>{
    apiFetch.mockResolvedValueOnce({}).mockResolvedValueOnce({ok:true});
    await expect(submitWikiDraft('invalid-test','body')).rejects.toThrow('尚未确认写入');
    apiFetch.mockResolvedValueOnce({}).mockResolvedValueOnce({article_id:'article',published_at:'2026-10-19'});
    await expect(submitWikiDraft('local-test','body')).resolves.toEqual({state:'published'});
  });
  it('explains architecture and syntax ranking without deleting old concepts', ()=>{
    expect(focusLabel('architecture')).toBe('架构设计');
    expect(focusLabel('technology_stack')).toBe('技术栈深度应用');
    expect(focusLabel('syntax_detail')).toContain('低优先');
  });
});
