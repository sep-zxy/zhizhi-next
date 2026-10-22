import { parseRelationView } from "./relation-contracts";
import {
  fields,
  parseChallenge,
  parsePage,
  parseChange,
  parseEntry,
  parseEvidence,
  parseEvent,
  parseHealth,
  parseHit,
  parseIntake,
  parseList,
  parseTask,
  text,
  type Envelope,
  type BrowserAuthMode,
  type Metadata,
  type EvidenceRef,
} from "./contracts";

export type Transport = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;
export type IntakeSubmission = {
  requested_action: "preview";
  data_classification: "personal";
} & ({ url: string; text?: never } | { text: string; url?: never });

export function intakeSubmissionError(body: IntakeSubmission): string | null {
  if ("text" in body) {
    if (typeof body.text !== "string" || !body.text.trim()) return "EMPTY_TEXT";
    if (Array.from(body.text).length > 100000) return "TEXT_TOO_LARGE";
  }
  return new TextEncoder().encode(JSON.stringify(body)).length > 512 * 1024
    ? "BODY_TOO_LARGE"
    : null;
}
export interface TextAttachmentUpload {
  expected_preview_revision: number;
  filename: string;
  content_type: "text/plain" | "text/markdown";
  content_base64: string;
  private_note: string;
}
export class WikiApiError extends Error {
  constructor(
    public readonly code: string,
    public readonly status: number,
    public readonly traceId = "",
    public readonly retryable = false,
  ) {
    super(errorLabel(code, status));
    this.name = "WikiApiError";
  }
}
export function errorLabel(code: string, status = 0): string {
  if (status === 401 || code === "UNAUTHENTICATED")
    return "尚未连接已认证的私有会话。请在受信任的服务入口完成连接后重试。";
  if (code === "LOCAL_TEST_LINKS_DISABLED")
    return "本机测试会话只用于合成数据验收，真实文本与链接投递尚未开放。";
  if (code === "LOCAL_TEST_WRITES_DISABLED")
    return "本机合成验收会话不接收真实材料或知识修改。";
  if (status === 403) return "当前身份没有访问此内容的权限。未展示受限内容。";
  if (status === 404)
    return "此内容不存在、不可访问，或当前服务尚未提供该接口。";
  return (
    (
      {
        NETWORK_ERROR: "无法连接服务，请检查连接后重试。当前结果未得到确认。",
        EMPTY_TEXT: "请输入非空的来源正文；不会静默删改正文空白。",
        TEXT_TOO_LARGE: "来源正文最多 100,000 个 Unicode 字符。",
        BODY_TOO_LARGE: "完整 JSON 请求超过 512 KiB，请缩短材料后重新提交。",
        INVALID_URL: "请输入不含登录凭据的完整 HTTP 或 HTTPS 链接。",
        multiple_urls:
          "文本包含多个链接，请每次仅投递一个链接或不含链接的正文。",
        INVALID_RESPONSE: "服务返回了无法验证的数据，未按成功处理。",
        BASE_STALE: "基础版本已变化，请刷新并重新查看当前内容。",
        PREVIEW_STALE: "预览已变化，请重新查看后再决定。",
        PREVIEW_EXPIRED: "临时预览已到期，请重新提交材料。",
        NONCE_EXPIRED: "这次确认已过期，请重新查看差异后再确认。",
        REVIEW_REQUIRED: "需要重新复核当前版本。",
        PROJECTION_STALE: "读取期间知识版本发生变化，请重试。",
        IDEMPOTENCY_CONFLICT: "请求内容与已有请求不一致，请先核对已有结果。",
        INVALID_STATE: "当前状态不允许此操作，请刷新查看最新状态。",
        INTAKE_EXPIRED: "临时材料已到期，请重新提交，不会保存这次补料。",
        TASK_RUNNING: "当前解析仍在执行，请等待其结束后再补充。",
        SUPPLEMENT_LIMIT: "本条临时材料已达到补充次数上限。",
        RECONCILIATION_REQUIRED: "前次执行结果需要对账，不能直接恢复或取消。",
        LOCAL_TEST_WRITES_DISABLED:
          "本机合成验收会话不接收真实材料或知识修改。",
        ATTACHMENT_INVALID_NAME: "文件名不能包含路径或控制字符。",
        ATTACHMENT_UNSUPPORTED:
          "仅支持 UTF-8 的 .txt/.md 文本文件，不接受归档、二进制或其他格式。",
        ATTACHMENT_TOO_LARGE: "文本文件必须为 1 至 100,000 字节。",
        ATTACHMENT_INVALID_UTF8:
          "文件不是有效 UTF-8 文本，不会猜测或转换编码。",
        ATTACHMENT_INVALID: "文件内容或编码无法验证，尚未上传。",
        ATTACHMENT_READ_FAILED: "无法读取所选文本文件，请重新选择。",
        LOCAL_TEST_LINKS_DISABLED:
          "本机测试会话只用于合成数据验收，真实文本与链接投递尚未开放。",
      } as Record<string, string>
    )[code] || "请求未完成。请保留当前信息，并根据错误标识检查服务状态。"
  );
}
export function safeCode(value: unknown): string {
  const v = text(value);
  return /^[A-Za-z0-9_.:-]{1,100}$/.test(v) ? v : "UNKNOWN_ERROR";
}
function segment(value: string): string {
  if (!value || value.length > 256)
    throw new WikiApiError("INVALID_INPUT", 422);
  return encodeURIComponent(value);
}
export const PAGE_SIZE = 100;
const AUTH_PREFIX = {
  local_test: "/api/knowledge/v1/auth/local-test",
  owner_loopback: "/api/knowledge/v1/auth/owner",
} as const;
export type BrowserScope =
  | "knowledge:read"
  | "knowledge:write"
  | "knowledge:review";
