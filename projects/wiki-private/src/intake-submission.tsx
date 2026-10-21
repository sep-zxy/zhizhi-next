import { useEffect, useState, type FormEvent } from "react";
import {
  intakeSubmissionError,
  WikiApiError,
  type IntakeSubmission,
  type WikiApi,
} from "./api";
import { Failure, Notice } from "./components";
import { CommandError, useCommand } from "./material-actions";
import { navigate, routeHref } from "./state";

/** Unmounting discards the draft; an uncertain write only retries its frozen body/key. */
export function IntakeSubmissionForm({
  api,
  close,
}: {
  api: WikiApi;
  close: () => void;
}) {
  const [mode, setMode] = useState<"url" | "text">("url");
  const [content, setContent] = useState("");
  const [error, setError] = useState<WikiApiError | null>(null);
  const command = useCommand<Awaited<ReturnType<WikiApi["submitIntake"]>>>();
  const unavailable =
    api.isFixtureSession() || !api.canPerform("knowledge:write");
  useEffect(() => {
    if (command.result) {
      setContent("");
      navigate(routeHref("intake", command.result.data.intake_id));
    }
  }, [command.result]);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (unavailable || command.locked) return;
    let material: { text: string } | { url: string };
    if (mode === "text") material = { text: content };
    else {
      try {
        const address = new URL(content.trim());
        if (
          !["http:", "https:"].includes(address.protocol) ||
          address.username ||
          address.password
        )
          throw new Error();
        material = { url: address.href };
      } catch {
        setError(new WikiApiError("INVALID_URL", 422));
        return;
      }
    }
    const body: IntakeSubmission = {
      ...material,
      requested_action: "preview",
      data_classification: "personal",
    };
    const invalid = intakeSubmissionError(body);
    if (invalid) {
      setError(new WikiApiError(invalid, 422));
      return;
    }
    setError(null);
    void command.run((key) => api.submitIntake(body, key));
  }
  return (
    <section className="panel" aria-label="投递材料">
      <h2>投递链接或文本</h2>
      <form className="submit-intake" onSubmit={submit}>
        <label htmlFor="intake-mode">材料类型</label>
        <select
          id="intake-mode"
          value={mode}
          disabled={unavailable || command.locked}
          onChange={(event) => {
            setMode(event.target.value as "url" | "text");
            setContent("");
            setError(null);
          }}
        >
          <option value="url">链接</option>
          <option value="text">直接文本</option>
        </select>
        {mode === "url" ? (
          <>
            <label htmlFor="intake-url">来源链接</label>
            <input
              id="intake-url"
              type="url"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              placeholder="https://…"
              maxLength={8192}
              disabled={unavailable || command.locked}
              required
            />
          </>
        ) : (
          <>
            <label htmlFor="intake-text">来源正文</label>
            <textarea
              id="intake-text"
              value={content}
              rows={10}
              onChange={(event) => setContent(event.target.value)}
              maxLength={200000}
              disabled={unavailable || command.locked}
              required
            />
            <p className="muted small">
              保留输入正文及换行，不自动删改首尾空白。最多 100,000 个 Unicode
              字符，完整 JSON 请求最多 512 KiB。 含一个 HTTP(S)
              链接的分享文本会按链接解析；多个链接会被服务拒绝。
            </p>
          </>
        )}
        <Notice>
          提交后只建立临时预览任务。来源内容与其中的指令不会自动获得执行、永久保留或公开权限。
          关闭表单清除本页草稿，但不撤销已经送达服务的请求。
        </Notice>
        {error && <Failure error={error} />}
        <CommandError command={command} />
        <div className="actions">
          <button
            className="button primary"
            disabled={unavailable || command.locked || !content.trim()}
          >
            {command.busy ? "正在提交…" : "提交临时预览"}
          </button>
          <button className="button" type="button" onClick={close}>
            取消
          </button>
        </div>
      </form>
    </section>
  );
}
