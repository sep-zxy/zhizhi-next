import { useEffect, useRef, useState } from 'react';
import { Button, Textarea } from '@mantine/core';
import {
  decideRadar, getRadarSuggestions, radarSubmissionLabel, readRadarPractice,
  submitRadarPractice, type RadarPractice, type RadarSuggestion,
} from '../api/radar';

export default function RadarPracticePanel({activeId, onSelect}: {
  activeId: string|null; onSelect: (id: string|null) => void;
}) {
  const [suggestions, setSuggestions] = useState<RadarSuggestion[]>([]);
  const [current, setCurrent] = useState<RadarPractice|null>(null);
  const [text, setText] = useState('');
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const working = useRef(false);

  useEffect(() => {
    let live = true;
    setCurrent(null); setText(''); setStatus(''); setError('');
    if (activeId) {
      void readRadarPractice(activeId).then(value => {
        if (!live) return;
        setCurrent(value); setText(value.user_text ?? '');
        if (value.state === 'pending') setStatus(radarSubmissionLabel('pending'));
        else if (value.suggestion_state === 'completed') setStatus(radarSubmissionLabel('submitted'));
      }).catch(e => { if (live) setError(e instanceof Error ? e.message : '无法恢复学习记录。'); });
    }
    return () => { live = false; };
  }, [activeId]);

  async function act(action: () => Promise<void>) {
    if (working.current) return;
    working.current = true; setBusy(true); setError('');
    try { await action(); }
    catch (e) { setError(e instanceof Error ? e.message : '暂时无法完成，请保留原话重试。'); }
    finally { working.current = false; setBusy(false); }
  }

  return <section className="knowledge-paper" aria-label="架构学习雷达">
    <h2>架构学习雷达</h2>
    <p>从已有条目的待验证问题出发，补齐技术特点、设计取舍与适用边界。接受建议不代表已经掌握。</p>
    {error && <p role="alert">{error}</p>}
    {status && <p role="status">{status}</p>}
    {!activeId && <>
      <Button variant="default" loading={busy} onClick={() => void act(async () => {
        const next = await getRadarSuggestions(); setSuggestions(next);
        setStatus(next.length ? '' : '当前没有有据可查的待验证问题。');
      })}>查看学习建议</Button>
      <ul>{suggestions.map(item => <li key={item.suggestion_id}>
        <h3>{item.proposed_exercise}</h3><p>{item.reason}</p>
        {item.expected_artifact && <p>练习产出：{item.expected_artifact}</p>}
        <Button disabled={busy} onClick={() => void act(async () => {
          const value = await decideRadar(item.suggestion_id, 'accept');
          setCurrent(value); onSelect(item.suggestion_id);
        })}>开始练习</Button>{' '}
        <Button variant="subtle" disabled={busy} onClick={() => void act(async () => {
          await decideRadar(item.suggestion_id, 'snooze');
          setSuggestions(items => items.filter(row => row.suggestion_id !== item.suggestion_id));
        })}>稍后</Button>{' '}
        <Button variant="subtle" disabled={busy} onClick={() => void act(async () => {
          await decideRadar(item.suggestion_id, 'reject');
          setSuggestions(items => items.filter(row => row.suggestion_id !== item.suggestion_id));
        })}>不采用</Button>
      </li>)}</ul>
    </>}
    {activeId && !current && !error && <p role="status">正在恢复练习…</p>}
    {current?.practice && <>
      <h3>{current.practice.title}</h3>
      <details><summary>查阅已有知识与依据</summary>
        <pre style={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{current.practice.body}</pre>
      </details>
      <p><strong>待验证问题：</strong>{current.practice.question}</p>
      <Textarea label="用自己的话说明架构取舍或应用思路"
        description="只记录你的真实表达，不会由 AI 代写“我的理解”。"
        minRows={4} maxLength={12000} value={text}
        disabled={busy || current.suggestion_state === 'completed'}
        onChange={event => setText(event.currentTarget.value)} />
      <Button mt="sm" loading={busy} disabled={!text.trim() || current.suggestion_state === 'completed'}
        onClick={() => void act(async () => {
          const result = await submitRadarPractice(current.suggestion_id, text);
          setStatus(radarSubmissionLabel(result.state));
          setCurrent(value => value ? {...value,
            suggestion_state: result.state === 'submitted' ? 'completed' : value.suggestion_state,
            user_text: text, state: result.state} : value);
        })}>提交学习原话</Button>
    </>}
    {activeId && <Button variant="subtle" mt="sm" disabled={busy} onClick={() => onSelect(null)}>
      返回学习建议
    </Button>}
  </section>;
}
