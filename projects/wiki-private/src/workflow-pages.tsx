import { useEffect, useRef, useState, type FormEvent } from "react";
import { PAGE_SIZE, WikiApiError, safeCode, type WikiApi } from "./api";
import {
  Badge,
  Empty,
  Failure,
  Field,
  Icon,
  MarkdownBody,
  Notice,
  PageTitle,
  ResourceView,
  SafeExternal,
} from "./components";
import {
  fields,
  strings,
  text,
  type Change,
  type DecisionChallenge,
  type Entry,
  type Intake,
} from "./contracts";
import {
  formatTime,
  navigate,
  routeHref,
  TASK_LABELS,
  taskLabel,
  taskResult,
  taskTone,
  useResource,
} from "./state";

const INTAKE_LABELS: Record<string, string> = {
  received: "待预览",
  normalized: "链接已规范化",
  fetching: "正在获取",
  extracted: "已提取",
  preview_ready: "待决定",
  awaiting_keep: "等待保留决定",
  partial: "内容不完整",
  retained: "已私有保留",
  kept: "已保留",
  knowledge_processing: "知识处理中",
  reviewed: "已复核",
  committed: "已提交知识",
  needs_auth: "来源需要登录",
  rate_limited: "来源限流",
  unsupported: "暂不支持",
  needs_input: "需要补料",
  waiting_model: "等待模型",
  discarded: "已丢弃",
  expired: "已到期",
};
const intakeLabel = (state: string) => INTAKE_LABELS[state] || state;
function IdLookup({
  label,
  placeholder,
  onOpen,
}: {
  label: string;
  placeholder: string;
  onOpen: (id: string) => void;
}) {
  const [value, setValue] = useState("");
  return (
    <form
      className="id-lookup"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onOpen(value.trim());
      }}
    >
      <label>
        {label}
        <input
          value={value}
          maxLength={256}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
        />
      </label>
      <button className="button" disabled={!value.trim()}>
        按 ID 打开
      </button>
    </form>
  );
}

