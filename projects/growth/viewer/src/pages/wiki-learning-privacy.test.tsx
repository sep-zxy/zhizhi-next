import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { WikiLearningPrivateArea } from './WikiLearningPage';
import type { WikiLearningContext } from '../api/wiki-learning';

const secret = 'private-own-note-sentinel';
const content = createElement('textarea', { defaultValue: secret, 'aria-label': '本人原话' });
describe('自由学习缓存的本次授权显示边界', () => {
  it('未取得或失去授权时，即使内存有原话也不输出隐藏 DOM 或 textarea', () => {
    const html = renderToStaticMarkup(createElement(WikiLearningPrivateArea, { context: null, children: content }));
    expect(html).not.toContain(secret); expect(html).not.toContain('textarea'); expect(html).toContain('授权成功前不显示缓存原话或候选');
  });
  it('本次精确来源授权成功后才可显示本人保留输入', () => {
    const context: WikiLearningContext = { context_id: '44444444-4444-4444-8444-444444444444', source_kind: 'wiki_entry', context_version: 1, created_at: '', updated_at: '', state: 'reading', note_text: '', note_sha256: 'a'.repeat(64), entry: { entry_id: 'entry1', revision: 1, title: '合成来源', body: '', content_hash: 'b'.repeat(64), evidence_refs: [], data_classification: 'personal', classification_labels: [] } };
    expect(renderToStaticMarkup(createElement(WikiLearningPrivateArea, { context, children: content }))).toContain(secret);
  });
});
