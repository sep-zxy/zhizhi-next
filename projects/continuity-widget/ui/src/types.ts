export type ProjectStatus = "candidate" | "active" | "paused" | "completed" | "archived";
export type MaturityStage = "idea" | "defined" | "executing" | "validating" | "mature";
export type ItemKind = "question" | "action";
export type ItemHorizon = "daily" | "weekly" | "later";
export type ItemStatus = "open" | "done" | "dismissed";
export type MilestoneStatus = "planned" | "in_progress" | "completed" | "cancelled";
export type SuggestionStatus = "pending" | "applied" | "rejected";
export type SuggestionKind = "item_patch" | "completion_candidate" | "target_resolution" | "milestone_patch";
export type EventDisposition = "applied" | "suggested" | "evidence_only" | "reverted";

export interface Project {
  id: string;
  legacyId: string | null;
  name: string;
  status: ProjectStatus;
  summary: string;
  goal: string;
  successCriteria: string;
  maturityStage: MaturityStage;
  maturityManualOverride: boolean;
  projectPath: string | null;
  thresholdOverride: number | null;
  createdAt: string;
  updatedAt: string;
  revision: number;
}

export interface Item {
  id: string;
  projectId: string;
  milestoneId: string | null;
  fingerprint: string;
  title: string;
  details: string;
  kind: ItemKind;
  horizon: ItemHorizon;
  status: ItemStatus;
  blocking: boolean;
  priority: number;
  activityScore: number;
  lastEvidenceAt: string | null;
  manualOverride: boolean;
  pinned: boolean;
  autoDaily: boolean;
  sortOrder: number;
  closedAt: string | null;
  revision: number;
}

export interface ProjectMilestone {
  id: string;
  projectId: string;
  fingerprint: string;
  title: string;
  acceptanceCriteria: string;
  status: MilestoneStatus;
  weight: number;
  manualOverride: boolean;
  sortOrder: number;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
  revision: number;
}

export interface ProjectProgress {
  state: "unplanned" | "planned";
  percent: number | null;
  completedWeight: number;
  totalWeight: number;
  completedMilestones: number;
  totalMilestones: number;
}

export interface Evidence {
  id: string;
  itemId: string;
  sourceRef: string;
  summary: string;
  shortQuote: string;
  occurredAt: string;
  fingerprint: string;
}

export interface Suggestion {
  id: string;
  projectId: string;
  itemId: string | null;
  milestoneId: string | null;
  kind: SuggestionKind;
  source: string;
  eventId: string | null;
  proposedJson: unknown;
  status: SuggestionStatus;
  createdAt: string;
  resolvedAt: string | null;
  revision: number;
}

export interface ActivityBreakdown { recency: number; frequency: number; priority: number; total: number }

export interface ItemView extends Item {
  evidence: Evidence[];
  suggestions: Suggestion[];
  activity: ActivityBreakdown;
}

export interface ProjectHistory {
  id: string;
  projectId: string;
  revision: number;
  changeKind: string;
  snapshotJson: unknown;
  createdAt: string;
}

export interface EventReceiptSummary {
  source: string;
  eventId: string;
  eventType: string;
  disposition: EventDisposition;
  projectId: string | null;
  itemId: string | null;
  milestoneId: string | null;
  appliedRevision: number;
  createdAt: string;
  revertedAt: string | null;
}

export interface ProjectView extends Project {
  activityScore: number;
  progress: ProjectProgress;
  milestones: ProjectMilestone[];
  items: ItemView[];
  history: ProjectHistory[];
  events: EventReceiptSummary[];
}

export interface ProjectDashboardView {
  project: Project;
  progress: ProjectProgress;
  openTodoCount: number;
  openQuestionCount: number;
  blockingCount: number;
  pendingSuggestionCount: number;
  nextItems: ItemView[];
}

export interface DailyProjectView { project: Project; activityScore: number; items: ItemView[] }
export interface WeeklyProjectView { project: Project; activityScore: number; summary: string }

export interface Settings {
  dailyThreshold: number;
  nowLimit: number;
  pollSeconds: number;
  alwaysOnTop: boolean;
  autoSnap: boolean;
  autostart: boolean;
}

export interface DashboardView {
  revision: number;
  settings: Settings;
  projects: ProjectDashboardView[];
  now: ItemView[];
  daily: DailyProjectView[];
  weekly: WeeklyProjectView[];
  candidateCount: number;
}

export interface EditorView { revision: number; settings: Settings; projects: ProjectView[] }
export interface ProjectPreview { revision: number; project: ProjectView }
export interface RevisionUpdate { revision: number; changed: boolean; dashboard: DashboardView | null }
export interface MutationResult<T> { revision: number; value: T; possibleDuplicates: string[] }
export interface SnapResult { edge: string }

declare global { interface Window { __previewProjectId?: string } }
