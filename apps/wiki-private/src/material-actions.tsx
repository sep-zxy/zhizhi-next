import { useEffect, useRef, useState } from "react";
import { WikiApiError, type WikiApi } from "./api";
import {
  Badge,
  Failure,
  MarkdownBody,
  Notice,
  ResourceView,
} from "./components";
import {
  fields,
  text,
  type Entry,
  type EvidenceRef,
  type Intake,
  type Task,
} from "./contracts";
import { routeHref, taskLabel, useResource } from "./state";

/** One immutable request per mounted form; uncertain outcomes retry the same body/key. */
export function useCommand<T>() {
  const mounted = useRef(true),
    pending = useRef(false);
  const attempt = useRef<{
    key: string;
    send: (key: string) => Promise<T>;
  } | null>(null);
  const [busy, setBusy] = useState(false),
    [locked, setLocked] = useState(false);
  const [error, setError] = useState<WikiApiError | null>(null),
    [result, setResult] = useState<T | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      attempt.current = null;
    };
  }, []);
  async function run(send?: (key: string) => Promise<T>) {
    if (pending.current || result || !mounted.current) return;
    if (!attempt.current && send)
      attempt.current = { key: crypto.randomUUID(), send };
    if (!attempt.current) return;
    pending.current = true;
    setBusy(true);
    setLocked(true);
    setError(null);
    try {
      const value = await attempt.current.send(attempt.current.key);
      attempt.current = null;
      if (mounted.current) setResult(value);
    } catch (e) {
      if (mounted.current)
        setError(
          e instanceof WikiApiError ? e : new WikiApiError("NETWORK_ERROR", 0),
        );
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return { busy, locked, error, result, run };
}
export function CommandError({
  command,
}: {
  command: {
    error: WikiApiError | null;
    busy: boolean;
    run: () => Promise<void>;
  };
}) {
  if (!command.error) return null;
  const retryable =
    command.error.status === 0 ||
    command.error.code === "INVALID_RESPONSE" ||
    command.error.status >= 500;
  return (
    <>
      <Failure error={command.error} />
      {retryable ? (
        <>
          <p className="muted">
            结果未确认。表单已冻结；重试复用同一请求，不会换键重复提交。
          </p>
          <button
            className="button"
            disabled={command.busy}
            onClick={() => void command.run()}
          >
            重试同一请求
          </button>
        </>
      ) : (
        <p className="muted">
          本次请求未完成。请刷新读取当前版本，核对后重新操作。
        </p>
      )}
    </>
  );
}

export function TaskControls({
  api,
  task,
  reload,
}: {
  api: WikiApi;
  task: Task;
  reload: () => void;
}) {
  const [action, setAction] = useState<"resume" | "cancel" | null>(null);
  const command = useCommand<Awaited<ReturnType<WikiApi["controlTask"]>>>();
  const waiting = [
    "waiting_auth",
    "waiting_quota",
    "waiting_user",
    "waiting_review",
    "waiting_input",
    "blocked",
    "partial",
    "retry_wait",
  ].includes(task.state);
  if (!api.canPerform("knowledge:write"))
    return <Notice>当前 Owner 会话只有读取权限，不能恢复或取消任务。</Notice>;
  if (task.reconciliation_required || (!waiting && task.state !== "queued"))
    return (
      <p className="muted small">
        运行中、终态或待对账任务不允许强行恢复/取消，现有结果与工作租约不会被覆盖。
      </p>
    );
  return (
    <section className="action-section" aria-label="任务操作">
      <h3>处理等待与取消</h3>
      <p>
        只针对状态修订 {task.revision}
        。恢复只重新排队，不代表授权、额度或执行问题已经解决。取消不撤销已保存的来源或知识。
      </p>
      {!action && !command.locked && (
        <div className="actions">
          {waiting && (
            <button className="button" onClick={() => setAction("resume")}>
              恢复任务
            </button>
          )}
          <button className="button" onClick={() => setAction("cancel")}>
            取消任务
          </button>
        </div>
      )}
      {action && !command.result && (
        <div className="decision-confirm">
          <p>
            确认{action === "resume" ? "重新排队" : "取消尚未运行的任务"}
            ？当前状态：{taskLabel(task.state)}。
          </p>
          <button
            className="button primary"
            disabled={command.locked}
            onClick={() =>
              void command.run((key) =>
                api.controlTask(task.task_id, action, task.revision, key),
              )
            }
          >
            {command.busy ? "提交中…" : "确认任务操作"}
          </button>
          <button
            className="button"
            disabled={command.locked}
            onClick={() => setAction(null)}
          >
            返回
          </button>
        </div>
      )}
      <CommandError command={command} />
      {command.result && (
        <Notice title="已取得服务回执">
          {command.result.data.state === "queued"
            ? "已重新排队，尚未完成。"
            : "已取消此任务，已有资料未被撤销。"}
          <button className="button" onClick={reload}>
            读取最新任务状态
          </button>
        </Notice>
      )}
    </section>
  );
}

export function EvidenceReader({
  api,
  reference,
}: {
  api: WikiApi;
  reference: EvidenceRef;
}) {
  const [open, setOpen] = useState(false);
  const id =
    reference.source_id ||
    reference.external_document_id ||
    reference.learning_outcome_id;
  return (
    <section className="evidence-reader">
      <h3>
        {reference.source_id
          ? "私有来源文本"
          : reference.external_document_id
            ? "外部正文证据"
            : "本人学习原话"}
      </h3>
      <code>{id || "引用信息不完整"}</code>
      {reference.source_revision !== undefined && (
        <p>来源修订：{reference.source_revision}</p>
      )}
      {reference.content_hash && (
        <p className="small">
          内容哈希：<code>{reference.content_hash}</code>
        </p>
      )}
      {reference.quote && (
        <p>条目引用片段（不等于完整来源）：{reference.quote}</p>
      )}
      <button
        className="button"
        disabled={!id}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "关闭来源文本" : "读取已授权来源文本"}
      </button>
      {open && <EvidenceContent api={api} reference={reference} />}
    </section>
  );
}
function EvidenceContent({
  api,
  reference,
}: {
  api: WikiApi;
  reference: EvidenceRef;
}) {
  const resource = useResource(
    "evidence:" + JSON.stringify(reference),
    (signal) => api.evidence(reference, signal),
  );
  return (
    <ResourceView resource={resource}>
      {({ data }) => (
        <>
          <Badge>{data.text_basis}</Badge>
          <Notice>
            这里只展示服务验证过的来源文本，不是下载整个 RAW
            包。附件、原始媒体与私人附言不混入正文；完整度：
            {data.body_complete === true ? "来源标记完整" : "不完整或未验证"}。
          </Notice>
          {data.text ? (
            <MarkdownBody body={data.text} />
          ) : (
            <p>没有可读取的文本。不会从标题、摘要或示例补造原文。</p>
          )}
        </>
      )}
    </ResourceView>
  );
}

