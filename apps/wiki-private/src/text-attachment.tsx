import { useEffect, useRef, useState } from "react";
import { WikiApiError, type WikiApi, type TextAttachmentUpload } from "./api";
import { Failure, MarkdownBody, Notice } from "./components";
import { type Intake } from "./contracts";
import { CommandError, useCommand } from "./material-actions";

type SelectedFile = Pick<
  TextAttachmentUpload,
  "filename" | "content_type" | "content_base64"
> & { text: string; size: number };
function encodeBytes(bytes: Uint8Array): string {
  let binary = "";
  for (let start = 0; start < bytes.length; start += 8192)
    binary += String.fromCharCode(...bytes.subarray(start, start + 8192));
  return btoa(binary);
}

export function TextAttachmentForm({
  api,
  item,
  reload,
}: {
  api: WikiApi;
  item: Intake;
  reload: () => void;
}) {
  const input = useRef<HTMLInputElement | null>(null),
    reader = useRef<FileReader | null>(null),
    generation = useRef(0);
  const [selected, setSelected] = useState<SelectedFile | null>(null),
    [note, setNote] = useState("");
  const [reading, setReading] = useState(false),
    [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<WikiApiError | null>(null);
  const command =
    useCommand<Awaited<ReturnType<WikiApi["uploadTextAttachment"]>>>();
  const fixture = api.isFixtureSession();
  const expires = Date.parse(item.expires_at || "");
  const unavailable =
    fixture ||
    !api.canPerform("knowledge:write") ||
    !!item.source_id ||
    ["retained", "kept", "discarded", "expired"].includes(item.state) ||
    !Number.isFinite(expires) ||
    expires <= Date.now() ||
    item.preview_revision === undefined;
  function clear() {
    generation.current++;
    reader.current?.abort();
    reader.current = null;
    if (input.current) input.current.value = "";
    setSelected(null);
    setNote("");
    setConfirmed(false);
    setReading(false);
    setError(null);
  }
  useEffect(
    () => () => {
      generation.current++;
      reader.current?.abort();
      reader.current = null;
    },
    [],
  );
  useEffect(() => {
    if (command.result) clear();
  }, [command.result]);
  function choose(file?: File) {
    clear();
    if (!file || unavailable || command.locked) return;
    if (file.size < 1 || file.size > 100000) {
      setError(new WikiApiError("ATTACHMENT_TOO_LARGE", 422));
      return;
    }
    const extension = file.name.split(".").at(-1)?.toLowerCase();
    const mime =
      file.type || (extension === "md" ? "text/markdown" : "text/plain");
    if (
      !["txt", "md"].includes(extension || "") ||
      !["text/plain", "text/markdown"].includes(mime) ||
      (extension === "txt" && mime !== "text/plain")
    ) {
      setError(new WikiApiError("ATTACHMENT_UNSUPPORTED", 422));
      return;
    }
    const epoch = generation.current,
      current = new FileReader();
    reader.current = current;
    setReading(true);
    current.onload = () => {
      if (epoch !== generation.current) return;
      try {
        if (!current.result || typeof current.result === "string")
          throw new WikiApiError("ATTACHMENT_READ_FAILED", 422);
        const bytes = new Uint8Array(current.result);
        if (bytes.length !== file.size)
          throw new WikiApiError("ATTACHMENT_READ_FAILED", 422);
        let text: string;
        try {
          text = new TextDecoder("utf-8", {
            fatal: true,
            ignoreBOM: true,
          }).decode(bytes);
        } catch {
          throw new WikiApiError("ATTACHMENT_INVALID_UTF8", 422);
        }
        if (!text.replace(/^\uFEFF/, "").trim())
          throw new WikiApiError("ATTACHMENT_INVALID", 422);
        if (
          bytes.some(
            (byte) =>
              (byte < 32 && ![9, 10, 13].includes(byte)) || byte === 127,
          )
        )
          throw new WikiApiError("ATTACHMENT_UNSUPPORTED", 422);
        setSelected({
          filename: file.name,
          content_type: mime as SelectedFile["content_type"],
          content_base64: encodeBytes(bytes),
          text,
          size: bytes.length,
        });
      } catch (e) {
        setError(
          e instanceof WikiApiError
            ? e
            : new WikiApiError("ATTACHMENT_READ_FAILED", 422),
        );
      } finally {
        setReading(false);
        reader.current = null;
      }
    };
    current.onerror = () => {
      if (epoch === generation.current) {
        setError(new WikiApiError("ATTACHMENT_READ_FAILED", 422));
        setReading(false);
        reader.current = null;
      }
    };
    current.readAsArrayBuffer(file);
  }
  function upload() {
    if (!selected || !confirmed || command.locked) return;
    if (!Number.isFinite(expires) || expires <= Date.now()) {
      clear();
      setError(new WikiApiError("PREVIEW_EXPIRED", 409));
      return;
    }
    if (
      unavailable ||
      note.length > 10000 ||
      new TextEncoder().encode(note).length > 30000
    )
      return;
    const payload: TextAttachmentUpload = {
      expected_preview_revision: item.preview_revision!,
      filename: selected.filename,
      content_type: selected.content_type,
      content_base64: selected.content_base64,
      private_note: note,
    };
    void command.run((key) =>
      api.uploadTextAttachment(item.intake_id, payload, key),
    );
  }
  return (
    <section className="panel" aria-label="上传文本文件">
      <h2>上传文本文件</h2>
      <p>
        只接收 UTF-8 的 .txt/.md 原始文件字节，最多 100,000
        字节。仅补充当前临时预览，不自动保留、不延长有效期、不运行文件。图片、音视频、PDF
        和归档仍不支持。
      </p>
      {fixture && <Notice>本机合成验收会话禁止上传真实文件。</Notice>}
      {!api.canPerform("knowledge:write") && (
        <Notice>当前 Owner 会话没有文件写入权限。</Notice>
      )}
      {!command.result && (
        <>
          <label>
            选择文本文件
            <input
              ref={input}
              type="file"
              accept=".txt,.md,text/plain,text/markdown"
              disabled={unavailable || command.locked}
              onChange={(e) => choose(e.target.files?.[0])}
            />
          </label>
          {reading && <p role="status">正在读取所选文件，尚未上传…</p>}
          {(reading || selected || error) && !command.locked && (
            <button className="button" onClick={clear}>
              取消并清除所选文件
            </button>
          )}
          {selected && (
            <div className="supplement-editor">
              <p>
                文件：{selected.filename} · {selected.size} 字节 ·{" "}
                {selected.content_type}
              </p>
              <details open>
                <summary>上传前核对来源正文</summary>
                <MarkdownBody body={selected.text} />
              </details>
              <label>
                文件私人附言（不属于来源正文）
                <textarea
                  rows={4}
                  maxLength={10000}
                  disabled={command.locked}
                  value={note}
                  onChange={(e) => {
                    setNote(e.target.value);
                    setConfirmed(false);
                  }}
                />
              </label>
              <label className="confirmation-label">
                <input
                  type="checkbox"
                  checked={confirmed}
                  disabled={command.locked}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />
                我已核对文件正文与私人附言，只补充预览修订{" "}
                {item.preview_revision}，不自动长期保留。
              </label>
              <button
                className="button primary"
                disabled={
                  unavailable ||
                  command.locked ||
                  !confirmed ||
                  new TextEncoder().encode(note).length > 30000
                }
                onClick={upload}
              >
                {command.busy ? "上传中…" : "上传核对后的文本文件"}
              </button>
            </div>
          )}
        </>
      )}
      {error && <Failure error={error} />}
      <CommandError command={command} />
      {command.result && (
        <Notice title="文本文件已进入临时预览，尚未保留">
          原始字节只保存在私有临时缓存中。请重新查看预览修订{" "}
          {command.result.data.preview_revision}{" "}
          后决定；本地所选文件副本已清除。
          <button className="button" onClick={reload}>
            查看上传后的新预览
          </button>
        </Notice>
      )}
    </section>
  );
}
