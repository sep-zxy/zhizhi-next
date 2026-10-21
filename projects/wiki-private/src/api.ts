import {
  fields,
  parseChallenge,
  parsePage,
  parseChange,
  parseEntry,
  parseEvent,
  parseHealth,
  parseHit,
  parseIntake,
  parseList,
  parseTask,
  text,
  type Envelope,
  type Metadata,
} from "./contracts";

export type Transport = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;
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
    return "本机测试会话只用于合成数据验收，真实链接投递尚未开放。";
  if (status === 403) return "当前身份没有访问此内容的权限。未展示受限内容。";
  if (status === 404)
    return "此内容不存在、不可访问，或当前服务尚未提供该接口。";
  return (
    (
      {
        NETWORK_ERROR: "无法连接服务，请检查连接后重试。当前结果未得到确认。",
        INVALID_RESPONSE: "服务返回了无法验证的数据，未按成功处理。",
        BASE_STALE: "基础版本已变化，请刷新并重新查看当前内容。",
        PREVIEW_STALE: "预览已变化，请重新查看后再决定。",
        PREVIEW_EXPIRED: "临时预览已到期，请重新提交材料。",
        NONCE_EXPIRED: "这次确认已过期，请重新查看差异后再确认。",
        REVIEW_REQUIRED: "需要重新复核当前版本。",
        PROJECTION_STALE: "读取期间知识版本发生变化，请重试。",
        IDEMPOTENCY_CONFLICT: "请求内容与已有请求不一致，请先核对已有结果。",
        INVALID_STATE: "当前状态不允许此操作，请刷新查看最新状态。",
        LOCAL_TEST_LINKS_DISABLED:
          "本机测试会话只用于合成数据验收，真实链接投递尚未开放。",
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
const LOCAL_AUTH = "/api/knowledge/v1/auth/local-test";
const SECRET_VALUE = /^[A-Za-z0-9_-]{32,128}$/;
export type LocalTestSession = Readonly<{
  status: "waiting_auth" | "waiting_approval" | "authenticated";
  pairingCode?: string;
  expiresAt: number;
}>;
const NO_LOCAL_SESSION: LocalTestSession = Object.freeze({
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
  #localSession: LocalTestSession = NO_LOCAL_SESSION;
  private readonly sessionListeners = new Set<() => void>();
  private accessBlocked = false;
  private readonly accessListeners = new Set<() => void>();
  constructor(
    private readonly transport: Transport = (input, init) => fetch(input, init),
  ) {}
  readonly isAccessBlocked = () => this.accessBlocked;
  readonly canRetryAccess = () =>
    !this.#authBusy && this.#localSession.status === "waiting_auth";
  readonly getLocalTestSession = () => this.#localSession;
  readonly subscribeLocalTestSession = (listener: () => void) => {
    this.sessionListeners.add(listener);
    return () => {
      this.sessionListeners.delete(listener);
    };
  };
  private setLocalTestSession(value: LocalTestSession) {
    this.#localSession = Object.freeze(value);
    for (const listener of this.sessionListeners) listener();
  }
  private setLocalTestAuthBusy(value: boolean) {
    this.#authBusy = value;
    for (const listener of this.sessionListeners) listener();
  }
  forgetLocalTestSession() {
    this.#authEpoch += 1;
    this.#pairingProof = "";
    this.#csrf = "";
    this.setLocalTestSession(NO_LOCAL_SESSION);
    this.setAccessBlocked(true);
  }
  expireLocalTestSession() {
    if (
      this.#localSession.expiresAt &&
      this.#localSession.expiresAt <= Date.now()
    )
      this.forgetLocalTestSession();
  }
  private localAuthValue(raw: unknown) {
    const value = fields(raw);
    if (value.fixture !== true) throw new WikiApiError("INVALID_RESPONSE", 200);
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
  async requestLocalTestPairing(): Promise<LocalTestSession> {
    if (this.#authBusy) throw new WikiApiError("LOCAL_AUTH_BUSY", 409);
    this.setLocalTestAuthBusy(true);
    this.forgetLocalTestSession();
    const epoch = this.#authEpoch;
    try {
      const value = this.localAuthValue(
        await this.raw(LOCAL_AUTH + "/request", {
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
        !/^[A-F0-9]{20}$/.test(text(value.pairing_code)) ||
        !SECRET_VALUE.test(text(value.pairing_proof))
      )
        throw new WikiApiError("INVALID_RESPONSE", 200);
      this.#pairingProof = text(value.pairing_proof);
      this.setLocalTestSession({
        status: "waiting_approval",
        pairingCode: text(value.pairing_code),
        expiresAt,
      });
      return this.#localSession;
    } catch (error) {
      if (epoch === this.#authEpoch) this.forgetLocalTestSession();
      throw error;
    } finally {
      this.setLocalTestAuthBusy(false);
    }
  }
  async checkLocalTestSession(): Promise<LocalTestSession> {
    if (this.#authBusy) throw new WikiApiError("LOCAL_AUTH_BUSY", 409);
    this.expireLocalTestSession();
    if (!this.#pairingProof) throw new WikiApiError("UNAUTHENTICATED", 401);
    this.setLocalTestAuthBusy(true);
    const epoch = this.#authEpoch;
    try {
      const value = this.localAuthValue(
        await this.raw(LOCAL_AUTH + "/session", {
          signal: AbortSignal.timeout(15000),
        }),
      );
      if (epoch !== this.#authEpoch)
        throw new WikiApiError("LOCAL_SESSION_CHANGED", 409);
      if (value.status === "waiting_auth") this.forgetLocalTestSession();
      else if (value.status === "authenticated") {
        const expiresAt = this.localAuthTtl(value.expires_in_seconds, 900);
        if (!SECRET_VALUE.test(text(value.csrf_token)))
          throw new WikiApiError("INVALID_RESPONSE", 200);
        this.#csrf = text(value.csrf_token);
        this.setLocalTestSession({ status: "authenticated", expiresAt });
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
        this.forgetLocalTestSession();
      throw error;
    } finally {
      this.setLocalTestAuthBusy(false);
    }
  }
  async logoutLocalTestSession(): Promise<void> {
    if (this.#authBusy) throw new WikiApiError("LOCAL_AUTH_BUSY", 409);
    this.expireLocalTestSession();
    if (!this.#pairingProof || !this.#csrf)
      throw new WikiApiError("UNAUTHENTICATED", 401);
    this.setLocalTestAuthBusy(true);
    this.setAccessBlocked(true);
    try {
      const value = this.localAuthValue(
        await this.raw(LOCAL_AUTH + "/logout", {
          method: "POST",
          body: "{}",
          signal: AbortSignal.timeout(15000),
        }),
      );
      if (value.status !== "waiting_auth")
        throw new WikiApiError("INVALID_RESPONSE", 200);
    } finally {
      this.forgetLocalTestSession();
      this.setLocalTestAuthBusy(false);
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
    this.expireLocalTestSession();
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
    if (response.status === 401) this.forgetLocalTestSession();
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
        this.forgetLocalTestSession();
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
  ): Promise<Envelope<T>> {
    const body = fields(await this.raw("/api/knowledge/v1" + path, { signal }));
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
    } catch {
      throw new WikiApiError(
        "INVALID_RESPONSE",
        200,
        safeCode(result.trace_id),
      );
    }
  }
  entries(offset = 0, signal?: AbortSignal) {
    return this.read(
      "/entries?limit=" + PAGE_SIZE + "&offset=" + offset,
      (v) => parseList(v, parseEntry),
      signal,
    );
  }
  entry(id: string, revision?: number, signal?: AbortSignal) {
    return this.read(
      "/entries/" + segment(id) + (revision ? "?revision=" + revision : ""),
      parseEntry,
      signal,
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
      return parseHealth(await this.raw("/health", { signal }));
    } catch (error) {
      if (error instanceof WikiApiError || signal?.aborted) throw error;
      throw new WikiApiError("INVALID_RESPONSE", 200);
    }
  }
  submitIntake(
    body: {
      url: string;
      requested_action: "preview";
      data_classification: "personal";
    },
    key: string,
  ) {
    if (this.#localSession.status === "authenticated")
      return Promise.reject(new WikiApiError("LOCAL_TEST_LINKS_DISABLED", 403));
    return this.write("/intakes", body, key, parseIntake);
  }
}
export const defaultApi = new WikiApi();