export function TasksPage({ api, id }: { api: WikiApi; id: string }) {
  const [page, setPage] = useState(0);
  const [state, setState] = useState("");
  const list = useResource("tasks:" + page + ":" + state, (signal) =>
    api.tasks(page * PAGE_SIZE, state, signal),
  );
  return (
    <>
      <PageTitle
        title="后台任务与变更历史"
        description="查看真实处理状态与可恢复节点。任务完成不等于知识已公开。"
      />
      <IdLookup
        label="任务 ID"
        placeholder="tsk_…"
        onOpen={(value) => navigate(routeHref("tasks", value))}
      />
      {id ? (
        <TaskDetail key={id} api={api} id={id} />
      ) : (
        <>
          <div className="toolbar">
            <label>
              状态筛选{" "}
              <select
                value={state}
                onChange={(e) => {
                  setState(e.target.value);
                  setPage(0);
                }}
              >
                <option value="">全部状态</option>
                {Object.entries(TASK_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <button className="button" onClick={list.reload}>
              刷新列表
            </button>
          </div>
          <ResourceView resource={list}>
            {({ data }) => (
              <section className="panel">
                {data.items.length ? (
                  <div className="task-list">
                    {data.items.map((task) => (
                      <a
                        className="task-row"
                        href={routeHref("tasks", task.task_id)}
                        key={task.task_id}
                      >
                        <Icon name="task" />
                        <span>
                          <strong>{task.kind}</strong>
                          <small>{task.task_id}</small>
                        </span>
                        <Badge tone={taskTone(task.state)}>
                          {taskLabel(task.state)}
                        </Badge>
                        <time>{formatTime(task.updated_at)}</time>
                        <span>›</span>
                      </a>
                    ))}
                  </div>
                ) : (
                  <Empty title="没有符合条件的任务">
                    这里只展示服务返回的记录，不把未读取或失败请求当作空队列。
                  </Empty>
                )}
                <div className="pagination">
                  <button
                    className="button"
                    disabled={!page}
                    onClick={() => setPage(page - 1)}
                  >
                    上一页
                  </button>
                  <span>第 {page + 1} 页</span>
                  <button
                    className="button"
                    disabled={!data.has_more}
                    onClick={() => setPage(page + 1)}
                  >
                    下一页
                  </button>
                </div>
              </section>
            )}
          </ResourceView>
        </>
      )}
    </>
  );
}
function TaskDetail({ api, id }: { api: WikiApi; id: string }) {
  const task = useResource("task:" + id, (signal) => api.task(id, signal));
  const [after, setAfter] = useState(0);
  const events = useResource("events:" + id + ":" + after, (signal) =>
    api.taskEvents(id, after, signal),
  );
  return (
    <>
      <a className="back-link" href="#/tasks">
        ← 全部任务
      </a>
      <ResourceView resource={task}>
        {({ data }) => {
          const result = taskResult(data);
          const model = fields(data.result?.model);
          const failure = fields(model.failure);
          return (
            <section className="panel">
              <div className="panel-heading">
                <h2>
                  <Icon name="task" />
                  {data.kind}
                </h2>
                <div className="actions">
                  <Badge tone={taskTone(data.state)}>
                    {taskLabel(data.state)}
                  </Badge>
                  <button
                    className="button"
                    onClick={() => {
                      task.reload();
                      events.reload();
                    }}
                  >
                    刷新状态
                  </button>
                </div>
              </div>
              <div className="task-detail-grid">
                <dl>
                  <Field label="任务 ID">
                    <code>{data.task_id}</code>
                  </Field>
                  <Field label="状态修订">{data.revision}</Field>
                  <Field label="尝试次数">
                    {data.attempt} / {data.max_attempts || "未提供上限"}
                  </Field>
                  <Field label="创建时间">{formatTime(data.created_at)}</Field>
                  <Field label="最后更新">{formatTime(data.updated_at)}</Field>
                  {data.next_run_at && (
                    <Field label="下一次尝试">
                      {formatTime(data.next_run_at)}
                    </Field>
                  )}
                  {data.error?.code && (
                    <Field label="原因类别">
                      <Badge tone="danger">{safeCode(data.error.code)}</Badge>
                    </Field>
                  )}
                  {text(model.status) && (
                    <Field label="模型运行状态">{safeCode(model.status)}</Field>
                  )}
                  {text(failure.stage) && (
                    <Field label="受控失败阶段">
                      {safeCode(failure.stage)}
                    </Field>
                  )}
                  {text(failure.kind) && (
                    <Field label="受控失败类型">{safeCode(failure.kind)}</Field>
                  )}
                </dl>
                <div>
                  <Notice
                    tone={data.state === "succeeded" ? "info" : "warning"}
                    title={taskLabel(data.state)}
                  >
                    {result.label}
                  </Notice>
                  {data.reconciliation_required && (
                    <Notice tone="warning" title="需要核对已有结果">
                      前一次执行的结果尚需对账，不应直接重复产生副作用。
                    </Notice>
                  )}
                  {result.entryId && (
                    <a
                      className="button"
                      href={routeHref(
                        "entry",
                        result.entryId,
                        result.revision
                          ? new URLSearchParams({
                              revision: String(result.revision),
                            })
                          : undefined,
                      )}
                    >
                      打开结果条目 →
                    </a>
                  )}
                  {result.changeId && (
                    <a
                      className="button"
                      href={routeHref("review", result.changeId)}
                    >
                      查看相关提案 →
                    </a>
                  )}
                  <p className="muted">
                    本页不显示租约凭据、原始模型输入输出或服务器错误原文。恢复与取消尚未接入，不能假装已执行。
                  </p>
                  {text(model.status) && (
                    <p className="muted small">
                      诊断类别仅用于定位执行阶段，不代表已核实根因。
                    </p>
                  )}
                </div>
              </div>
            </section>
          );
        }}
      </ResourceView>
      <section className="panel">
        <div className="panel-heading">
          <h2>
            <Icon name="clock" />
            已记录事件
          </h2>
        </div>
        <ResourceView resource={events}>
          {({ data }) =>
            data.length ? (
              <>
                <ol className="timeline">
                  {data.map((event) => (
                    <li key={event.event_id}>
                      <span className="timeline-dot" />
                      <div>
                        <strong>{event.type}</strong>
                        <p>
                          状态修订 {event.aggregate_revision}
                          {typeof event.payload?.state === "string"
                            ? " · " + taskLabel(event.payload.state)
                            : ""}
                        </p>
                        <time>{formatTime(event.occurred_at)}</time>
                      </div>
                    </li>
                  ))}
                </ol>
                <div className="pagination">
                  {after > 0 && (
                    <button className="button" onClick={() => setAfter(0)}>
                      回到起点
                    </button>
                  )}
                  <button
                    className="button"
                    disabled={data.length < PAGE_SIZE}
                    onClick={() => setAfter(data[data.length - 1].sequence)}
                  >
                    后续事件
                  </button>
                </div>
              </>
            ) : (
              <p className="muted">当前范围没有已记录事件。</p>
            )
          }
        </ResourceView>
      </section>
    </>
  );
}

export function InboxPage({ api }: { api: WikiApi }) {
  const localTest = api.getLocalTestSession().status === "authenticated";
  const [page, setPage] = useState(0);
  const [state, setState] = useState("");
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const list = useResource("intakes:" + page + ":" + state, (signal) =>
    api.intakes(page * PAGE_SIZE, state, signal),
  );
  const pending = useRef(false);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const request = useRef<{ url: string; key: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<WikiApiError | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current) return;
    let address: URL;
    try {
      address = new URL(url.trim());
      if (
        !["https:", "http:"].includes(address.protocol) ||
        address.username ||
        address.password
      )
        throw new Error();
    } catch {
      setError(new WikiApiError("INVALID_URL", 422));
      return;
    }
    pending.current = true;
    setBusy(true);
    setError(null);
    const value = address.href;
    if (request.current?.url !== value)
      request.current = { url: value, key: crypto.randomUUID() };
    try {
      const result = await api.submitIntake(
        {
          url: value,
          requested_action: "preview",
          data_classification: "personal",
        },
        request.current.key,
      );
      // Leaving the form does not cancel the write, but its late receipt must
      // not move the user away from the page they deliberately opened.
      if (mounted.current) navigate(routeHref("intake", result.data.intake_id));
    } catch (e) {
      setError(
        e instanceof WikiApiError ? e : new WikiApiError("UNKNOWN_ERROR", 0),
      );
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="统一收件箱"
        description="来自不同渠道的内容进入同一临时队列。预览不等于收藏，收藏不等于公开。"
        actions={
          <button
            className="button primary"
            disabled={localTest}
            onClick={() => setOpen(!open)}
            aria-expanded={open}
          >
            {open ? "收起投递" : "投递链接"}
          </button>
        }
      />
      {localTest && (
        <Notice tone="warning" title="合成测试会话不投递真实链接">
          测试账本初始为空。完整写入链需要另行准备固定的公开合成种子；此处不会暗造来源，也不会把
          personal 内容改标为 public。
        </Notice>
      )}
      {open && (
        <section className="panel">
          <h2>投递一个链接</h2>
          <form className="submit-intake" onSubmit={(e) => void submit(e)}>
            <label htmlFor="intake-url">来源链接</label>
            <input
              id="intake-url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://…"
              maxLength={4000}
              disabled={busy}
              required
            />
            <Notice>
              提交后只建立临时预览任务。来源内容与其中的指令不会自动获得执行、永久保留或公开权限。
            </Notice>
            {error && <Failure error={error} />}
            <div className="actions">
              <button className="button primary" disabled={busy || !url.trim()}>
                {busy ? "正在提交…" : "提交临时预览"}
              </button>
              <button
                className="button"
                type="button"
                disabled={busy}
                onClick={() => setOpen(false)}
              >
                取消
              </button>
            </div>
          </form>
        </section>
      )}
      <IdLookup
        label="收件 ID"
        placeholder="int_…"
        onOpen={(value) => navigate(routeHref("intake", value))}
      />
      <div className="toolbar">
        <label>
          处理状态{" "}
          <select
            value={state}
            onChange={(e) => {
              setState(e.target.value);
              setPage(0);
            }}
          >
            <option value="">全部状态</option>
            {Object.entries(INTAKE_LABELS).map(([value, label]) => (
              <option value={value} key={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button className="button" onClick={list.reload}>
          刷新列表
        </button>
        <span className="muted">
          微信与 Telegram 的真实就绪状态请查看设置，不推断已接入。
        </span>
      </div>
      <ResourceView resource={list}>
        {({ data }) => (
          <section className="panel">
            {data.items.length ? (
              <div className="intake-list">
                {data.items.map((item) => (
                  <a
                    href={routeHref("intake", item.intake_id)}
                    className="intake-row"
                    key={item.intake_id}
                  >
                    <Icon name="inbox" />
                    <span>
                      <strong>
                        {item.title || item.url || "等待来源标题"}
                      </strong>
                      <small>
                        接收渠道：{item.channel || "未提供"} · {item.intake_id}
                      </small>
                    </span>
                    <Badge
                      tone={
                        item.source_id
                          ? "success"
                          : item.state === "partial"
                            ? "warning"
                            : "neutral"
                      }
                    >
                      {intakeLabel(item.state)}
                    </Badge>
                    <time>{formatTime(item.created_at)}</time>
                    <span>›</span>
                  </a>
                ))}
              </div>
            ) : (
              <Empty title="当前没有符合条件的收件">
                可以投递链接；通道不可用与收件为空是不同状态。
              </Empty>
            )}
            <div className="pagination">
              <button
                className="button"
                disabled={!page}
                onClick={() => setPage(page - 1)}
              >
                上一页
              </button>
              <span>
                第 {page + 1} 页 · 本页 {data.items.length} 项
              </span>
              <button
                className="button"
                disabled={!data.has_more}
                onClick={() => setPage(page + 1)}
              >
                下一页
              </button>
            </div>
          </section>
        )}
      </ResourceView>
    </>
  );
}

export function IntakePage({ api, id }: { api: WikiApi; id: string }) {
  const data = useResource("intake:" + id, (signal) => api.intake(id, signal));
  return (
    <>
      <a className="back-link" href="#/inbox">
        ← 返回收件箱
      </a>
      <PageTitle
        title="素材预览与来源详情"
        description="先核对实际取得的内容与缺口，再决定是否长期私有保留。"
        actions={
          <button className="button" onClick={data.reload}>
            刷新预览
          </button>
        }
      />
      <ResourceView resource={data}>
        {({ data: item }) => (
          <IntakeContent
            key={
              item.intake_id + ":" + item.preview_revision + ":" + item.state
            }
            api={api}
            item={item}
            reload={data.reload}
          />
        )}
      </ResourceView>
    </>
  );
}
function IntakeContent({
  api,
  item,
  reload,
}: {
  api: WikiApi;
  item: Intake;
  reload: () => void;
}) {
  const preview = fields(item.preview),
    document = fields(preview.document),
    meta = fields(document.metadata);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const expired =
    !item.source_id &&
    !!item.expires_at &&
    new Date(item.expires_at).getTime() <= now;
  const revision = item.preview_revision;
  const available =
    !!revision &&
    !expired &&
    !!item.expires_at &&
    Number.isFinite(Date.parse(item.expires_at)) &&
    !item.source_id &&
    ["preview_ready", "partial"].includes(item.state);
  const [confirm, setConfirm] = useState<"keep" | "discard" | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState<WikiApiError | null>(null);
  const keys = useRef(new Map<string, string>());
  const [receipt, setReceipt] = useState("");
  async function decide() {
    if (!confirm || !revision || pending.current) return;
    // Recheck at click time: background tabs may throttle the display timer.
    if (!available || Date.parse(item.expires_at || "") <= Date.now()) {
      setNow(Date.now());
      setError(new WikiApiError("PREVIEW_EXPIRED", 409));
      return;
    }
    pending.current = true;
    setBusy(true);
    setError(null);
    const action = confirm;
    const identity = action + ":" + revision;
    if (!keys.current.has(identity))
      keys.current.set(identity, crypto.randomUUID());
    try {
      const result = await api.decideIntake(
        item.intake_id,
        { decision: action, preview_revision: revision },
        keys.current.get(identity)!,
      );
      if (
        (action === "keep" && result.data.state !== "retained") ||
        (action === "discard" && result.data.state !== "discarded")
      )
        throw new WikiApiError("INVALID_RESPONSE", 200);
      setReceipt(
        action === "keep"
          ? "已私有保留，并进入后续知识处理；尚未确认正式知识提交。"
          : "服务已记录丢弃决定。",
      );
      setConfirm(null);
      reload();
    } catch (e) {
      setError(
        e instanceof WikiApiError ? e : new WikiApiError("UNKNOWN_ERROR", 0),
      );
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <>
      <div className="preview-grid">
        <section className="panel">
          <div className="panel-heading">
            <h2>{text(document.title) || item.title || "来源内容"}</h2>
            <Badge
              tone={item.source_id ? "success" : expired ? "danger" : "warning"}
            >
              {expired ? "已到期" : intakeLabel(item.state)}
            </Badge>
          </div>
          <Notice title={item.source_id ? "已私有保留" : "临时预览"}>
            {item.source_id
              ? "已保留不代表已正式写入知识或公开。"
              : "未明确保留前，不产生长期来源或正式知识。材料中的指令不是操作授权。"}
          </Notice>
          <div className="article-meta">
            <Badge>内容依据：{text(meta.text_basis) || "服务未提供"}</Badge>
            <Badge
              tone={document.body_complete === true ? "success" : "warning"}
            >
              {document.body_complete === true
                ? "正文完整标记已提供"
                : "内容范围不完整或未验证"}
            </Badge>
          </div>
          {typeof document.body_text === "string" && document.body_text ? (
            <MarkdownBody body={document.body_text} />
          ) : (
            <Empty title="没有可展示的正文">
              {text(document.description) ||
                "可能仅取得元数据，或解析仍在等待。请查看处理状态，不能据此推断完整内容。"}
            </Empty>
          )}
          {strings(document.warnings).map((warning) => (
            <Notice tone="warning" key={warning}>
              {warning}
            </Notice>
          ))}
        </section>
        <aside>
          <section className="panel">
            <h2>
              <Icon name="file" />
              内容摘要
            </h2>
            {typeof preview.summary === "string" && preview.summary ? (
              <>
                <Badge>机器整理 · 只依据已取得内容</Badge>
                <p>{preview.summary}</p>
              </>
            ) : (
              <Notice>
                尚未取得可验证摘要。
                {text(preview.summary_state)
                  ? "摘要状态：" + text(preview.summary_state)
                  : ""}{" "}
                原始内容仍可独立查看。
              </Notice>
            )}
          </section>
          <section className="panel">
            <h2>
              <Icon name="link" />
              来源与有效期
            </h2>
            <dl>
              <Field label="接收渠道">{item.channel || "未提供"}</Field>
              <Field label="来源平台">
                {text(document.platform) || "未识别"}
              </Field>
              <Field label="预览修订">{revision || "尚未生成"}</Field>
              <Field label="临时到期">{formatTime(item.expires_at)}</Field>
              <Field label="解析状态">{item.parse_status || "尚未确认"}</Field>
            </dl>
            {item.url && (
              <SafeExternal url={item.url}>在原网站查看 ↗</SafeExternal>
            )}
            {item.task_id && (
              <a
                className="button full"
                href={routeHref("tasks", item.task_id)}
              >
                查看处理任务 →
              </a>
            )}
            <p className="muted small">
              不自动加载远程图片或视频。补充材料入口尚未接入，不能假装已取得缺失内容。
            </p>
          </section>
          <section className="panel">
            <h2>保留决定</h2>
            {receipt && <Notice>{receipt}</Notice>}
            {error && <Failure error={error} />}
            <button
              className="button primary full"
              disabled={!available || busy}
              onClick={() => setConfirm("keep")}
            >
              私有保留并整理
            </button>
            <button
              className="button full"
              disabled={!available || busy}
              onClick={() => setConfirm("discard")}
            >
              丢弃临时预览
            </button>
            <a className="button full" href="#/inbox">
              稍后处理
            </a>
            <small className="muted">稍后处理不会自动延长临时有效期。</small>
            {confirm && (
              <div
                className="decision-confirm"
                role="group"
                aria-label="确认保留决定"
              >
                <h3>
                  {confirm === "keep"
                    ? "确认长期私有保留？"
                    : "确认丢弃临时预览？"}
                </h3>
                <p>
                  {confirm === "keep"
                    ? "将保存当前预览修订 " +
                      revision +
                      " 对应的已取得原件，并进入知识处理。不会直接公开。"
                    : "仅处理当前临时收件，不删除既有正式知识。"}
                </p>
                <button
                  className="button primary"
                  disabled={busy || !available}
                  onClick={() => void decide()}
                >
                  {busy ? "提交中…" : "确认本次决定"}
                </button>
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => setConfirm(null)}
                >
                  取消
                </button>
              </div>
            )}
          </section>
        </aside>
      </div>
    </>
  );
}

const RISK_LABELS: Record<string, string> = {
  core_conclusion: "核心结论变化",
  important_merge_delete: "重要合并或删除",
  personal_position: "个人立场变化",
  privacy_exposure: "隐私范围变化",
};
export function ReviewsPage({ api }: { api: WikiApi }) {
  const list = useResource("reviews", (signal) => api.reviews(signal));
  return (
    <>
      <PageTitle
        title="重大决定审核台"
        description="只处理需要本人决定的提案。普通 AI 复核不会被伪装成人工批准。"
        actions={
          <button className="button" onClick={list.reload}>
            刷新待确认
          </button>
        }
      />
      <ResourceView resource={list}>
        {({ data }) => (
          <section className="panel">
            {data.length ? (
              <div className="review-list">
                {data.map((change) => (
                  <a
                    className="review-row"
                    href={routeHref("review", change.change_id)}
                    key={change.change_id}
                  >
                    <Icon name="file" size={25} />
                    <span>
                      <h2>{change.title}</h2>
                      <span className="tags">
                        {change.risk_flags.map((flag) => (
                          <span key={flag}>{RISK_LABELS[flag] || flag}</span>
                        ))}
                      </span>
                      <small>
                        {change.change_id} · {formatTime(change.updated_at)}
                      </small>
                    </span>
                    <Badge tone="warning">待本人决定</Badge>
                    <span>›</span>
                  </a>
                ))}
              </div>
            ) : (
              <Empty title="暂无需要本人确认的提案">
                空队列仅表示本次查询没有匹配项，不代表所有知识都已通过审核。
              </Empty>
            )}
            {data.length === PAGE_SIZE && (
              <Notice>本次最多显示 100 项；当前接口没有进一步分页参数。</Notice>
            )}
          </section>
        )}
      </ResourceView>
    </>
  );
}
export function ReviewPage({ api, id }: { api: WikiApi; id: string }) {
  const resource = useResource<{ change: Change; base: Entry | null }>(
    "review:" + id,
    async (signal) => {
      const change = (await api.change(id, signal)).data;
      const base =
        change.base_revision > 0
          ? (await api.entry(change.entry_id, change.base_revision, signal))
              .data
          : null;
      return { change, base };
    },
  );
  return (
    <>
      <a className="back-link" href="#/reviews">
        ← 返回重要确认
      </a>
      <PageTitle
        title="核对本次知识变更"
        description="阅读完整旧版、新版、元信息与证据后，再决定是否批准这一份具体差异。"
        actions={
          <button className="button" onClick={resource.reload}>
            重新读取差异
          </button>
        }
      />
      <ResourceView resource={resource}>
        {({ change, base }) => (
          <ReviewContent
            key={
              change.change_id + ":" + change.patch_hash + ":" + change.state
            }
            api={api}
            change={change}
            base={base}
            reload={resource.reload}
          />
        )}
      </ResourceView>
    </>
  );
}
function ReviewContent({
  api,
  change,
  base,
  reload,
}: {
  api: WikiApi;
  change: Change;
  base: Entry | null;
  reload: () => void;
}) {
  const [choice, setChoice] = useState<
      "approve" | "reject" | "needs_evidence" | null
    >(null),
    [reason, setReason] = useState(""),
    [checked, setChecked] = useState(false),
    [challenge, setChallenge] = useState<DecisionChallenge | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<WikiApiError | null>(null),
    [result, setResult] = useState<{
      decision: string;
      taskId?: string;
    } | null>(null),
    [now, setNow] = useState(Date.now());
  const pending = useRef(false),
    key = useRef("");
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const reviewable =
    ["needs_user", "needs_ai_review", "approved"].includes(change.state) &&
    ["create", "update", "delete"].includes(change.operation);
  const bound =
    challenge?.change_id === change.change_id &&
    challenge.patch_hash === change.patch_hash &&
    challenge.base_revision === change.base_revision &&
    new Date(challenge.expires_at).getTime() > now;
  async function prepare(decision: "approve" | "reject" | "needs_evidence") {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    setChallenge(null);
    setChecked(false);
    setChoice(decision);
    try {
      const value = (await api.challenge(change.change_id)).data;
      if (
        value.change_id !== change.change_id ||
        value.patch_hash !== change.patch_hash ||
        value.base_revision !== change.base_revision
      )
        throw new WikiApiError("REVIEW_REQUIRED", 409);
      setChallenge(value);
      key.current = crypto.randomUUID();
    } catch (e) {
      setError(
        e instanceof WikiApiError ? e : new WikiApiError("UNKNOWN_ERROR", 0),
      );
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  async function submit() {
    if (
      !choice ||
      !challenge ||
      !bound ||
      !checked ||
      !reason.trim() ||
      pending.current
    )
      return;
    if (Date.parse(challenge.expires_at) <= Date.now()) {
      setNow(Date.now());
      setError(new WikiApiError("NONCE_EXPIRED", 409));
      return;
    }
    pending.current = true;
    setBusy(true);
    setError(null);
    try {
      const receipt = await api.decideChange(
        change.change_id,
        {
          decision: choice,
          reason: reason.trim(),
          expected_patch_hash: change.patch_hash,
          base_revision: change.base_revision,
          decision_nonce: challenge.decision_nonce,
        },
        key.current,
      );
      if (
        receipt.data.decision !== choice ||
        receipt.data.change_id !== change.change_id
      )
        throw new WikiApiError("INVALID_RESPONSE", 200);
      setResult({
        decision: choice,
        taskId: receipt.task_id || text(receipt.data.task_id) || undefined,
      });
      setChoice(null);
      setChallenge(null);
      setChecked(false);
    } catch (e) {
      setError(
        e instanceof WikiApiError ? e : new WikiApiError("UNKNOWN_ERROR", 0),
      );
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  const metadata = {
    operation: change.operation,
    entry_type: change.entry_type,
    publication_intent: change.publication_intent,
    publication_eligible: change.publication_eligible,
    visibility: change.visibility,
    metadata: change.metadata,
    evidence_refs: change.evidence_refs,
  };
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>{change.title}</h2>
        <Badge tone="warning">{change.state}</Badge>
      </div>
      <div className="tags">
        {change.risk_flags.map((flag) => (
          <span key={flag}>{RISK_LABELS[flag] || flag}</span>
        ))}
      </div>
      <dl className="compact-fields">
        <Field label="提案 ID">
          <code>{change.change_id}</code>
        </Field>
        <Field label="基础修订">{change.base_revision}</Field>
        <Field label="绑定差异">
          <code>{change.patch_hash}</code>
        </Field>
      </dl>
      <Notice tone="warning">
        批准只对应当前差异；基础、策略或证据变化后须重新查看。有效期来自实时挑战，不使用设计图中的示例“7
        天”。
      </Notice>
      <div className="diff-grid">
        <section>
          <h3>旧版 {base ? "v" + base.revision : "（新条目，无旧版）"}</h3>
          <h4>{base?.title || "尚不存在"}</h4>
          <MarkdownBody body={base?.body || ""} />
          <h4>旧版元信息</h4>
          <pre className="metadata">
            {JSON.stringify(
              base
                ? {
                    entry_type: base.entry_type,
                    publication_intent: base.publication_intent,
                    publication_eligible: base.publication_eligible,
                    visibility: base.visibility,
                    metadata: base.metadata,
                    evidence_refs: base.evidence_refs,
                  }
                : {},
              null,
              2,
            )}
          </pre>
        </section>
        <section>
          <h3>候选新版</h3>
          <h4>{change.title}</h4>
          <MarkdownBody body={change.body} />
          <h4>候选元信息与证据引用</h4>
          <pre className="metadata">{JSON.stringify(metadata, null, 2)}</pre>
        </section>
      </div>
      <Notice>
        这是正式提案中已保存的内容，不是 AI
        临时生成的确认摘要。来源正文尚未在本页接入；证据不足时请选择“要求补证据”，不要默认已核验。
      </Notice>
      {error && <Failure error={error} />}{" "}
      {result ? (
        <Notice title="服务已记录决定">
          {result.decision === "approve"
            ? "批准已提交，后续 Writer 与索引状态需要单独核对。"
            : "决定已记录，不代表执行了知识修改。"}
          {result.taskId && (
            <a className="button" href={routeHref("tasks", result.taskId)}>
              查看后续任务 →
            </a>
          )}
          <button className="button" onClick={reload}>
            读取最新提案状态
          </button>
        </Notice>
      ) : (
        <>
          <div className="actions review-actions">
            <button
              className="button primary"
              disabled={!reviewable || busy}
              onClick={() => void prepare("approve")}
            >
              准备批准本次差异
            </button>
            <button
              className="button"
              disabled={!reviewable || busy}
              onClick={() => void prepare("reject")}
            >
              拒绝
            </button>
            <button
              className="button"
              disabled={!reviewable || busy}
              onClick={() => void prepare("needs_evidence")}
            >
              要求补证据
            </button>
            <a className="button" href="#/reviews">
              稍后
            </a>
          </div>
          {!reviewable && (
            <p className="muted">
              当前状态不能在此作出决定，请刷新或查看相关任务。
            </p>
          )}
        </>
      )}
      {choice && (
        <section
          className="decision-confirm"
          role="group"
          aria-label="确认具体差异"
        >
          <h3>
            {choice === "approve"
              ? "确认批准当前差异"
              : choice === "reject"
                ? "确认拒绝当前提案"
                : "确认要求补充证据"}
          </h3>
          {busy && !challenge ? (
            <p role="status">正在获取当前差异的确认凭据…</p>
          ) : challenge ? (
            <p>
              确认有效期：{formatTime(challenge.expires_at)}
              {!bound ? " · 已失效，请取消并重新读取差异。" : ""}
            </p>
          ) : (
            <p>未获取有效确认凭据，不能提交。</p>
          )}
          <label htmlFor="review-reason">决定说明</label>
          <textarea
            id="review-reason"
            rows={3}
            value={reason}
            maxLength={2000}
            onChange={(e) => setReason(e.target.value)}
            disabled={busy}
          />
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => setChecked(e.target.checked)}
              disabled={busy || !bound}
            />
            我已查看上方完整正文、元信息与证据引用，确认只针对当前差异。
          </label>
          <div className="actions">
            <button
              className="button primary"
              disabled={busy || !bound || !checked || !reason.trim()}
              onClick={() => void submit()}
            >
              {busy ? "正在提交…" : "提交这次决定"}
            </button>
            <button
              className="button"
              disabled={busy}
              onClick={() => {
                setChoice(null);
                setChallenge(null);
                setChecked(false);
              }}
            >
              取消，不作决定
            </button>
          </div>
        </section>
      )}
    </section>
  );
}
export function SettingsPage({ api }: { api: WikiApi }) {
  const health = useResource("health", (signal) => api.health(signal)),
    capabilities = useResource("capabilities", (signal) =>
      api.capabilities(signal),
    );
  return (
    <>
      <PageTitle
        title="设置与运行状态"
        description="只读核对服务、渠道与权限边界。不在浏览器保存凭据，不自动启用付费回退。"
        actions={
          <button
            className="button"
            onClick={() => {
              health.reload();
              capabilities.reload();
            }}
          >
            重新检查
          </button>
        }
      />
      <div className="settings-grid">
        <section className="panel">
          <h2>
            <Icon name="settings" />
            统一服务
          </h2>
          <ResourceView resource={health}>
            {(data) => (
              <>
                <dl>
                  <Field label="服务状态">
                    <Badge
                      tone={data.status === "ready" ? "success" : "warning"}
                    >
                      {data.status}
                    </Badge>
                  </Field>
                  <Field label="后台任务处理">
                    {data.worker_enabled ? "已启用" : "未启用"}
                  </Field>
                  <Field label="渠道处理">
                    {data.channel_worker_enabled ? "已启用" : "未启用"} ·{" "}
                    {data.channel_status}
                  </Field>
                  <Field label="自动提交开关">
                    {data.automatic_commit_enabled
                      ? "已启用（仍受门禁约束）"
                      : "未启用"}
                  </Field>
                  <Field label="发布服务">
                    {data.publication_enabled
                      ? "服务报告已启用"
                      : "服务未启用自动发布"}
                  </Field>
                </dl>
                <Notice>
                  健康检查成功不等于浏览器已登录、渠道已通过真实验证或内容已发布。
                </Notice>
              </>
            )}
          </ResourceView>
        </section>
        <section className="panel">
          <h2>
            <Icon name="lock" />
            访问边界
          </h2>
          <dl>
            <Field label="身份来源">同源服务端已认证会话</Field>
            <Field label="浏览器凭据存储">
              不持久化 bearer；测试证明仅在内存，会话 cookie 由服务端管理。
            </Field>
            <Field label="模型回退">不在前端启用付费 API</Field>
            <Field label="模型运行结果">本页没有实时提供者诊断接口</Field>
          </dl>
          <Notice tone="warning">
            认证桥未配置时显示 401，不能以健康状态、示例数据或假身份绕过。
          </Notice>
        </section>
      </div>
      <section className="panel">
        <h2>来源解析能力</h2>
        <ResourceView resource={capabilities}>
          {({ data }) =>
            data.length ? (
              <div className="capability-table">
                <div className="capability-header">
                  <span>平台 / 适配器</span>
                  <span>正文</span>
                  <span>字幕</span>
                  <span>视频</span>
                  <span>真实验证</span>
                </div>
                {data.map((item, index) => (
                  <div
                    className="capability-line"
                    key={text(item.adapter) + index}
                  >
                    <strong>
                      {text(item.platform) || text(item.adapter) || "未命名"}
                    </strong>
                    <span>{text(item.body_text) || "未提供"}</span>
                    <span>{text(item.captions) || "未提供"}</span>
                    <span>{text(item.video) || "未提供"}</span>
                    <span>{text(item.verification) || "未验证"}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">服务没有返回解析能力记录。</p>
            )
          }
        </ResourceView>
        <p className="muted small">
          解析器支持某种内容，不代表微信/Telegram
          账号已接入，也不代表这次媒体获取完整。
        </p>
      </section>
      <section className="panel">
        <h2>尚未接入的管理能力</h2>
        <p className="muted">
          模型账号、渠道授权、OpenBao、Git
          同步和备份状态尚无此页面可核实的完整接口。这里不显示虚构“已连接”或可自动修复按钮。
        </p>
        <Notice>
          公开意图、资格通过、产物已构建、已部署是不同状态。只有相应服务的证据才能确认。
        </Notice>
      </section>
    </>
  );
}
export function ToolsPage() {
  return (
    <>
      <PageTitle
        title="工具与 Skill"
        description="维护者审核后的受限能力目录。"
        actions={<Badge tone="warning">后续规划 · 尚未实现</Badge>}
      />
      <section className="panel">
        <Notice tone="warning" title="本页仅说明规划与权限边界">
          完整注册、安装、启用和升级管理尚未实现。不会因收录说明而获得执行权限。
        </Notice>
        <h2>现有固定接口与未来注册中心分开</h2>
        <p>
          知识检索、来源预览与任务查询使用已注册的受限领域接口。未来的工具目录需要逐项来源、版本、许可证、输入输出、权限与验证证据。
        </p>
        <ul className="plain-list">
          <li>不开放通用 Shell、任意网络或直接写库。</li>
          <li>写入、公开与权限变更仍遵守独立确认和后端门禁。</li>
          <li>未审查的材料不能自动变成可执行 Skill。</li>
        </ul>
        <a className="button" href="#/settings">
          查看当前连接状态
        </a>
      </section>
    </>
  );
}
