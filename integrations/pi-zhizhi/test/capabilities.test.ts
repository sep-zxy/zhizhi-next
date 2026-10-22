import assert from 'node:assert/strict';
import test from 'node:test';
import { capabilitySummary } from '../src/capabilities.ts';

test('capability view distinguishes registered names, design placeholders and verified readiness', () => {
  const text = capabilitySummary(['zhizhi_search', 'zhizhi_job_status'], false);
  assert.match(text, /zhizhi_search、zhizhi_job_status/);
  assert.match(text, /本人决定命令未注册/);
  assert.doesNotMatch(text, /本人 TUI 命令：/);
  assert.match(text, /不表示后端\/账号已就绪/);
  assert.match(text, /kb … 是设计命令占位/);
  assert.match(text, /尚未接入统一 CLI/);
  assert.match(text, /不调用模型、不检查账号、不写知识/);
});

test('registered human commands still require actual identity and confirmation', () => {
  const text = capabilitySummary([], true);
  assert.match(text, /仍须有效身份及具体确认/);
  assert.match(text, /提案不等于 Writer 提交/);
  assert.match(text, /公开意图不等于已发布/);
});
