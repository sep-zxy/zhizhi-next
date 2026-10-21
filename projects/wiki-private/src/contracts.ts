/** Authenticated domain wire contracts. Unknown fields are never blindly rendered. */
export type Metadata = Record<string, unknown>;
export interface EvidenceRef {
  source_id?: string;
  source_revision?: number;
  claim_ids?: string[];
  learning_outcome_id?: string;
  content_hash?: string;
  locator?: Metadata;
}
export interface Entry {
  entry_id: string;
  revision: number;
  title: string;
  body: string;
  entry_type: string;
  visibility: string;
  publication_intent?: string;
  publication_eligible?: boolean;
  evidence_refs: EvidenceRef[];
  metadata: Metadata;
  deleted: boolean;
  created_at: string;
  updated_at: string;
  commit_sha?: string;
}
export interface SearchHit {
  entry_id: string;
  revision: number;
  title: string;
  snippet: string;
  stale_index: boolean;
  scope: string;
  visibility: string;
  evidence_refs: EvidenceRef[];
  matched_section?: string;
}
export interface Task {
  task_id: string;
  kind: string;
  state: string;
  revision: number;
  attempt: number;
  max_attempts: number;
  created_at: string;
  updated_at: string;
  next_run_at?: string;
  reconciliation_required?: boolean;
  error?: { code?: string } | null;
  result?: Metadata | null;
}
export interface TaskEvent {
  event_id: string;
  type: string;
  sequence: number;
  occurred_at: string;
  aggregate_revision: number;
  payload?: Metadata;
}
export interface Change {
  change_id: string;
  entry_id: string;
  title: string;
  body: string;
  state: string;
  operation: string;
  entry_type: string;
  publication_intent?: string;
  publication_eligible?: boolean;
  base_revision: number;
  patch_hash: string;
  evidence_refs: EvidenceRef[];
  risk_flags: string[];
  visibility: string;
  metadata: Metadata;
  updated_at: string;
  task_id?: string;
  review_level?: string;
}
export interface Intake {
  intake_id: string;
  state: string;
  channel: string;
  url?: string;
  title?: string;
  preview_revision?: number;
  parse_status?: string;
  source_id?: string;
  task_id?: string;
  expires_at?: string;
  created_at: string;
  updated_at: string;
  preview?: Metadata;
}
export interface Health {
  status: string;
  service: string;
  worker_enabled: boolean;
  channel_worker_enabled: boolean;
  channel_status: string;
  automatic_commit_enabled: boolean;
  publication_enabled: boolean;
}
export interface Envelope<T> {
  data: T;
  task_id: string | null;
  trace_id: string;
}

export const isRecord = (value: unknown): value is Metadata =>
  !!value && typeof value === "object" && !Array.isArray(value);
