import {
  EMPTY_FEEDBACK,
  EMPTY_OUTPUTS,
  createEmptySession,
  type ArticleTypeId,
  type ArticleStyleId,
  type ContextItem,
  type PersistedContextRef,
  type WriterRevision,
  type WriterSession,
  type WriterSessionSnapshot,
  type WorkflowStage
} from "./types";

export const SESSION_HISTORY_LIMIT = 20;

export function contextItemsToRefs(items: ContextItem[]): PersistedContextRef[] {
  return items.map((item) => ({
    id: item.id,
    kind: item.kind,
    label: item.label,
    path: item.path,
    content: item.kind === "selection" ? item.content : undefined
  }));
}

export function normalizeWriterSession(
  targetPath: string,
  value: Partial<WriterSession> | undefined,
  inferredType: ArticleTypeId = "blog"
): WriterSession {
  const empty = createEmptySession(targetPath, inferredType);
  const outputs = { ...structuredClone(EMPTY_OUTPUTS), ...(value?.outputs || {}) };
  const normalized: WriterSession = {
    ...empty,
    ...structuredClone(value || {}),
    targetPath,
    articleType: value?.articleType || inferredType,
    articleStyle: (value?.articleStyle || value?.articleType || inferredType) as ArticleStyleId,
    stylePrompt: value?.stylePrompt || "",
    contextRefs: Array.isArray(value?.contextRefs) ? structuredClone(value.contextRefs) : [],
    outputs,
    imagePlaceholders: Array.isArray(value?.imagePlaceholders)
      ? structuredClone(value.imagePlaceholders)
      : [],
    feedback: { ...structuredClone(EMPTY_FEEDBACK), ...(value?.feedback || {}) },
    activeStage: isWorkflowStage(value?.activeStage) ? value.activeStage : latestAvailableStage(outputs),
    previewTab: value?.previewTab || "article",
    history: Array.isArray(value?.history) ? structuredClone(value.history).slice(-SESSION_HISTORY_LIMIT) : [],
    updatedAt: value?.updatedAt || new Date().toISOString()
  };

  return normalized;
}

export function createSessionSnapshot(session: WriterSession): WriterSessionSnapshot {
  return {
    articleType: session.articleType,
    articleStyle: session.articleStyle,
    stylePrompt: session.stylePrompt,
    brief: session.brief,
    targetLength: session.targetLength,
    contextRefs: structuredClone(session.contextRefs),
    originalOutline: session.originalOutline,
    outputs: structuredClone(session.outputs),
    imagePlaceholders: structuredClone(session.imagePlaceholders),
    feedback: structuredClone(session.feedback),
    activeStage: session.activeStage,
    previewTab: session.previewTab
  };
}

export function createRevision(
  session: WriterSession,
  reason: string,
  stage?: WorkflowStage
): WriterRevision {
  const createdAt = new Date().toISOString();
  return {
    id: `revision-${createdAt}-${Math.random().toString(36).slice(2, 8)}`,
    reason,
    stage,
    createdAt,
    snapshot: createSessionSnapshot(session)
  };
}

export function appendRevision(
  session: WriterSession,
  reason: string,
  stage?: WorkflowStage
): WriterSession {
  return {
    ...session,
    history: [...session.history, createRevision(session, reason, stage)].slice(-SESSION_HISTORY_LIMIT)
  };
}

export function restoreRevision(session: WriterSession, revision: WriterRevision): WriterSession {
  const withBackup = appendRevision(session, `恢复前备份：${revision.reason}`);
  return {
    ...withBackup,
    ...structuredClone(revision.snapshot),
    targetPath: session.targetPath,
    history: withBackup.history,
    updatedAt: new Date().toISOString()
  };
}

export function getStageOutput(outputs: WriterSessionSnapshot["outputs"], stage: WorkflowStage) {
  return stage === "images" ? (outputs.illustrated || outputs.images) : outputs[stage];
}

export function restoreStageRevision(
  session: WriterSession,
  revision: WriterRevision,
  stage: WorkflowStage
): WriterSession {
  const restoredValue = getStageOutput(revision.snapshot.outputs, stage);
  if (!restoredValue) return session;

  const withBackup = appendRevision(session, `恢复${stageName(stage)}前备份`, stage);
  const outputs = structuredClone(withBackup.outputs);
  let imagePlaceholders = structuredClone(withBackup.imagePlaceholders);

  if (stage === "outline") {
    outputs.outline = revision.snapshot.outputs.outline;
    outputs.draft = "";
    outputs.review = "";
    outputs.images = "";
    outputs.illustrated = "";
    imagePlaceholders = [];
  } else if (stage === "draft") {
    outputs.draft = revision.snapshot.outputs.draft;
    outputs.review = "";
    outputs.images = "";
    outputs.illustrated = "";
    imagePlaceholders = [];
  } else if (stage === "review") {
    outputs.review = revision.snapshot.outputs.review;
    outputs.images = "";
    outputs.illustrated = "";
    imagePlaceholders = [];
  } else {
    outputs.images = revision.snapshot.outputs.images;
    outputs.illustrated = revision.snapshot.outputs.illustrated;
    imagePlaceholders = structuredClone(revision.snapshot.imagePlaceholders);
  }

  return {
    ...withBackup,
    outputs,
    imagePlaceholders,
    activeStage: stage,
    previewTab: "article",
    updatedAt: new Date().toISOString()
  };
}

function isWorkflowStage(value: unknown): value is WorkflowStage {
  return value === "outline" || value === "draft" || value === "review" || value === "images";
}

function latestAvailableStage(outputs: WriterSessionSnapshot["outputs"]): WorkflowStage {
  if (outputs.images || outputs.illustrated) return "images";
  if (outputs.review) return "review";
  if (outputs.draft) return "draft";
  return "outline";
}

function stageName(stage: WorkflowStage) {
  if (stage === "outline") return "提纲";
  if (stage === "draft") return "初稿";
  if (stage === "review") return "审校稿";
  return "插图稿";
}
