import type { WikiDraft } from '../api/knowledge';
import type { UnderstandingSubmission } from '../api/understanding';

export type WritebackSnapshot = { submission?: UnderstandingSubmission; unavailable: boolean };

/** A read-only projection: task completion alone never proves review or Writer success. */
export function writebackSteps(draft: WikiDraft | null, snapshot: WritebackSnapshot): string[] {
  const value = snapshot.submission;
  if (snapshot.unavailable || value?.remote_state === 'unknown') {
    return ['提交状态待核对', '复核状态未确认', '写入状态未确认'];
  }
  if (value) {
    if (value.delivery_state === 'pending') return ['已保存到本机待发', '尚未确认受理', '尚未确认写入'];
    if (value.delivery_state === 'rejected') return ['提交未被受理', '尚未进入复核', '尚未确认写入'];
    const change = value.change;
    if (!change) {
      const taskStatus = ({ failed: '候选生成失败，尚未进入复核', cancelled: '生成任务已取消', waiting_auth: '生成任务等待授权', retry_wait: '生成任务等待服务重试' } as Record<string, string>)[value.task?.state ?? ''];
      return ['服务已受理', taskStatus ?? '等待生成或读取候选', '尚未确认写入'];
    }
    const written = value.remote_state === 'available' && ['committed', 'indexed'].includes(change.state) && (change.committed_revision ?? 0) > 0;
    if (written) return ['服务已受理', '已进入正式写入', `已确认写入 v${change.committed_revision}`];
    const review = ({ needs_ai_review: '等待独立复核', needs_user: '等待重要确认', approved: '复核已通过', rejected: '候选未通过复核', no_change: '本次未产生知识变更' } as Record<string, string>)[change.state] ?? '复核状态未确认';
    return ['服务已受理', review, change.state === 'approved' ? '等待正式写入' : '尚未确认写入'];
  }
  if (draft?.delivery_error) return ['递送未获确认', '复核状态未确认', '写入状态未确认'];
  if (draft?.status === 'published') return ['旧记录已提交', '未提供独立复核详情', '旧记录已写入，Writer 状态未提供'];
  if (draft?.status === 'submitted') return ['已提交', '待核对复核结果', '尚未确认写入'];
  return [draft ? '候选草稿，尚未提交' : '尚未提交', '尚未进入复核', '尚未进入正式写入'];
}

export default function WritebackProgress({ draft, snapshot }: { draft: WikiDraft | null; snapshot: WritebackSnapshot }) {
  const steps = writebackSteps(draft, snapshot);
  return <section className="zz-panel zz-writeback-progress" aria-label="成果回填处理进度">
    <ol className="zz-session-steps">
      {['提交成果', 'AI 复核', '唯一 Writer 提交'].map((title, index) => <li key={title}>
        <span aria-hidden="true">{index + 1}</span><div><strong>{title}</strong><small>{steps[index]}</small></div>
      </li>)}
    </ol>
    <p className="zz-muted">提交不等于发布；正式写入也不代表公开。{snapshot.submission && <>当前展示本人提交 v{snapshot.submission.card_version} 的状态。</>}</p>
  </section>;
}
