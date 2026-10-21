/** Narrow domain HTTP transport. No arbitrary URLs, headers, identities or shell commands. */
import { createHash, randomUUID } from "node:crypto";

export type Envelope = {
  data: Record<string, unknown>;
  task_id: string | null;
  trace_id: string;
  data_classification?: string;
};
export type AuditEvent = {
  event: "zhizhi.http"; trace_id: string; action: string; method: string;
  request_hash: string; status: number | "transport_error";
};
export type AuditSink = (event: AuditEvent) => void;

const CLASSIFICATIONS = new Set(["public", "personal", "third_party_private", "employer", "secret"]);
const CLASSIFICATION_FIELDS = new Set([
  "data_classification", "classification", "input_classification", "evidence_classification", "classification_labels",
]);

/** A domain response cannot grant its own model-transmission permission. */
export function assertDomainClassification(
  response: Record<string, unknown>,
  allowed: readonly string[] = (process.env.ZHIZHI_ALLOWED_CLASSIFICATIONS ?? "public,personal").split(","),
): void {
  if (typeof response.data_classification !== "string" ||
      !CLASSIFICATIONS.has(response.data_classification)) throw new Error("DOMAIN_CLASSIFICATION_REQUIRED");
  if (allowed.some((value) => !CLASSIFICATIONS.has(value) || value === "secret")) {
    throw new Error("MODEL_CLASSIFICATION_POLICY_INVALID");
  }
  const queue: unknown[] = [response];
  while (queue.length) {
    const item = queue.pop();
    if (!item || typeof item !== "object") continue;
    for (const [key, value] of Object.entries(item)) {
      if (CLASSIFICATION_FIELDS.has(key)) {
        const labels = Array.isArray(value) ? value : [value];
        if (!labels.length || labels.some((label) => typeof label !== "string" || !CLASSIFICATIONS.has(label))) {
          throw new Error("DOMAIN_CLASSIFICATION_REQUIRED");
        }
        if (labels.some((label) => label === "secret" || !allowed.includes(label))) throw new Error("DOMAIN_CLASSIFICATION_FORBIDDEN");
      } else if (value && typeof value === "object") queue.push(value);
    }
  }
  // Defense in depth, never a substitute for the server classification or actor scopes.
  const serialized = JSON.stringify(response.data);
  if (/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk-proj-|sk-live-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}|"(?:refresh_token|access_token|client_secret|password|api_key)"\s*:\s*"[^"\s]{6,}"/i.test(serialized)) {
    throw new Error("DOMAIN_SECRET_CONTENT_FORBIDDEN");
  }
}

const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/;
export function safeId(value: string): string {
  if (ID.exec(value)?.[0] !== value) throw new Error("INVALID_RESOURCE_ID");
  return encodeURIComponent(value);
}

export function validateBaseUrl(raw: string): URL {
  const url = new URL(raw);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.username || url.password || url.search || url.hash ||
      (url.protocol !== "https:" && !(local && url.protocol === "http:"))) {
    throw new Error("DOMAIN_URL_REQUIRES_TLS_OR_LOOPBACK");
  }
  if (!["", "/", "/api/knowledge/v1", "/api/knowledge/v1/"].includes(url.pathname)) {
    throw new Error("DOMAIN_URL_PATH_INVALID");
  }
  url.pathname = "/api/knowledge/v1/";
  return url;
}