export function EntryProposal({
  api,
  entry,
  historical,
}: {
  api: WikiApi;
  entry: Entry;
  historical: boolean;
}) {
  const [operation, setOperation] = useState<"update" | "delete" | null>(null);
  const [title, setTitle] = useState(entry.title),
    [body, setBody] = useState(entry.body),
    [confirmed, setConfirmed] = useState(false);
  const command = useCommand<Awaited<ReturnType<WikiApi["proposeChange"]>>>();
  const fixture = api.isFixtureSession();
  const unavailable =
    historical ||
    entry.deleted ||
    !entry.evidence_refs.length ||
    fixture ||
    !api.canPerform("knowledge:write");
  function submit() {
    if (!operation || !confirmed || unavailable) return;
    const payload = {
      entry_id: entry.entry_id,
      base_revision: entry.revision,
      operation,
      title: operation === "delete" ? entry.title : title,
      body: operation === "delete" ? entry.body : body,
      evidence_refs: entry.evidence_refs,
      ...(entry.publication_intent
        ? { publication_intent: entry.publication_intent }
        : {}),
    };
    void command.run((key) => api.proposeChange(payload, key));
  }
  return (
    <section className="action-section" aria-label="版本化修改提案">
      <p>
        只创建提案，须经独立复核和 Writer
        才能更新正式知识。删除是可追溯的删除提案，不擦除历史，不直接删除 RAW。
      </p>
      {unavailable && (
        <Notice>
          {fixture
            ? "本机合成验收会话不接收真实知识修改。"
            : !api.canPerform("knowledge:write")
              ? "当前 Owner 会话没有写入提案权限。"
              : historical
                ? "请先返回当前版本再提出修改。"
                : entry.deleted
                  ? "此版本已标记删除。"
                  : "当前条目没有可验证证据，暂不能创建提案。"}
        </Notice>
      )}
      {!operation && !command.locked && (
        <div className="actions">
          <button
            className="button"
            disabled={unavailable}
            onClick={() => setOperation("update")}
          >
            提出修改
          </button>
          <button
            className="button"
            disabled={unavailable}
            onClick={() => setOperation("delete")}
          >
            提出删除
          </button>
        </div>
      )}
      {operation && !command.result && (
        <div className="proposal-editor">
          <h3>
            {operation === "delete" ? "删除提案确认" : "对照修改内容"} · 基础 v
            {entry.revision}
          </h3>
          <div className="version-compare">
            <section>
              <h4>正式基础版本</h4>
              <strong>{entry.title}</strong>
              <MarkdownBody body={entry.body} />
            </section>
            <section>
              <h4>{operation === "delete" ? "提案效果" : "候选版本"}</h4>
              {operation === "delete" ? (
                <Notice tone="warning">
                  提案经复核并由 Writer 接受后，条目标记删除。历史仍保留。
                </Notice>
              ) : (
                <>
                  <label>
                    候选标题
                    <input
                      value={title}
                      maxLength={500}
                      disabled={command.locked}
                      onChange={(e) => {
                        setTitle(e.target.value);
                        setConfirmed(false);
                      }}
                    />
                  </label>
                  <label>
                    候选正文
                    <textarea
                      value={body}
                      maxLength={1000000}
                      disabled={command.locked}
                      rows={14}
                      onChange={(e) => {
                        setBody(e.target.value);
                        setConfirmed(false);
                      }}
                    />
                  </label>
                </>
              )}
            </section>
          </div>
          <label className="confirmation-label">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={command.locked}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            我已核对基础与候选内容，保留原证据和公开意图，仅提交提案。
          </label>
          <button
            className="button primary"
            disabled={
              unavailable || command.locked || !confirmed || !title.trim()
            }
            onClick={submit}
          >
            {command.busy ? "提交中…" : "提交版本化提案"}
          </button>
          <button
            className="button"
            disabled={command.locked}
            onClick={() => {
              setOperation(null);
              setConfirmed(false);
            }}
          >
            关闭编辑
          </button>
        </div>
      )}
      <CommandError command={command} />
      {command.result && (
        <Notice
          title={
            command.result.data.state === "no_change"
              ? "未产生内容变化"
              : "提案已创建，正式知识未改写"
          }
        >
          <a
            className="button"
            href={routeHref("review", command.result.data.change_id)}
          >
            查看提案与复核
          </a>
          {command.result.task_id && (
            <a
              className="button"
              href={routeHref("tasks", command.result.task_id)}
            >
              查看处理任务
            </a>
          )}
        </Notice>
      )}
      <p className="muted small">
        条目合并尚无安全的领域契约，本页不把替换正文伪装成合并。
      </p>
    </section>
  );
}

