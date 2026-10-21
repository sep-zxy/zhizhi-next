import type { DashboardView, EditorView, ItemView, ProjectView } from "./types";

const now = "2026-07-30T10:30:00+08:00";

function item(id: string, title: string, kind: "action" | "question", options: Partial<ItemView> = {}): ItemView {
  return {
    id,
    projectId: "project-continuity",
    milestoneId: "milestone-workspace",
    fingerprint: id,
    title,
    details: "明确完成条件、工程证据与后续确认边界。",
    kind,
    horizon: "daily",
    status: "open",
    blocking: false,
    priority: 12,
    activityScore: 76,
    lastEvidenceAt: now,
    manualOverride: false,
    pinned: false,
    autoDaily: false,
    sortOrder: 0,
    closedAt: null,
    revision: 18,
    evidence: [{ id: `${id}-evidence`, itemId: id, sourceRef: "fixture:workspace", summary: "已登记工程上下文", shortQuote: "", occurredAt: now, fingerprint: `${id}-evidence` }],
    suggestions: [],
    activity: { recency: 30, frequency: 24, priority: 22, total: 76 },
    ...options,
  };
}

const project: ProjectView = {
  id: "project-continuity",
  legacyId: "continuity-widget",
  name: "Continuity Widget",
  status: "active",
  summary: "把零散想法推进为目标明确、事项可追踪、工程证据可回写的成熟项目。",
  goal: "让 Todo、待确认、里程碑和工程进度在同一个项目工作区内形成闭环。",
  successCriteria: "默认工作区可用；事项和待确认清晰分流；完成事件能够幂等回写；Markdown 可以重建本地索引。",
  maturityStage: "executing",
  maturityManualOverride: false,
  projectPath: "C:\\Code\\rust\\continuity-widget",
  thresholdOverride: null,
  createdAt: now,
  updatedAt: now,
  revision: 18,
  activityScore: 81,
  progress: { state: "planned", percent: 42, completedWeight: 5, totalWeight: 12, completedMilestones: 1, totalMilestones: 3 },
  milestones: [
    { id: "milestone-workspace", projectId: "project-continuity", fingerprint: "workspace", title: "完整项目工作区", acceptanceCriteria: "默认启动完整工作区并完成 Todo、待确认和 Peek 闭环", status: "in_progress", weight: 5, manualOverride: false, sortOrder: 0, createdAt: now, updatedAt: now, completedAt: null, revision: 18 },
    { id: "milestone-events", projectId: "project-continuity", fingerprint: "events", title: "工程事件回写", acceptanceCriteria: "Skill 与 Hook 只对精确登记事项提交幂等事件", status: "planned", weight: 4, manualOverride: false, sortOrder: 1, createdAt: now, updatedAt: now, completedAt: null, revision: 18 },
    { id: "milestone-rebuild", projectId: "project-continuity", fingerprint: "rebuild", title: "Markdown 恢复链路", acceptanceCriteria: "备份旧库后可从 Vault Markdown 重建 SQLite", status: "completed", weight: 3, manualOverride: false, sortOrder: 2, createdAt: now, updatedAt: now, completedAt: now, revision: 18 },
  ],
  items: [
    item("item-main-window", "将完整项目工作区设为 EXE 默认入口", "action", { priority: 16 }),
    item("item-state", "实现筛选、看板唯一分组与草稿冲突保护", "action"),
    item("item-source", "确认 Markdown 恢复时是否保留已完成事项", "question", { blocking: true, priority: 15 }),
    item("item-done", "完成里程碑加权进度模型", "action", { status: "done", closedAt: now }),
  ],
  history: [{ id: "history-1", projectId: "project-continuity", revision: 18, changeKind: "project_manual", snapshotJson: {}, createdAt: now }],
  events: [{ source: "codex", eventId: "fixture-event", eventType: "work_item.completed", disposition: "applied", projectId: "project-continuity", itemId: "item-done", milestoneId: "milestone-rebuild", appliedRevision: 18, createdAt: now, revertedAt: null }],
};

export const fixtureEditorView: EditorView = {
  revision: 18,
  settings: { dailyThreshold: 30, nowLimit: 3, pollSeconds: 1, alwaysOnTop: true, autoSnap: true, autostart: false },
  projects: [project],
};

export const fixtureDashboardView: DashboardView = {
  revision: 18,
  settings: fixtureEditorView.settings,
  projects: [{ project, progress: project.progress, openTodoCount: 2, openQuestionCount: 1, blockingCount: 1, pendingSuggestionCount: 0, nextItems: project.items.filter((entry) => entry.status === "open") }],
  now: project.items.filter((entry) => entry.kind === "action" && entry.status === "open"),
  daily: [{ project, activityScore: 81, items: project.items.filter((entry) => entry.status === "open") }],
  weekly: [{ project, activityScore: 81, summary: project.summary }],
  candidateCount: 0,
};