const OWNER_SCOPES: readonly BrowserScope[] = [
  "knowledge:read",
  "knowledge:write",
  "knowledge:review",
];
const SECRET_VALUE = /^[A-Za-z0-9_-]{32,128}(?![\s\S])/;
export type BrowserSession = Readonly<{
  mode?: Exclude<BrowserAuthMode, "disabled">;
  fixture?: boolean;
  scopes?: readonly BrowserScope[];
  status: "waiting_auth" | "waiting_approval" | "authenticated";
  pairingCode?: string;
  expiresAt: number;
}>;
const NO_LOCAL_SESSION: BrowserSession = Object.freeze({
  status: "waiting_auth",
  expiresAt: 0,
});

/** Only same-origin, noncached requests. Authentication belongs to the host, not this UI. */
export class WikiApi {
  // Never exposed to React state, URLs, browser storage, logs, or public snapshots.
  #pairingProof = "";
  #csrf = "";
  #authEpoch = 0;
  #authBusy = false;
  #browserMode: BrowserAuthMode = "disabled";
  #learningViewerUrl: string | null | undefined;
  #localSession: BrowserSession = NO_LOCAL_SESSION;
  private readonly sessionListeners = new Set<() => void>();
  private accessBlocked = false;
  private readonly accessListeners = new Set<() => void>();
  constructor(
    private readonly transport: Transport = (input, init) => fetch(input, init),
  ) {}
  readonly isAccessBlocked = () => this.accessBlocked;
  readonly canRetryAccess = () =>
    !this.#authBusy && this.#localSession.status === "waiting_auth";
  readonly getBrowserSession = () => this.#localSession;
  readonly getBrowserMode = () => this.#browserMode;
  readonly getLearningViewerUrl = () => this.#learningViewerUrl;
  readonly isFixtureSession = () =>
    this.#localSession.status === "authenticated" &&
    this.#localSession.mode === "local_test";
  readonly canPerform = (scope: BrowserScope) =>
    this.#browserMode !== "owner_loopback" ||
    (this.#localSession.status === "authenticated" &&
      this.#localSession.mode === "owner_loopback" &&
      this.#localSession.scopes?.includes(scope) === true);
  private authPrefix() {
    if (this.#browserMode === "disabled")
      throw new WikiApiError("BROWSER_AUTH_DISABLED", 401);
    return AUTH_PREFIX[this.#browserMode];
  }
  readonly subscribeBrowserSession = (listener: () => void) => {
    this.sessionListeners.add(listener);
    return () => {
      this.sessionListeners.delete(listener);
    };
  };
  private setBrowserSession(value: BrowserSession) {
    this.#localSession = Object.freeze(value);
    for (const listener of this.sessionListeners) listener();
  }
  private setBrowserAuthBusy(value: boolean) {
    this.#authBusy = value;
    for (const listener of this.sessionListeners) listener();
  }
  forgetBrowserSession() {
    this.#authEpoch += 1;
    this.#pairingProof = "";
    this.#csrf = "";
    this.setBrowserSession(NO_LOCAL_SESSION);
    this.setAccessBlocked(true);
  }
  expireBrowserSession() {
    if (
      this.#localSession.expiresAt &&
      this.#localSession.expiresAt <= Date.now()
    )
      this.forgetBrowserSession();
  }
  private localAuthValue(raw: unknown) {
    const value = fields(raw);
    const owner = this.#browserMode === "owner_loopback";
    if (
      this.#browserMode === "disabled" ||
      (owner
        ? value.fixture !== false ||
          value.authentication_mode !== "owner_loopback"
        : value.fixture !== true ||
          (value.authentication_mode !== undefined &&
            value.authentication_mode !== "local_test"))
    )
      throw new WikiApiError("INVALID_RESPONSE", 200);
    if (value.status !== "authenticated" && value.scopes !== undefined)
      throw new WikiApiError("INVALID_RESPONSE", 200);
    return value;
  }
  private localAuthTtl(value: unknown, maximum: number) {
    if (
      typeof value !== "number" ||
      !Number.isSafeInteger(value) ||
      value < 1 ||
      value > maximum
    )
      throw new WikiApiError("INVALID_RESPONSE", 200);
    return Date.now() + value * 1000;
  }
  async requestBrowserPairing(): Promise<BrowserSession> {
    if (this.#authBusy) throw new WikiApiError("LOCAL_AUTH_BUSY", 409);
    this.setBrowserAuthBusy(true);
    this.forgetBrowserSession();
    const epoch = this.#authEpoch;
    try {
      await this.health(AbortSignal.timeout(15000));
      const prefix = this.authPrefix();
      const value = this.localAuthValue(
        await this.raw(prefix + "/request", {
          method: "POST",
          body: "{}",
          signal: AbortSignal.timeout(15000),
        }),
      );
      if (epoch !== this.#authEpoch)
        throw new WikiApiError("LOCAL_SESSION_CHANGED", 409);
      const expiresAt = this.localAuthTtl(value.expires_in_seconds, 120);
      if (
        value.status !== "waiting_approval" ||
        !/^[A-F0-9]{20}(?![\s\S])/.test(text(value.pairing_code)) ||
        !SECRET_VALUE.test(text(value.pairing_proof))
      )
        throw new WikiApiError("INVALID_RESPONSE", 200);
      this.#pairingProof = text(value.pairing_proof);
      this.setBrowserSession({
        status: "waiting_approval",
        mode: this.#browserMode as Exclude<BrowserAuthMode, "disabled">,
        fixture: this.#browserMode === "local_test",
        pairingCode: text(value.pairing_code),
        expiresAt,
      });
      return this.#localSession;
    } catch (error) {
      if (epoch === this.#authEpoch) this.forgetBrowserSession();
      throw error;
    } finally {
      this.setBrowserAuthBusy(false);
    }
  }
  async checkBrowserSession(): Promise<BrowserSession> {
    if (this.#authBusy) throw new WikiApiError("LOCAL_AUTH_BUSY", 409);
    this.expireBrowserSession();
    if (!this.#pairingProof) throw new WikiApiError("UNAUTHENTICATED", 401);
    this.setBrowserAuthBusy(true);
    const epoch = this.#authEpoch;
    try {
      const value = this.localAuthValue(
        await this.raw(this.authPrefix() + "/session", {
          signal: AbortSignal.timeout(15000),
        }),
      );
      if (epoch !== this.#authEpoch)
        throw new WikiApiError("LOCAL_SESSION_CHANGED", 409);
      if (value.status === "waiting_auth") this.forgetBrowserSession();
      else if (value.status === "authenticated") {
        const expiresAt = this.localAuthTtl(value.expires_in_seconds, 900);
        if (!SECRET_VALUE.test(text(value.csrf_token)))
          throw new WikiApiError("INVALID_RESPONSE", 200);
        const owner = this.#browserMode === "owner_loopback";
        let scopes: readonly BrowserScope[] | undefined;
        if (owner) {
          if (
            !Array.isArray(value.scopes) ||
            !value.scopes.includes("knowledge:read") ||
            new Set(value.scopes).size !== value.scopes.length ||
            value.scopes.some(
              (scope) => !OWNER_SCOPES.includes(scope as BrowserScope),
            )
          )
            throw new WikiApiError("INVALID_RESPONSE", 200);
          scopes = Object.freeze([...value.scopes]) as readonly BrowserScope[];
        } else if (value.scopes !== undefined)
          throw new WikiApiError("INVALID_RESPONSE", 200);
        this.#csrf = text(value.csrf_token);
        this.setBrowserSession({
          status: "authenticated",
          expiresAt,
          mode: owner ? "owner_loopback" : "local_test",
          fixture: !owner,
          ...(scopes ? { scopes } : {}),
        });
        this.setAccessBlocked(false);
      } else if (
        value.status !== "waiting_approval" ||
        this.#localSession.status !== "waiting_approval"
      ) {
        throw new WikiApiError("INVALID_RESPONSE", 200);
      }
      return this.#localSession;
    } catch (error) {
      if (
        error instanceof WikiApiError &&
        error.code === "INVALID_RESPONSE" &&
        epoch === this.#authEpoch
      )
        this.forgetBrowserSession();
      throw error;
    } finally {
      this.setBrowserAuthBusy(false);
    }
  }
  async logoutBrowserSession(): Promise<void> {
    if (this.#authBusy) throw new WikiApiError("LOCAL_AUTH_BUSY", 409);
    this.expireBrowserSession();
    if (!this.#pairingProof || !this.#csrf)
      throw new WikiApiError("UNAUTHENTICATED", 401);
    this.setBrowserAuthBusy(true);
    this.setAccessBlocked(true);
    try {
      const value = this.localAuthValue(
        await this.raw(this.authPrefix() + "/logout", {
          method: "POST",
          body: "{}",
          signal: AbortSignal.timeout(15000),
        }),
      );
      if (value.status !== "waiting_auth")
        throw new WikiApiError("INVALID_RESPONSE", 200);
    } finally {
      this.forgetBrowserSession();
      this.setBrowserAuthBusy(false);
    }
  }
  readonly subscribeAccess = (listener: () => void) => {
    this.accessListeners.add(listener);
    return () => {
      this.accessListeners.delete(listener);
    };
  };
  retryAccess() {
    if (!this.canRetryAccess()) return;
    this.setAccessBlocked(false);
  }
  private setAccessBlocked(value: boolean) {
    if (this.accessBlocked === value) return;
    this.accessBlocked = value;
    for (const listener of this.accessListeners) listener();
  }
  private async raw(path: string, init: RequestInit = {}): Promise<unknown> {
    const hadProof = !!this.#pairingProof;
    this.expireBrowserSession();
    if (hadProof && !this.#pairingProof)
      throw new WikiApiError("UNAUTHENTICATED", 401);
    const epoch = this.#authEpoch;
    const domainRequest = path.startsWith("/api/knowledge/v1/");
    const unsafe = !["GET", "HEAD", "OPTIONS"].includes(init.method || "GET");
    let response: Response;
    try {
      response = await this.transport(path, {
        ...init,
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        headers: {
          Accept: "application/json",
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...init.headers,
          ...(domainRequest && this.#pairingProof
            ? { "X-Wiki-Pairing-Proof": this.#pairingProof }
            : {}),
          ...(domainRequest && unsafe && this.#csrf
            ? { "X-Wiki-CSRF": this.#csrf }
            : {}),
        },
      });
    } catch (error) {
      if (init.signal?.aborted) throw error;
      throw new WikiApiError("NETWORK_ERROR", 0, "", true);
    }
    // A cancelled route must not invalidate a newer explicitly retried session.
    if (init.signal?.aborted || epoch !== this.#authEpoch)
      throw new DOMException("Aborted", "AbortError");
    if (response.status === 401) this.forgetBrowserSession();
    const responseEpoch = this.#authEpoch;
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new WikiApiError("INVALID_RESPONSE", response.status);
    }
    // JSON decoding can finish after logout, expiry, or a new explicit pairing.
    if (init.signal?.aborted || responseEpoch !== this.#authEpoch)
      throw new DOMException("Aborted", "AbortError");
    if (!response.ok) {
      const data = fields(body),
        error = fields(data.error);
      if (
        this.#pairingProof &&
        [
          "CSRF_REJECTED",
          "BROWSER_ORIGIN_REJECTED",
          "BROWSER_REQUEST_REJECTED",
        ].includes(text(error.code))
      )
        this.forgetBrowserSession();
      throw new WikiApiError(
        safeCode(error.code),
        response.status,
        safeCode(data.trace_id),
        error.retryable === true,
      );
    }
    return body;
  }
  private async read<T>(
    path: string,
    parse: (v: unknown) => T,
    signal?: AbortSignal,
    requestBody?: Metadata,
  ): Promise<Envelope<T>> {
    const body = fields(
      await this.raw("/api/knowledge/v1" + path, {
        signal,
        ...(requestBody
          ? { method: "POST", body: JSON.stringify(requestBody) }
          : {}),
      }),
    );
    if (
      !Object.prototype.hasOwnProperty.call(body, "data") ||
      typeof body.trace_id !== "string"
    )
      throw new WikiApiError("INVALID_RESPONSE", 200);
    try {
      return {
        data: parse(body.data),
        task_id: typeof body.task_id === "string" ? body.task_id : null,
        trace_id: body.trace_id,
      };
    } catch (error) {
      if (error instanceof WikiApiError) throw error;
      throw new WikiApiError("INVALID_RESPONSE", 200, safeCode(body.trace_id));
    }
  }
  private async write<T>(
    path: string,
    body: Metadata,
    key: string,
    parse: (v: unknown) => T,
  ): Promise<Envelope<T>> {
    this.expireBrowserSession();
    if (
      !this.canPerform(
        path.endsWith("/decisions") ? "knowledge:review" : "knowledge:write",
      )
    )
      throw new WikiApiError("FORBIDDEN", 403);
    const result = fields(
      await this.raw("/api/knowledge/v1" + path, {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify(body),
      }),
    );
    if (
      !Object.prototype.hasOwnProperty.call(result, "data") ||
      typeof result.trace_id !== "string"
    )
      throw new WikiApiError("INVALID_RESPONSE", 200);
    try {
      return {
        data: parse(result.data),
        task_id: typeof result.task_id === "string" ? result.task_id : null,
        trace_id: result.trace_id,
      };
    } catch (error) {
      if (error instanceof WikiApiError) throw error;
      throw new WikiApiError(
        "INVALID_RESPONSE",
        200,
        safeCode(result.trace_id),
      );
    }
  }
  relations(id: string, signal?: AbortSignal) {
    return this.read(
      "/relations/" + encodeURIComponent(id) + "?limit=20",
      (raw) => parseRelationView(raw, id),
      signal,
    );
  }
  entries(offset = 0, signal?: AbortSignal) {
    return this.read(
      "/entries?limit=" + PAGE_SIZE + "&offset=" + offset,
      (v) => parseList(v, parseEntry),
      signal,
    );
  }
  entry(id: string, revision?: number, signal?: AbortSignal) {
    if (
      revision !== undefined &&
      (!Number.isSafeInteger(revision) || revision < 1)
    )
      return Promise.reject(new WikiApiError("INVALID_INPUT", 422));
    return this.read(
      "/entries/" + segment(id) + (revision ? "?revision=" + revision : ""),
      (raw) => {
        const value = parseEntry(raw);
        if (
          value.entry_id !== id ||
          (revision !== undefined && value.revision !== revision)
        )
          throw new WikiApiError("INVALID_RESPONSE", 200);
        return value;
      },
      signal,
    );
  }
  evidence(ref: EvidenceRef, signal?: AbortSignal) {
    return this.read(
      "/evidence/read",
      (raw) => {
        const value = parseEvidence(raw);
        if (
          (ref.source_id && value.source_id !== ref.source_id) ||
          (ref.external_document_id &&
            value.document_id !== ref.external_document_id) ||
          (ref.learning_outcome_id &&
            value.outcome_id !== ref.learning_outcome_id) ||
          (ref.content_hash && value.content_hash !== ref.content_hash) ||
          (ref.source_revision !== undefined &&
            value.revision !== ref.source_revision)
        )
          throw new WikiApiError("INVALID_RESPONSE", 200);
        return value;
      },
      signal,
      { ...ref },
    );
  }
  proposeChange(body: Metadata, key: string) {
    if (this.isFixtureSession())
      return Promise.reject(
        new WikiApiError("LOCAL_TEST_WRITES_DISABLED", 403),
      );
    return this.write("/changes", body, key, (raw) => {
      const change = parseChange(raw);
      for (const name of [
        "entry_id",
        "base_revision",
        "operation",
        "title",
        "body",
      ] as const) {
        if (body[name] !== undefined && body[name] !== change[name])
          throw new WikiApiError("INVALID_RESPONSE", 200);
      }
      return change;
    });
  }
  controlTask(
    id: string,
    action: "resume" | "cancel",
    revision: number,
    key: string,
  ) {
    return this.write(
      "/tasks/" + segment(id) + "/" + action,
      { expected_revision: revision },
      key,
      (raw) => {
        const value = parseTask(raw);
        if (
          value.task_id !== id ||
          value.revision !== revision + 1 ||
          value.state !== (action === "resume" ? "queued" : "cancelled")
        )
          throw new WikiApiError("INVALID_RESPONSE", 200);
        return value;
      },
    );
  }
  supplementIntake(
    id: string,
    body: {
      expected_preview_revision: number;
      text: string;
      private_note: string;
    },
    key: string,
  ) {
    if (this.isFixtureSession())
      return Promise.reject(
        new WikiApiError("LOCAL_TEST_WRITES_DISABLED", 403),
      );
    return this.write(
      "/intakes/" + segment(id) + "/supplement",
      body,
      key,
      (raw) => {
        const value = parseIntake(raw);
        if (
          value.intake_id === id &&
          ["discarded", "expired"].includes(value.state)
        )
          throw new WikiApiError(
            value.state === "expired" ? "PREVIEW_EXPIRED" : "INVALID_STATE",
            409,
          );
        if (
          value.intake_id !== id ||
          value.preview_revision !== body.expected_preview_revision + 1 ||
          value.state !== "partial"
        )
          throw new WikiApiError("INVALID_RESPONSE", 200);
        return value;
      },
    );
  }
  uploadTextAttachment(id: string, body: TextAttachmentUpload, key: string) {
    if (this.isFixtureSession())
      return Promise.reject(
        new WikiApiError("LOCAL_TEST_WRITES_DISABLED", 403),
      );
    return this.write(
      "/intakes/" + segment(id) + "/text-attachment",
      { ...body },
      key,
      (raw) => {
        const value = parseIntake(raw);
        if (
          value.intake_id === id &&
          ["discarded", "expired"].includes(value.state)
        )
          throw new WikiApiError(
            value.state === "expired" ? "PREVIEW_EXPIRED" : "INVALID_STATE",
            409,
          );
        if (
          value.intake_id !== id ||
          value.preview_revision !== body.expected_preview_revision + 1 ||
          value.state !== "partial"
        )
          throw new WikiApiError("INVALID_RESPONSE", 200);
        return value;
      },
    );
  }
  search(query: string, signal?: AbortSignal) {
    return this.read(
      "/search?" +
        new URLSearchParams({
          q: query,
          scope: "wiki",
          limit: String(PAGE_SIZE),
        }),
      (v) => parseList(fields(v).results, parseHit),
      signal,
    );
  }
  task(id: string, signal?: AbortSignal) {
    return this.read("/tasks/" + segment(id), parseTask, signal);
  }
  taskEvents(id: string, after = 0, signal?: AbortSignal) {
    return this.read(
      "/tasks/" +
        segment(id) +
        "/events?after=" +
        after +
        "&limit=" +
        PAGE_SIZE,
      (v) => parseList(v, parseEvent),
      signal,
    );
  }
  reviews(signal?: AbortSignal) {
    return this.read(
      "/reviews?state=needs_user&limit=" + PAGE_SIZE,
      (v) => parseList(v, parseChange),
      signal,
    );
  }
  change(id: string, signal?: AbortSignal) {
    return this.read("/changes/" + segment(id), parseChange, signal);
  }
  intake(id: string, signal?: AbortSignal) {
    return this.read("/intakes/" + segment(id), parseIntake, signal);
  }
  capabilities(signal?: AbortSignal) {
    return this.read(
      "/intakes/capabilities",
      (v) => parseList(fields(v).capabilities, fields),
      signal,
    );
  }
  intakes(offset = 0, state = "", signal?: AbortSignal) {
    return this.read(
      "/intakes?" +
        new URLSearchParams({
          limit: String(PAGE_SIZE),
          offset: String(offset),
          ...(state ? { state } : {}),
        }),
      (v) => parsePage(v, parseIntake),
      signal,
    );
  }
  tasks(offset = 0, state = "", signal?: AbortSignal) {
    return this.read(
      "/tasks?" +
        new URLSearchParams({
          limit: String(PAGE_SIZE),
          offset: String(offset),
          ...(state ? { state } : {}),
        }),
      (v) => parsePage(v, parseTask),
      signal,
    );
  }
  challenge(id: string, signal?: AbortSignal) {
    return this.read(
      "/changes/" + segment(id) + "/decision-challenge",
      parseChallenge,
      signal,
    );
  }
  decideChange(id: string, body: Metadata, key: string) {
    return this.write(
      "/changes/" + segment(id) + "/decisions",
      body,
      key,
      fields,
    );
  }
  decideIntake(
    id: string,
    body: { decision: "keep" | "discard"; preview_revision: number },
    key: string,
  ) {
    return this.write(
      "/intakes/" + segment(id) + "/decision",
      body,
      key,
      fields,
    );
  }
  async health(signal?: AbortSignal) {
    try {
      const health = parseHealth(await this.raw("/health", { signal }));
      const mode = health.browser_authentication_mode;
      if (this.#learningViewerUrl !== health.learning_viewer_url) {
        this.#learningViewerUrl = health.learning_viewer_url;
        for (const listener of this.sessionListeners) listener();
      }
      if (mode !== this.#browserMode) {
        const active = this.#localSession.status !== "waiting_auth";
        this.#browserMode = mode;
        if (active) this.forgetBrowserSession();
        else for (const listener of this.sessionListeners) listener();
      }
      return health;
    } catch (error) {
      if (
        signal?.aborted ||
        (error instanceof DOMException && error.name === "AbortError")
      )
        throw error;
      if (
        !(error instanceof WikiApiError) ||
        error.code === "INVALID_RESPONSE"
      ) {
        this.#browserMode = "disabled";
        this.#learningViewerUrl = null;
        this.forgetBrowserSession();
      }
      if (error instanceof WikiApiError || signal?.aborted) throw error;
      throw new WikiApiError("INVALID_RESPONSE", 200);
    }
  }
  submitIntake(body: IntakeSubmission, key: string) {
    if (this.isFixtureSession())
      return Promise.reject(new WikiApiError("LOCAL_TEST_LINKS_DISABLED", 403));
    const invalid = intakeSubmissionError(body);
    if (invalid) return Promise.reject(new WikiApiError(invalid, 422));
    return this.write("/intakes", body, key, parseIntake);
  }
}
export const defaultApi = new WikiApi();