export function SupplementForm({
  api,
  item,
  reload,
}: {
  api: WikiApi;
  item: Intake;
  reload: () => void;
}) {
  const [open, setOpen] = useState(false),
    [body, setBody] = useState(""),
    [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false),
    [expiredAtClick, setExpiredAtClick] = useState(false);
  const command =
    useCommand<Awaited<ReturnType<WikiApi["supplementIntake"]>>>();
  const fixture = api.isFixtureSession();
  const expires = Date.parse(item.expires_at || "");
  const unavailable =
    fixture ||
    !api.canPerform("knowledge:write") ||
    !!item.source_id ||
    ["retained", "kept", "discarded", "expired"].includes(item.state) ||
    !Number.isFinite(expires) ||
    expires <= Date.now() ||
    expiredAtClick ||
    item.preview_revision === undefined;
  const validSize =
    body.length <= 100000 &&
    new TextEncoder().encode(body).length <= 300000 &&
    note.length <= 10000 &&
    new TextEncoder().encode(note).length <= 30000;
  function submit() {
    if (Date.parse(item.expires_at || "") <= Date.now()) {
      setExpiredAtClick(true);
      return;
    }
    if (unavailable || !confirmed || !body.trim() || !validSize) return;
    const payload = {
      expected_preview_revision: item.preview_revision!,
      text: body,
      private_note: note,
    };
    void command.run((key) =>
      api.supplementIntake(item.intake_id, payload, key),
    );
  }
  return (
    <section className="panel" aria-label="补充来源材料">
      <h2>补充来源材料</h2>
      <p>
        粘贴可核对的原文/字幕，并单独记录私人附言。不访问新增链接、不调用模型、不自动长期保留。新的预览会使旧保留确认失效。
      </p>
      <Notice>
        支持粘贴文本，或在下方上传有界 UTF-8 文本文件。截图、音视频、PDF
        与归档仍不支持，不会假装上传成功。
      </Notice>
      {fixture && <Notice>本机合成验收会话不接收真实材料。</Notice>}
      {!api.canPerform("knowledge:write") && (
        <Notice>当前 Owner 会话没有材料写入权限。</Notice>
      )}
      {expiredAtClick && (
        <Notice tone="warning">临时预览已到期，这次补料未发送。</Notice>
      )}
      {!open && !command.locked && (
        <button
          className="button"
          disabled={unavailable}
          onClick={() => setOpen(true)}
        >
          粘贴补充文本
        </button>
      )}
      {open && !command.result && (
        <div className="supplement-editor">
          <label>
            补充来源正文
            <textarea
              rows={9}
              maxLength={100000}
              value={body}
              disabled={command.locked}
              onChange={(e) => {
                setBody(e.target.value);
                setConfirmed(false);
              }}
            />
          </label>
          <label>
            私人附言（不作为来源正文）
            <textarea
              rows={4}
              maxLength={10000}
              value={note}
              disabled={command.locked}
              onChange={(e) => {
                setNote(e.target.value);
                setConfirmed(false);
              }}
            />
          </label>
          <p className="muted">
            正文上限 100,000 字符/300 KB，附言上限 10,000 字符/30
            KB。每条最多补充 20
            次，不延长原有效期。将保留此前文本，当前阅读副本改为本次正文。
          </p>
          <label className="confirmation-label">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={command.locked}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            我已核对两栏内容，补充到预览修订 {item.preview_revision}
            ，不把附言当作来源事实。
          </label>
          <button
            className="button primary"
            disabled={
              unavailable ||
              command.locked ||
              !confirmed ||
              !body.trim() ||
              !validSize
            }
            onClick={submit}
          >
            {command.busy ? "提交中…" : "提交本次补料"}
          </button>
          <button
            className="button"
            disabled={command.locked}
            onClick={() => setOpen(false)}
          >
            返回预览
          </button>
        </div>
      )}
      <CommandError command={command} />
      {command.result && (
        <Notice title="补料已写入临时预览，尚未保留">
          新预览修订 {command.result.data.preview_revision}
          。尚未运行的旧解析任务已取消，正文仍标记未验证完整度。
          <button className="button" onClick={reload}>
            查看新预览后再决定
          </button>
        </Notice>
      )}
    </section>
  );
}
export function SupplementHistory({ item }: { item: Intake }) {
  const preview = fields(item.preview);
  if (preview.manual_supplement !== true) return null;
  const original =
    text(fields(preview.original_document).body_text) ||
    text(preview.original_text);
  const supplements = Array.isArray(preview.supplements)
    ? preview.supplements.map(fields)
    : [];
  return (
    <section className="panel">
      <h2>原材料与补充记录</h2>
      {original && (
        <details>
          <summary>补料前的来源文本</summary>
          <MarkdownBody body={original} />
        </details>
      )}
      {supplements.map((item, index) => (
        <details key={index}>
          <summary>
            补充 {index + 1} · 预览修订{" "}
            {String(item.preview_revision || "未知")}
          </summary>
          {text(fields(item.attachment).filename) && (
            <p className="muted">
              原文件：{text(fields(item.attachment).filename)} ·{" "}
              {text(fields(item.attachment).content_type)}
              {" · "}
              {String(fields(item.attachment).byte_count)} 字节
              <br />
              SHA-256：{text(fields(item.attachment).sha256)}
            </p>
          )}
          <h3>来源正文</h3>
          <MarkdownBody body={text(item.text)} />
          <h3>私人附言 · 不属于来源事实</h3>
          <p className="private-note">{text(item.private_note) || "无附言"}</p>
        </details>
      ))}
    </section>
  );
}