export function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}
export function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((v): v is string => typeof v === "string")
    : [];
}
export function fields(value: unknown): Metadata {
  return isRecord(value) ? value : {};
}
function record(value: unknown): Metadata {
  if (!isRecord(value)) throw new Error("INVALID_RESPONSE");
  return value;
}
function required(value: unknown): string {
  if (typeof value !== "string" || !value) throw new Error("INVALID_RESPONSE");
  return value;
}
function integer(value: unknown, fallback?: number): number {
  if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0)
    return value;
  if (fallback !== undefined) return fallback;
  throw new Error("INVALID_RESPONSE");
}
function refs(value: unknown): EvidenceRef[] {
  return Array.isArray(value)
    ? value.filter(isRecord).map((v) => ({
        source_id: text(v.source_id) || undefined,
        source_revision:
          typeof v.source_revision === "number"
            ? integer(v.source_revision)
            : undefined,
        claim_ids: strings(v.claim_ids),
        learning_outcome_id: text(v.learning_outcome_id) || undefined,
        content_hash: text(v.content_hash) || undefined,
        locator: fields(v.locator),
      }))
    : [];
}
export function parseEntry(raw: unknown): Entry {
  const v = record(raw);
  if (typeof v.deleted !== "boolean") throw new Error("INVALID_RESPONSE");
  if (typeof v.body !== "string") throw new Error("INVALID_RESPONSE");
  return {
    entry_id: required(v.entry_id),
    revision: integer(v.revision),
    title: required(v.title),
    body: v.body,
    entry_type: text(v.entry_type, "knowledge"),
    visibility: text(v.visibility, "unknown"),
    publication_intent: text(v.publication_intent) || undefined,
    publication_eligible:
      typeof v.publication_eligible === "boolean"
        ? v.publication_eligible
        : undefined,
    evidence_refs: refs(v.evidence_refs),
    metadata: fields(v.metadata),
    deleted: v.deleted === true,
    created_at: text(v.created_at),
    updated_at: text(v.updated_at),
    commit_sha: text(v.commit_sha) || undefined,
  };
}
export function parseHit(raw: unknown): SearchHit {
  const v = record(raw);
  if (typeof v.snippet !== "string" || typeof v.stale_index !== "boolean")
    throw new Error("INVALID_RESPONSE");
  return {
    entry_id: required(v.entry_id),
    revision: integer(v.revision),
    title: required(v.title),
    snippet: v.snippet,
    stale_index: v.stale_index,
    scope: required(v.scope),
    visibility: text(v.visibility, "unknown"),
    evidence_refs: refs(v.evidence_refs),
    matched_section: text(v.matched_section) || undefined,
  };
}
export function parseTask(raw: unknown): Task {
  const v = record(raw);
  return {
    task_id: required(v.task_id),
    kind: required(v.kind),
    state: required(v.state),
    revision: integer(v.revision),
    attempt: integer(v.attempt, 0),
    max_attempts: integer(v.max_attempts, 0),
    created_at: text(v.created_at),
    updated_at: text(v.updated_at),
    next_run_at: text(v.next_run_at) || undefined,
    reconciliation_required: v.reconciliation_required === true,
    error: v.error ? { code: text(fields(v.error).code) || undefined } : null,
    result: v.result ? safeTaskResult(v.result) : null,
  };
}
export function parseEvent(raw: unknown): TaskEvent {
  const v = record(raw);
  return {
    event_id: required(v.event_id),
    type: required(v.type),
    sequence: integer(v.sequence),
    occurred_at: text(v.occurred_at),
    aggregate_revision: integer(v.aggregate_revision, 0),
    payload: fields(v.payload),
  };
}
export function parseChange(raw: unknown): Change {
  const v = record(raw);
  if (typeof v.body !== "string" || typeof v.entry_type !== "string")
    throw new Error("INVALID_RESPONSE");
  return {
    change_id: required(v.change_id),
    entry_id: required(v.entry_id),
    title: required(v.title),
    body: text(v.body),
    state: required(v.state),
    operation: text(v.operation),
    entry_type: v.entry_type,
    publication_intent: text(v.publication_intent) || undefined,
    publication_eligible:
      typeof v.publication_eligible === "boolean"
        ? v.publication_eligible
        : undefined,
    base_revision: integer(v.base_revision),
    patch_hash: required(v.patch_hash),
    evidence_refs: refs(v.evidence_refs),
    risk_flags: strings(v.risk_flags),
    visibility: text(v.visibility, "unknown"),
    metadata: fields(v.metadata),
    updated_at: text(v.updated_at),
    task_id: text(v.task_id) || undefined,
    review_level: text(v.review_level) || undefined,
  };
}
export function parseIntake(raw: unknown): Intake {
  const v = record(raw);
  return {
    intake_id: required(v.intake_id),
    state: required(v.state),
    channel: text(v.channel),
    url: text(v.url) || undefined,
    title: text(v.title) || undefined,
    preview_revision:
      typeof v.preview_revision === "number"
        ? integer(v.preview_revision)
        : undefined,
    parse_status: text(v.parse_status) || undefined,
    source_id: text(v.source_id) || undefined,
    task_id: text(v.task_id) || undefined,
    expires_at: text(v.expires_at) || undefined,
    created_at: text(v.created_at),
    updated_at: text(v.updated_at),
    preview: v.preview ? fields(v.preview) : undefined,
  };
}
export function parseHealth(raw: unknown): Health {
  const v = record(raw);
  for (const field of [
    "worker_enabled",
    "channel_worker_enabled",
    "automatic_commit_enabled",
    "publication_enabled",
  ])
    if (typeof v[field] !== "boolean") throw new Error("INVALID_RESPONSE");
  return {
    status: required(v.status),
    service: required(v.service),
    worker_enabled: v.worker_enabled as boolean,
    channel_worker_enabled: v.channel_worker_enabled as boolean,
    channel_status: required(v.channel_status),
    automatic_commit_enabled: v.automatic_commit_enabled as boolean,
    publication_enabled: v.publication_enabled as boolean,
  };
}
export function parseList<T>(raw: unknown, parser: (v: unknown) => T): T[] {
  if (!Array.isArray(raw)) throw new Error("INVALID_RESPONSE");
  return raw.map(parser);
}
export interface ListPage<T> {
  items: T[];
  has_more: boolean;
}
export function parsePage<T>(
  raw: unknown,
  parser: (v: unknown) => T,
): ListPage<T> {
  const v = record(raw);
  if (typeof v.has_more !== "boolean") throw new Error("INVALID_RESPONSE");
  return { items: parseList(v.items, parser), has_more: v.has_more };
}
export interface DecisionChallenge {
  change_id: string;
  patch_hash: string;
  base_revision: number;
  policy_version: string;
  expires_at: string;
  decision_nonce: string;
}
export function parseChallenge(raw: unknown): DecisionChallenge {
  const v = record(raw);
  return {
    change_id: required(v.change_id),
    patch_hash: required(v.patch_hash),
    base_revision: integer(v.base_revision),
    policy_version: required(v.policy_version),
    expires_at: required(v.expires_at),
    decision_nonce: required(v.decision_nonce),
  };
}

function safeTaskResult(value: unknown): Metadata {
  const v = fields(value),
    result: Metadata = {};
  for (const key of ["entry_id", "change_id", "status", "stage"])
    if (typeof v[key] === "string") result[key] = v[key];
  if (
    typeof v.revision === "number" &&
    Number.isSafeInteger(v.revision) &&
    v.revision > 0
  )
    result.revision = v.revision;
  const model = fields(v.model),
    failure = fields(model.failure);
  if (Object.keys(model).length) {
    result.model = {
      status: text(model.status),
      error_code: text(model.error_code),
      failure: {
        stage: text(failure.stage),
        kind: text(failure.kind),
        event_type: text(failure.event_type),
        exit_code:
          typeof failure.exit_code === "number" ? failure.exit_code : null,
      },
    };
  }
  return result;
}