function routeAllowed(method: string, path: string, user: boolean): boolean {
  const relation = path.match(/^\/relations\/[A-Za-z0-9_.:%-]+(\/traverse)?(?:\?[^#]*)?$/);
  if (relation) {
    if (method !== "GET") return false;
    const query = new URLSearchParams(path.split("?", 2)[1] ?? "");
    const kinds = query.getAll("kind");
    if (kinds.length > 3 || new Set(kinds).size !== kinds.length) return false;
    const bounded = (value: string, maximum: number) => /^[1-9][0-9]*$/.test(value) && Number(value) <= maximum;
    const allowed = new Set(["kind", "direction", "visibility", ...(relation[1] ? ["max_depth", "max_nodes"] : ["limit"])]);
    for (const [key, value] of query) {
      if (!allowed.has(key) || (key !== "kind" && query.getAll(key).length > 1)) return false;
      if (key === "kind" && !["navigation", "confirmed", "candidate"].includes(value)) return false;
      if (key === "direction" && !["in", "out", "both"].includes(value)) return false;
      if (key === "visibility" && !["all", "public"].includes(value)) return false;
      if (key === "limit" && !bounded(value, 100)) return false;
      if (key === "max_depth" && !bounded(value, 3)) return false;
      if (key === "max_nodes" && !bounded(value, 200)) return false;
    }
    return true;
  }
  if (/^\/(?:search|growth|reviews)(?:\?[^#]*)?$/.test(path)) return method === "GET";
  if (/^\/(?:tasks|entries|intakes|changes)\/[A-Za-z0-9_.:%-]+$/.test(path)) return method === "GET";
  if (user && /^\/entries\/[A-Za-z0-9_.:%-]+\?revision=[1-9][0-9]*$/.test(path)) return method === "GET";
  if (["/intakes", "/changes", "/radar/runs"].includes(path)) return method === "POST";
  if (!user) return false;
  if (/^\/intakes\/[A-Za-z0-9_.:%-]+\/decision$/.test(path)) return method === "POST";
  if (/^\/changes\/[A-Za-z0-9_.:%-]+\/decision-challenge$/.test(path)) return method === "GET";
  if (/^\/changes\/[A-Za-z0-9_.:%-]+\/decisions$/.test(path)) return method === "POST";
  return false;
}

export class DomainClient {
  private readonly baseUrl: URL;
  constructor(
    baseUrl: string,
    private readonly token: string,
    private readonly user = false,
    private readonly audit: AuditSink = (event) => { process.stderr.write(JSON.stringify(event) + "\n"); },
    private readonly fetcher: typeof fetch = fetch,
  ) {
    this.baseUrl = validateBaseUrl(baseUrl);
    if (!token || /[\r\n]/.test(token)) throw new Error("DOMAIN_AUTH_NOT_CONFIGURED");
  }

  async request(
    action: string, method: "GET" | "POST", path: string,
    body?: Record<string, unknown>, idempotencyKey?: string, signal?: AbortSignal,
  ): Promise<Envelope> {
    if (!routeAllowed(method, path, this.user)) throw new Error("DOMAIN_ROUTE_FORBIDDEN");
    const target = new URL(path.slice(1), this.baseUrl);
    if (target.origin !== this.baseUrl.origin || !target.pathname.startsWith(this.baseUrl.pathname) ||
        /(?:\.\.|%2e|%2f|%5c)/i.test(path.split("?")[0])) throw new Error("DOMAIN_ROUTE_FORBIDDEN");
    if (method === "POST" && !idempotencyKey) throw new Error("IDEMPOTENCY_KEY_REQUIRED");
    const traceId = randomUUID();
    const data = body === undefined ? undefined : JSON.stringify(body);
    if (data && Buffer.byteLength(data) > 512_000) throw new Error("DOMAIN_INPUT_TOO_LARGE");
    const audit: AuditEvent = {
      event: "zhizhi.http", trace_id: traceId, action, method,
      request_hash: createHash("sha256").update(JSON.stringify({ path, body })).digest("hex"),
      status: "transport_error",
    };
    const timeout = AbortSignal.timeout(30_000);
    try {
      const response = await this.fetcher(target, {
        method, headers: {
          Authorization: `Bearer ${this.token}`, "Content-Type": "application/json",
          "X-Trace-ID": traceId,
          ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
        }, body: data, signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
        redirect: "error", credentials: "omit",
      });
      audit.status = response.status;
      // Bound streamed response before parsing, rather than trusting Content-Length.
      const reader = response.body?.getReader();
      if (!reader) throw new Error("DOMAIN_EMPTY_RESPONSE");
      const chunks: Uint8Array[] = [];
      let size = 0;
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        size += chunk.value.length;
        if (size > 1_048_576) { await reader.cancel(); throw new Error("DOMAIN_RESPONSE_TOO_LARGE"); }
        chunks.push(chunk.value);
      }
      const value = JSON.parse(Buffer.concat(chunks).toString("utf8")) as Record<string, unknown>;
      if (!response.ok) {
        const error = value.error as Record<string, unknown> | undefined;
        const code = typeof error?.code === "string" && /^[A-Z0-9_]+$/.test(error.code)
          ? error.code : "DOMAIN_REQUEST_FAILED";
        throw new Error(`${code} (HTTP ${response.status}, trace ${traceId})`);
      }
      if (!("data" in value) || typeof value.trace_id !== "string") {
        throw new Error("DOMAIN_ENVELOPE_INVALID");
      }
      // TUI user commands show owned content to the actual user, not to a model.
      if (!this.user) assertDomainClassification(value);
      return value as Envelope;
    } finally {
      this.audit(audit); // Only hashes and correlation fields, never body/token/response text.
    }
  }
}

export function requestKey(toolCallId: string, value: unknown): string {
  return `pi-${createHash("sha256").update(toolCallId + JSON.stringify(value)).digest("hex")}`;
}
